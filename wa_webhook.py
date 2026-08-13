#!/usr/bin/env python3
"""
El servidor que recibe los mensajes de WhatsApp y los contesta.

    python wa_webhook.py              -> local, en el puerto 8000
    WA_SIMULAR=1 python wa_webhook.py -> igual pero sin mandarle nada al cliente

En produccion lo levanta gunicorn:

    gunicorn wa_webhook:app --bind 0.0.0.0:$PORT --workers 1 --threads 8

**Un solo worker.** Las conversaciones, los mensajes ya vistos y los temporizadores
viven en memoria de este proceso. Con dos workers, Meta reparte los mensajes
entre los dos y cada uno ve media conversacion.

--------------------------------------------------------------------------
Las cuatro cosas que hace, en orden
--------------------------------------------------------------------------

1. **Contesta 200 al toque.** Meta reintenta el webhook si no le contestas
   rapido, y cada reintento es el mismo mensaje otra vez. Se responde primero
   y se piensa despues, en otro hilo.

2. **Espera unos segundos antes de contestar.** El cliente escribe "hola",
   despues "tenes candados?" y despues "de 40". Son tres webhooks pero una
   sola consulta: contestar cada uno seria mandarle tres respuestas y las tres
   incompletas.

3. **Descarta lo repetido.** Cada mensaje trae un id y se contesta una sola
   vez, aunque Meta lo mande de nuevo.

4. **Si algo falla, avisa.** Si no se puede contestar (el modelo, el envio, lo
   que sea) sale un mail a clientes@crafters.com.ar. Un cliente esperando en
   silencio es peor que no haber puesto el asistente.
"""

import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

DIR = Path(__file__).resolve().parent


def _materializar_secrets():
    """
    En Render no hay `.streamlit/secrets.toml`: el bloque entero viaja en la
    variable `CRAFTERS_SECRETS_TOML`, igual que en GitHub Actions.

    Es a proposito que sea el archivo completo y no una variable por dato:
    asi la configuracion es la misma en los tres lados y no hay que ir
    copiando secretos de a uno cada vez que se agrega algo.
    """
    bloque = os.environ.get("CRAFTERS_SECRETS_TOML")
    destino = DIR / ".streamlit" / "secrets.toml"
    if bloque and not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(bloque, encoding="utf-8")
        print(f"[wa] secrets.toml escrito desde el entorno "
              f"({len(bloque)} caracteres).", flush=True)


_materializar_secrets()          # antes de importar lo que lee los secrets

from flask import Flask, request                            # noqa: E402

import ventas_wa                                            # noqa: E402
import whatsapp                                             # noqa: E402
from catalogo import CACHE as CACHE_CATALOGO                # noqa: E402
from catalogo import cargar_catalogo                        # noqa: E402
from meli import Meli                                       # noqa: E402

# Cuanto se espera despues del ultimo mensaje antes de contestar. Siete
# segundos alcanzan para que termine de escribir sin que la respuesta se
# sienta lenta.
ESPERA_AGRUPAR = 7

# Cuanto se recuerda una conversacion sin mensajes nuevos. Mas que esto y el
# contexto viejo confunde mas de lo que ayuda.
VIDA_CONVERSACION = 6 * 3600

# Cada cuanto se vuelve a bajar el catalogo (para buscar; el precio que se le
# dice al cliente se relee en vivo en cada consulta).
REFRESCO_CATALOGO = 6 * 3600

# Cuanto se le da al motor para terminar de cargar antes de rendirse.
#
# Corto a proposito. Del otro lado hay alguien mirando el celular: si el
# catalogo todavia no esta, es mejor decirselo en 20 segundos y pasarlo a una
# persona que dejarlo dos minutos y medio en visto.
ESPERA_MOTOR = 20

MAX_VISTOS = 1000

SIMULAR = bool(os.environ.get("WA_SIMULAR"))

SIN_TEXTO = ("Recibí tu mensaje. Como no es texto, lo va a ver una persona "
             "del equipo y te contesta a la brevedad. Si querés algo ya, "
             "escribime qué producto buscás y te paso precio y "
             "disponibilidad.")

NO_PUDE = ("Perdón, tuve un problema para responderte. Ya avisé al equipo y "
           "una persona te contesta en un rato.")

