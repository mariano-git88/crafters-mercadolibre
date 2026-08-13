#!/usr/bin/env python3
"""
La capa que habla con Meta: recibir mensajes de WhatsApp y contestarlos.

    python whatsapp.py                      -> como esta configurado
    python whatsapp.py --a 5491122334455 --texto "hola"

Esto NO decide que contestar: de eso se ocupa `ventas_wa.py`. Aca solo esta
el transporte, separado a proposito, para poder probar que el numero manda y
recibe antes de meter al modelo en el medio.

--------------------------------------------------------------------------
Configuracion (seccion [whatsapp] en los secrets)
--------------------------------------------------------------------------

    [whatsapp]
    token           = "EAA..."        # de la app de Meta
    phone_number_id = "1296822553514864"
    verify_token    = "lo-que-vos-quieras"   # lo inventas y lo repetis en Meta
    app_secret      = "..."           # Configuracion > Basica, en la app
    version         = "v23.0"         # opcional

Tambien se leen de variables de entorno (`WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET`,
etc.), que es lo comodo en Render.

--------------------------------------------------------------------------
Dos cosas que se pagan caras si no se saben
--------------------------------------------------------------------------

1. **Se contesta al numero EXACTO que manda Meta.** En Argentina el numero
   viaja a veces con el 9 (549...) y a veces sin el, y no siempre coincide con
   el que uno tiene agendado. El `from` del webhook es el unico que seguro
   funciona: no se normaliza, no se "arregla", se usa tal cual vino.

2. **Un HTTP 200 de Meta no siempre es un mensaje enviado.** La respuesta
   buena trae `messages[0].id` (un `wamid...`). Si no viene ese id, el mensaje
   no salio aunque el codigo sea 200, asi que `enviar_texto` lo exige.
"""

import hashlib
import hmac
import json
import os
import sys

import requests

import almacen

VERSION_POR_DEFECTO = "v23.0"
BASE = "https://graph.facebook.com"

# WhatsApp corta los mensajes de texto en 4096 caracteres.
LARGO_MAXIMO = 4096

# Que significan los errores que devuelve Meta. Sin esto, un 400 con
# "(#131030)" adentro no le dice nada a nadie.
ERRORES = {
    131030: ("El numero del cliente no esta en la lista de permitidos. Pasa "
             "con el numero de PRUEBA de Meta: solo se le puede escribir a "
             "los hasta 5 numeros cargados a mano en la app."),
    131047: ("Pasaron mas de 24 horas desde el ultimo mensaje del cliente. "
             "Fuera de esa ventana Meta solo deja mandar plantillas "
             "aprobadas, no texto libre."),
    131026: "El numero no tiene WhatsApp o no puede recibir mensajes.",
    133010: "El numero de la empresa no esta registrado en la Cloud API.",
    190: "El token se vencio o lo revocaron. Hay que generar uno nuevo.",
    130429: "Se paso el limite de mensajes por segundo. Reintentar mas tarde.",
    80007: "Se paso el limite de la cuenta. Reintentar mas tarde.",
}


class WhatsAppError(RuntimeError):
    pass


# ------------------------------------------------------------------- config

def _valor(nombre):
    """
    Un dato de configuracion, de la seccion [whatsapp] o del entorno.

    El entorno gana solo si en los secrets no hay nada: asi el mismo codigo
    corre local con `secrets.toml` y en Render con variables, sin tocar nada.
    """
    try:
        seccion = almacen._seccion("whatsapp") or {}
    except Exception:                                       # noqa: BLE001
        seccion = {}
    v = str(seccion.get(nombre) or "").strip()
    if v:
        return v
    return str(os.environ.get(f"WHATSAPP_{nombre.upper()}") or "").strip()


def config():
    return {
        "token": _valor("token"),
        "phone_number_id": _valor("phone_number_id"),
        "verify_token": _valor("verify_token"),
        "app_secret": _valor("app_secret"),
        "version": _valor("version") or VERSION_POR_DEFECTO,
    }


def activo():
    """Si hay lo minimo para mandar un mensaje."""
    cfg = config()
    return bool(cfg["token"] and cfg["phone_number_id"])


