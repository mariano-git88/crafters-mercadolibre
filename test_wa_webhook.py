#!/usr/bin/env python3
"""
Lo que el webhook de WhatsApp no puede romper.

    python test_wa_webhook.py

Corre sin Meta, sin MercadoLibre y sin el modelo: no manda ningun mensaje, no
escribe en la planilla y no manda mails. Prueba el transporte, que es donde
las fallas son silenciosas — un mensaje contestado dos veces, un aviso de
"entregado" tomado como consulta, una firma que nadie verifica.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SECRETO = "secreto-de-prueba"
os.environ.update({"WA_SIMULAR": "1", "WA_SIN_MOTOR": "1"})

import ventas_wa                                            # noqa: E402
import wa_webhook                                           # noqa: E402
import whatsapp                                             # noqa: E402

# La configuracion se reemplaza entera, no con variables de entorno: los
# secrets del archivo le ganan al entorno (bien, es lo que corresponde en
# produccion), asi que apenas se cargo la seccion [whatsapp] de verdad la
# prueba empezo a firmar con un secreto y el servidor a validar con otro.
# Todo daba 403 y parecia que estaba roto el webhook.
whatsapp.config = lambda: {
    "token": "token-falso", "phone_number_id": "1296822553514864",
    "verify_token": "token-de-alta", "app_secret": SECRETO,
    "version": "v23.0"}

enviados, derivaciones, vistos_por_el_modelo = [], [], []

wa_webhook.ESPERA_AGRUPAR = 0.4
wa_webhook._enviar = lambda tel, txt: (enviados.append((tel, txt)),
                                       (True, "ok"))[1]
whatsapp.marcar_leido = lambda mid, escribiendo=False: (True, "ok")
ventas_wa.registrar = lambda *a, **k: None
ventas_wa.avisar_derivacion = lambda tel, nom, conv, sal: (
    derivaciones.append((tel, sal.get("motivo", ""))), (True, "ok"))[1]


def responder_falso(conversacion, cat, pre, ml=None, cliente=None):
    vistos_por_el_modelo.append([m["texto"] for m in conversacion
                                 if m["de"] == "cliente"])
    return {"responder": True, "respuesta": "Sí, tenemos. $1.000 cada uno.",
            "skus": [], "cantidad": 0, "accion": "ninguna",
            "confianza": "alta", "motivo": "prueba", "_productos": []}


ventas_wa.responder = responder_falso
wa_webhook.motor.listo.set()               # como si el catalogo ya estuviera

cli = wa_webhook.app.test_client()
fallas = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK  ' if condicion else 'FALLA'}  {nombre}"
          + (f"   ({detalle})" if detalle and not condicion else ""))
    if not condicion:
        fallas.append(nombre)


def postear(payload, firmar=True):
    crudo = json.dumps(payload).encode()
    cab = {}
    if firmar:
        cab["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            SECRETO.encode(), crudo, hashlib.sha256).hexdigest()
    return cli.post("/webhook", data=crudo,
                    content_type="application/json", headers=cab)


def mensaje(mid, texto="hola", tipo="text", telefono="5491133334444"):
    m = {"id": mid, "from": telefono, "timestamp": "1", "type": tipo}
    if tipo == "text":
        m["text"] = {"body": texto}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1",
            "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "contacts": [{"wa_id": telefono,
                              "profile": {"name": "Juan Prueba"}}],
                "messages": [m]}}]}]}


def main():
    print("\n1. Alta del webhook (el GET que manda Meta una sola vez)")
    r = cli.get("/webhook?hub.mode=subscribe&hub.verify_token=token-de-alta"
                "&hub.challenge=1234567")
    revisar("devuelve el challenge crudo, sin envolver",
            r.status_code == 200 and r.get_data(as_text=True) == "1234567",
            f"{r.status_code} {r.get_data(as_text=True)!r}")
    revisar("rechaza el verify_token equivocado",
            cli.get("/webhook?hub.mode=subscribe&hub.verify_token=otro"
                    "&hub.challenge=1").status_code == 403)

    print("\n2. Firma: la URL es publica, no alcanza con saberla")
    revisar("sin firma da 403", postear(mensaje("wa.x"), firmar=False)
            .status_code == 403)
    revisar("y no contesta nada", not enviados)

    print("\n3. Mensaje normal")
    r = postear(mensaje("wa.001", "tenés candados?"))
    revisar("contesta 200 al toque", r.status_code == 200)
    revisar("todavía no responde: espera por si sigue escribiendo",
            not enviados)
    time.sleep(1.2)
    revisar("después del agrupado contesta una sola vez",
            len(enviados) == 1, str(enviados))

    print("\n4. El mismo mensaje otra vez (Meta reintenta)")
    postear(mensaje("wa.001", "tenés candados?"))
    time.sleep(1.2)
    revisar("no lo contesta dos veces", len(enviados) == 1, str(len(enviados)))

    print("\n5. Tres mensajes seguidos son una consulta, no tres")
    enviados.clear(), vistos_por_el_modelo.clear()
    postear(mensaje("wa.010", "hola"))
    postear(mensaje("wa.011", "necesito burletes"))
    postear(mensaje("wa.012", "de 2 metros"))
    time.sleep(1.2)
    revisar("una sola respuesta", len(enviados) == 1, str(len(enviados)))
    revisar("y el modelo vio los tres mensajes",
            vistos_por_el_modelo and vistos_por_el_modelo[-1][-3:] ==
            ["hola", "necesito burletes", "de 2 metros"],
            str(vistos_por_el_modelo))

    print("\n6. Los avisos de estado (entregado / leído) no son consultas")
    enviados.clear()
    r = postear({"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wa.001", "status": "delivered"}]}}]}]})
    time.sleep(0.8)
    revisar("se ignoran", r.status_code == 200 and not enviados)

    print("\n7. Un audio: el asistente no adivina, deriva")
    enviados.clear(), derivaciones.clear()
    postear(mensaje("wa.020", tipo="audio", telefono="5491155556666"))
    time.sleep(1.2)
    revisar("le avisa al cliente",
            len(enviados) == 1 and "persona" in enviados[0][1])
    revisar("y manda el mail", len(derivaciones) == 1, str(derivaciones))

    print("\n8. Si falla el modelo, el cliente igual recibe algo")
    enviados.clear(), derivaciones.clear()

    def explotar(*a, **k):
        raise RuntimeError("se cayó la API")

    ventas_wa.responder = explotar
    postear(mensaje("wa.030", "hola?", telefono="5491177778888"))
    time.sleep(1.2)
    revisar("le contesta al cliente", len(enviados) == 1, str(enviados))
    revisar("y avisa por mail", len(derivaciones) == 1, str(derivaciones))
    ventas_wa.responder = responder_falso

    print("\n9. /salud dice la verdad")
    datos = cli.get("/salud").get_json()
    revisar("verifica la firma", datos.get("firma_verificada") is True)
    revisar("avisa que es el número de prueba",
            datos.get("numero_de_prueba") is True)
    revisar("cuenta los mensajes", datos.get("recibidos", 0) >= 5, str(datos))

    print("\n" + ("TODO OK" if not fallas else f"FALLARON: {fallas}"))
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