ARRANCANDO = ("¡Hola! Te leí. Estoy terminando de arrancar y todavía no puedo "
              "consultar precios. Ya le pasé tu mensaje a una persona del "
              "equipo, que te contesta enseguida.")


# -------------------------------------------------------------------- motor

class Motor:
    """
    El catalogo y las reglas de precio, cargados una sola vez.

    Bajar el catalogo son un par de minutos: hacerlo por mensaje seria
    impagable en tiempo. Se carga al arrancar, en un hilo aparte para que el
    servicio conteste el health check mientras tanto, y se refresca cada
    tantas horas.
    """

    def __init__(self):
        self.ml = None
        self.cat = None
        self.pre = None
        self.listo = threading.Event()
        self.error = ""
        self.cargado_en = 0
        self.etapa = "sin arrancar"

    def cargar(self, refrescar=False):
        try:
            # `verbose=True` a proposito: es lo que hace que los avisos de rate
            # limit de ML salgan en el log de Render. Con verbose=False una
            # bajada frenada por 429 se ve igual que un servicio colgado.
            self.etapa = "leyendo los tokens"
            ml = Meli(verbose=True)
            self.etapa = ("bajando el catálogo de MercadoLibre"
                          if refrescar or not CACHE_CATALOGO.exists()
                          else "leyendo el catálogo del disco")
            pubs = cargar_catalogo(ml, refrescar=refrescar)
            self.etapa = "armando el buscador"
            cat = ventas_wa.Catalogo(pubs)
            self.etapa = "leyendo las reglas de precio mayorista"
            pre = ventas_wa.Precios(pubs)
            self.etapa = "listo"
            # Recien aca se reemplaza lo que habia: mientras baja el catalogo
            # nuevo, las consultas siguen contestandose con el anterior.
            self.ml, self.cat, self.pre = ml, cat, pre
            self.cargado_en = time.time()
            self.error = ""
            self.listo.set()
            print(f"[wa] catalogo listo: {len(cat.items)} productos · "
                  f"{len(pre.regs)} reglas mayoristas", flush=True)
        except Exception as e:                              # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.etapa = f"falló {self.etapa}"
            print(f"[wa] ERROR cargando el catalogo: {self.error}", flush=True)
            traceback.print_exc()

    def arrancar(self):
        def ciclo():
            self.cargar()
            while True:
                time.sleep(REFRESCO_CATALOGO)
                self.cargar(refrescar=True)
        threading.Thread(target=ciclo, daemon=True, name="catalogo").start()

    def esperar(self, segundos=ESPERA_MOTOR):
        return self.listo.wait(timeout=segundos)


motor = Motor()


# ------------------------------------------------------------------- estado

_lock = threading.Lock()
_conversaciones = {}          # telefono -> {mensajes, nombre, ultimo, ...}
_vistos = deque(maxlen=MAX_VISTOS)
_timers = {}                  # telefono -> threading.Timer
_contadores = {"recibidos": 0, "contestados": 0, "derivados": 0, "fallados": 0}


def _limpiar_viejas():
    corte = time.time() - VIDA_CONVERSACION
    for tel in [t for t, c in _conversaciones.items() if c["ultimo"] < corte]:
        _conversaciones.pop(tel, None)


def _encolar(m):
    """Guarda el mensaje y (re)arma el temporizador para contestar."""
    with _lock:
        if not m["id"] or m["id"] in _vistos:
            return                                  # repetido: ya se atendio
        _vistos.append(m["id"])
        _limpiar_viejas()
        _contadores["recibidos"] += 1

        conv = _conversaciones.setdefault(m["telefono"], {
            "mensajes": [], "nombre": "", "ultimo": 0, "sin_texto": False})
        conv["nombre"] = m["nombre"] or conv["nombre"]
        conv["ultimo"] = time.time()
        if m["texto"]:
            conv["mensajes"].append({"de": "cliente", "texto": m["texto"]})
        else:
            conv["sin_texto"] = True

        anterior = _timers.pop(m["telefono"], None)
        if anterior:
            anterior.cancel()
        t = threading.Timer(ESPERA_AGRUPAR, _procesar, args=(m["telefono"],))
        t.daemon = True
        _timers[m["telefono"]] = t
        t.start()

    whatsapp.marcar_leido(m["id"], escribiendo=True)