def describir():
    """Que hay configurado, sin mostrar ningun valor secreto."""
    cfg = config()
    lineas = []
    for clave in ["token", "phone_number_id", "verify_token", "app_secret"]:
        v = cfg[clave]
        # El unico que se muestra entero es el phone_number_id, que no es
        # secreto. Del resto solo si esta y cuanto mide: alcanza para darse
        # cuenta de un typo y no deja el valor pegado en la pantalla.
        if clave == "phone_number_id":
            estado = v or "FALTA"
        else:
            estado = f"cargado ({len(v)} caracteres)" if v else "FALTA"
        lineas.append(f"  {clave:<16} {estado}")
    lineas.append(f"  {'version':<16} {cfg['version']}")
    return "\n".join(lineas)


# ------------------------------------------------------------------- firma

def firma_valida(cuerpo, cabecera):
    """
    Si el POST lo mando Meta de verdad.

    La URL del webhook es publica: sin esta verificacion, cualquiera que la
    descubra puede hacer que el asistente conteste (y que cada mensaje falso
    cueste una llamada al modelo).

    Falla cerrado cuando hay `app_secret` configurado. Si NO lo hay, deja
    pasar y avisa: es lo unico que permite probar el circuito antes de tener
    todo cargado, pero no es como se deja en produccion.
    """
    secreto = config()["app_secret"]
    if not secreto:
        print("[wa] AVISO: sin app_secret, no verifico la firma de Meta.")
        return True
    if not cabecera or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(secreto.encode("utf-8"), cuerpo,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera.split("=", 1)[1].strip())


def verificacion(args):
    """
    El GET que Meta manda una sola vez al dar de alta el webhook.

    Devuelve (codigo_http, cuerpo). Meta espera el challenge crudo, sin
    comillas ni JSON: si se lo devolves envuelto, la verificacion falla sin
    decir por que.
    """
    cfg = config()
    esperado = cfg["verify_token"]
    if not esperado:
        return 500, "falta verify_token en la configuracion"
    if (args.get("hub.mode") == "subscribe"
            and args.get("hub.verify_token") == esperado):
        return 200, str(args.get("hub.challenge") or "")
    return 403, "no coincide el verify_token"


# ------------------------------------------------------------------ entrada

def mensajes_del_payload(payload):
    """
    Los mensajes de clientes que trae un webhook, ya masticados.

    Devuelve [{id, telefono, nombre, tipo, texto}]. Se ignoran los avisos de
    estado (enviado / entregado / leido): llegan por el mismo webhook y muchas
    mas veces que los mensajes, y tratarlos como mensajes haria que el
    asistente se conteste a si mismo.
    """
    salida = []
    for entrada in payload.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            if not valor.get("messages"):
                continue
            nombres = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                       for c in (valor.get("contacts") or [])}
            for m in valor["messages"]:
                tipo = m.get("type") or ""
                telefono = m.get("from") or ""
                salida.append({
                    "id": m.get("id") or "",
                    "telefono": telefono,
                    "nombre": nombres.get(telefono, ""),
                    "tipo": tipo,
                    "texto": _texto_de(m),
                })
    return salida


def _texto_de(m):
    """
    El texto del mensaje, si es que tiene.

    Un audio, una foto o una ubicacion no tienen texto: devuelven "" y el que
    llama decide (nosotros derivamos a una persona). Los botones y las listas
    si traen texto y se tratan como si el cliente lo hubiera escrito.
    """
    tipo = m.get("type")
    if tipo == "text":
        return (m.get("text") or {}).get("body", "")
    if tipo == "button":
        return (m.get("button") or {}).get("text", "")
    if tipo == "interactive":
        i = m.get("interactive") or {}
        for clave in ("button_reply", "list_reply"):
            if i.get(clave):
                return i[clave].get("title", "")
    if tipo in ("image", "video", "document", "audio"):
        return (m.get(tipo) or {}).get("caption", "") or ""
    return ""


# ------------------------------------------------------------------- salida

def _pedir(cuerpo):
    """POST al endpoint de mensajes. Devuelve (ok, detalle)."""
    cfg = config()
    if not cfg["token"] or not cfg["phone_number_id"]:
        return False, "WhatsApp no esta configurado (falta token o phone_number_id)"
    url = f"{BASE}/{cfg['version']}/{cfg['phone_number_id']}/messages"
    try:
        r = requests.post(url, headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
        }, json=cuerpo, timeout=30)
    except requests.RequestException as e:
        return False, f"no pude llegar a Meta: {e}"

    try:
        datos = r.json()
    except ValueError:
        datos = {}

    if r.status_code >= 300 or datos.get("error"):
        err = datos.get("error") or {}
        codigo = err.get("code")
        detalle = ERRORES.get(codigo, err.get("message") or r.text[:300])
        return False, f"HTTP {r.status_code} · codigo {codigo}: {detalle}"

    # Un 200 sin wamid no es un mensaje enviado.
    wamid = ((datos.get("messages") or [{}])[0]).get("id")
    if not wamid:
        return False, f"Meta contesto 200 pero sin id de mensaje: {str(datos)[:200]}"
    return True, wamid