# --------------------------------------------------------------- respuesta

def _enviar(telefono, texto):
    if SIMULAR:
        print(f"[wa] (simulado) a {telefono}: {texto}", flush=True)
        return True, "simulado"
    return whatsapp.enviar_texto(telefono, texto)


def _derivar(telefono, nombre, mensajes, salida, texto_al_cliente=""):
    """Avisa por mail y, si hace falta, le dice algo al cliente."""
    _contadores["derivados"] += 1
    if texto_al_cliente:
        _enviar(telefono, texto_al_cliente)
    try:
        ok, detalle = ventas_wa.avisar_derivacion(telefono, nombre, mensajes,
                                                  salida)
        if not ok:
            print(f"[wa] AVISO: no pude mandar el mail de derivacion: "
                  f"{detalle}", flush=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"[wa] AVISO: fallo el mail de derivacion: {e}", flush=True)


def _link_de_pago(telefono, salida):
    """
    Arma el link con los productos que el asistente identifico.

    Devuelve (link, motivo_si_no_salio). El precio no se recalcula a ojo: sale
    del mismo tramo que se le dijo al cliente.
    """
    prods = {p["sku"]: p for p in (salida.get("_productos") or [])}
    cant = int(salida.get("cantidad") or 0) or 1
    items = []
    for sku in salida.get("skus") or []:
        p = prods.get(str(sku).strip().upper())
        if not p:
            continue
        unitario, _ = motor.pre.para(p["sku"], cant)
        items.append({"titulo": p["titulo"],
                      "precio": unitario or p["precio"], "cantidad": cant})
    if not items:
        return "", "no pude identificar el producto para cobrarlo"
    try:
        return ventas_wa.link_de_pago(motor.ml, items,
                                      referencia=f"wa-{telefono}"), ""
    except Exception as e:                                  # noqa: BLE001
        return "", str(e)[:200]


def _procesar(telefono):
    """Piensa y contesta. Corre en su propio hilo, nunca en el del webhook."""
    with _lock:
        _timers.pop(telefono, None)
        conv = _conversaciones.get(telefono)
        if not conv:
            return
        mensajes = list(conv["mensajes"])
        nombre = conv["nombre"]
        sin_texto = conv["sin_texto"]
        conv["sin_texto"] = False

    try:
        # Audio, foto o ubicacion: el asistente no los entiende y no vale la
        # pena que adivine. Va a una persona.
        if sin_texto and not mensajes:
            _derivar(telefono, nombre, [{"de": "cliente",
                                         "texto": "(mensaje de audio o imagen)"}],
                     {"motivo": "el cliente mandó un mensaje que no es texto"},
                     texto_al_cliente=SIN_TEXTO)
            return

        if not mensajes:
            return

        if not motor.esperar():
            _derivar(telefono, nombre, mensajes,
                     {"motivo": f"el asistente estaba {motor.etapa}"
                                + (f" · {motor.error}" if motor.error else "")},
                     texto_al_cliente=ARRANCANDO)
            _contadores["fallados"] += 1
            return

        salida = ventas_wa.responder(mensajes, motor.cat, motor.pre,
                                     ml=motor.ml)
        texto = (salida.get("respuesta") or "").strip()
        accion = salida.get("accion") or "ninguna"
        link = ""

        if accion == "link_de_pago":
            link, motivo = _link_de_pago(telefono, salida)
            if link:
                texto += f"\n\nAcá lo tenés para pagar: {link}"
            else:
                # No se le promete un link que no existe: se le avisa que va a
                # llegar y se pone a una persona atras.
                print(f"[wa] no pude armar el link: {motivo}", flush=True)
                texto += ("\n\nEn un momento te paso el link de pago por acá.")
                accion = "derivar"
                salida["motivo"] = (f"{salida.get('motivo', '')} · el link de "
                                    f"pago no salió: {motivo}")
        elif accion == "link_a_la_tienda" and ventas_wa.TIENDA not in texto:
            texto += f"\n\nAcá está la tienda: {ventas_wa.TIENDA}"

        if texto:
            ok, detalle = _enviar(telefono, texto)
            if ok:
                _contadores["contestados"] += 1
                with _lock:
                    c = _conversaciones.get(telefono)
                    if c:
                        c["mensajes"].append({"de": "nosotros", "texto": texto})
            else:
                # No llego. Que lo agarre una persona: para el cliente, un
                # mensaje que no sale es lo mismo que no haber escrito.
                _contadores["fallados"] += 1
                print(f"[wa] no pude enviar a {telefono}: {detalle}", flush=True)
                salida["motivo"] = (f"{salida.get('motivo', '')} · NO se pudo "
                                    f"enviar la respuesta: {detalle}")
                accion = "derivar"

        if accion == "derivar" or not salida.get("responder"):
            _derivar(telefono, nombre, mensajes, salida)

        ultimo_del_cliente = next(
            (m["texto"] for m in reversed(mensajes) if m["de"] == "cliente"), "")
        ventas_wa.registrar(telefono, nombre, ultimo_del_cliente, salida, link)

    except Exception as e:                                  # noqa: BLE001
        _contadores["fallados"] += 1
        print(f"[wa] ERROR atendiendo a {telefono}: {e}", flush=True)
        traceback.print_exc()
        _derivar(telefono, nombre, mensajes or [{"de": "cliente", "texto": ""}],
                 {"motivo": f"falló el asistente: {type(e).__name__}: {e}"},
                 texto_al_cliente=NO_PUDE)