def enviar_texto(telefono, texto, previsualizar_links=True):
    """
    Manda un mensaje de texto. Devuelve (ok, wamid_o_error).

    `telefono` tiene que ser el `from` que vino en el webhook, tal cual.
    """
    texto = (texto or "").strip()
    if not texto:
        return False, "mensaje vacio"
    if len(texto) > LARGO_MAXIMO:
        texto = texto[:LARGO_MAXIMO - 1] + "…"
    return _pedir({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(telefono),
        "type": "text",
        "text": {"preview_url": bool(previsualizar_links), "body": texto},
    })


def marcar_leido(message_id, escribiendo=False):
    """
    Le pone el visto al mensaje del cliente. Es cosmetico y nunca corta la
    atencion: si falla, se sigue igual.

    Con `escribiendo=True` ademas muestra el "escribiendo…", que se apaga solo
    a los 25 segundos o cuando le contestamos.
    """
    cuerpo = {"messaging_product": "whatsapp", "status": "read",
              "message_id": message_id}
    if escribiendo:
        cuerpo["typing_indicator"] = {"type": "text"}
    try:
        return _pedir(cuerpo)
    except Exception as e:                                  # noqa: BLE001
        return False, str(e)


def verificar_token(token=None):
    """
    Si el token sirve para operar este numero. Devuelve (ok, detalle).

    Es una lectura, no manda ningun mensaje. Sirve para darse cuenta de que el
    token se vencio ANTES de que un cliente escriba y no reciba respuesta: los
    temporales de Meta duran 24 horas y se caen sin avisar.
    """
    cfg = config()
    token = token or cfg["token"]
    if not token or not cfg["phone_number_id"]:
        return False, "falta el token o el phone_number_id"
    try:
        r = requests.get(
            f"{BASE}/{cfg['version']}/{cfg['phone_number_id']}",
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except requests.RequestException as e:
        return False, f"no pude llegar a Meta: {e}"
    datos = r.json() if r.content else {}
    if r.status_code >= 300 or datos.get("error"):
        err = datos.get("error") or {}
        codigo = err.get("code")
        return False, (f"codigo {codigo}: "
                       f"{ERRORES.get(codigo, err.get('message') or r.text[:200])}")
    return True, (f"{datos.get('verified_name', '')} · "
                  f"{datos.get('display_phone_number', '')} · calidad "
                  f"{datos.get('quality_rating', '?')}")


def numero_de_prueba():
    """
    Si el phone_number_id es el del numero de prueba que da Meta.

    Importa porque con ese numero solo se le puede escribir a los hasta 5
    destinatarios cargados a mano en la app: el asistente puede quedar
    perfecto y no contestarle a nadie.
    """
    return config()["phone_number_id"] == "1296822553514864"


# ------------------------------------------------------------------- prueba

def main():
    args = sys.argv[1:]

    def opcion(nombre):
        if nombre in args:
            i = args.index(nombre)
            if i + 1 < len(args):
                return args[i + 1]
        return None

    print("Configuracion de WhatsApp:")
    print(describir())
    if numero_de_prueba():
        print("\n  OJO: es el numero de PRUEBA de Meta. Solo le escribe a los "
              "destinatarios\n  cargados a mano en la app (hasta 5).")

    destino = opcion("--a")
    if not destino:
        print("\nPara probar el envio:\n"
              '  python whatsapp.py --a 5491122334455 --texto "hola"')
        return 0

    texto = opcion("--texto") or "Prueba del asistente de CRAFTERS."
    ok, detalle = enviar_texto(destino, texto)
    print(f"\n{'OK' if ok else 'FALLO'} · {detalle}")
    if not ok:
        print("\nSi dice que el numero no esta en la lista de permitidos, "
              "cargalo en\nla app de Meta (WhatsApp > Configuracion de la API "
              "> destinatarios).\nSi el numero es argentino, probar tambien "
              "con y sin el 9 despues del 54.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