# ------------------------------------------------------------------- rutas

app = Flask(__name__)


@app.get("/")
def raiz():
    return "Asistente de WhatsApp de CRAFTERS. El webhook es /webhook.", 200


@app.get("/salud")
def salud():
    cfg = whatsapp.config()
    return {
        "catalogo_listo": motor.listo.is_set(),
        # En que anda mientras no esta listo. Sin esto, "todavia no" y "se
        # colgo" se ven exactamente igual desde afuera.
        "etapa": motor.etapa,
        "catalogo_en_disco": CACHE_CATALOGO.exists(),
        "productos": len(motor.cat.items) if motor.cat else 0,
        "catalogo_cargado_hace_min": (
            round((time.time() - motor.cargado_en) / 60) if motor.cargado_en
            else None),
        "error_catalogo": motor.error,
        "whatsapp_configurado": whatsapp.activo(),
        "firma_verificada": bool(cfg["app_secret"]),
        "numero_de_prueba": whatsapp.numero_de_prueba(),
        "simulando": SIMULAR,
        "conversaciones_abiertas": len(_conversaciones),
        **_contadores,
    }, 200


@app.get("/webhook")
def verificar():
    codigo, cuerpo = whatsapp.verificacion(request.args)
    return cuerpo, codigo, {"Content-Type": "text/plain"}


@app.post("/webhook")
def recibir():
    crudo = request.get_data()
    if not whatsapp.firma_valida(crudo, request.headers.get("X-Hub-Signature-256")):
        print("[wa] rechazado: firma invalida", flush=True)
        return "", 403

    payload = request.get_json(silent=True) or {}
    try:
        for m in whatsapp.mensajes_del_payload(payload):
            _encolar(m)
    except Exception as e:                                  # noqa: BLE001
        # Aun con un payload raro se contesta 200: si devolvemos error, Meta
        # lo reintenta en loop y despues deshabilita el webhook.
        print(f"[wa] ERROR leyendo el webhook: {e}", flush=True)
        traceback.print_exc()
    return "", 200


# ------------------------------------------------------------------ arranque

# `WA_SIN_MOTOR` deja levantar el servidor sin bajar el catalogo: es lo que
# usan las pruebas del transporte, que no necesitan ni MercadoLibre ni el
# modelo.
if not os.environ.get("WA_SIN_MOTOR"):
    motor.arrancar()


def main():
    print("Asistente de WhatsApp de CRAFTERS")
    print(whatsapp.describir())
    if SIMULAR:
        print("\n  MODO SIMULACION: no se le manda nada a ningun cliente.")
    if not whatsapp.config()["app_secret"]:
        print("\n  OJO: sin app_secret no se verifica que el webhook venga de "
              "Meta.")
    puerto = int(os.environ.get("PORT") or 8000)
    print(f"\nEscuchando en http://localhost:{puerto}/webhook")
    print("Para que Meta lo alcance desde afuera hace falta una URL publica "
          "(Render, o\n`cloudflared tunnel --url http://localhost:"
          f"{puerto}` para probar).\n")
    app.run(host="0.0.0.0", port=puerto, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
