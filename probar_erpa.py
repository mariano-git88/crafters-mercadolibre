#!/usr/bin/env python3
"""
Prueba si un token de OTRA cuenta puede escribir en la publicidad.

    python probar_erpa.py                  -> muestra el link para autorizar
    python probar_erpa.py "URL_PEGADA"     -> canjea el codigo y prueba

**No guarda nada.** El token se usa en memoria y se descarta: hasta saber si
el workaround sirve no hay motivo para tocar la hoja `tokens_ml`, que es de
donde leen tambien los crons y el reporte de los lunes.

Que contesta:

  - Si el PUT anda, el 401 era de **propiedad**: los anunciantes son de ERPA
    y solo su cuenta los administra por API. Ahi el workaround de dos tokens
    sirve y vale construirlo.
  - Si el PUT falla igual, el 401 es de la **aplicacion**: ML pide habilitar
    la API de Advertising aparte de los scopes, y ningun token lo resuelve.
    Hay que pedirlo por soporte para la app 531270194124969.

La escritura que prueba es un **no-op**: le manda a la campana de Crafters el
estado que ya tiene (`paused`). Si el permiso existe, no cambia nada.
"""

import json
import sys

from meli import canjear_code, leer_credenciales, url_de_autorizacion

# Campana "General AON" de Crafters, que ya esta pausada.
CAMPANA = 344790967
RUTA = f"/marketplace/advertising/MLA/product_ads/campaigns/{CAMPANA}"


def extraer_code(pegado):
    import urllib.parse
    pegado = pegado.strip().strip('"').strip("'")
    if "code=" in pegado:
        query = urllib.parse.urlparse(pegado).query or pegado.split("?", 1)[-1]
        valores = urllib.parse.parse_qs(query).get("code")
        if valores:
            return valores[0]
    return pegado


def main():
    cred = leer_credenciales()

    if len(sys.argv) < 2:
        print("1) Abrí una ventana privada y entrá a MercadoLibre con la "
              "cuenta ERPA.\n")
        print("2) Pegá este link ahí y aceptá:\n")
        print("   " + url_de_autorizacion(cred["app_id"], cred["redirect_uri"],
                                          state="erpa"))
        print("\n3) Vas a terminar en crafters.com.ar con ?code=... en la "
              "barra. Copiá la URL entera y corré:\n")
        print('   python probar_erpa.py "URL_QUE_QUEDO"')
        return 0

    import requests

    print("Canjeando el código...")
    datos = canjear_code(cred, extraer_code(sys.argv[1]))
    token = datos["access_token"]
    uid = datos.get("user_id")

    yo = requests.get("https://api.mercadolibre.com/users/me",
                      headers={"Authorization": f"Bearer {token}"},
                      timeout=30).json()
    print(f"  token de {yo.get('nickname')} (user_id {uid})")
    print(f"  scopes: {datos.get('scope', '')[:200]}\n")

    print("Probando la escritura (no-op: le manda el estado que ya tiene)...")
    r = requests.put(
        "https://api.mercadolibre.com" + RUTA,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "Api-Version": "2"},
        json={"status": "paused"}, timeout=60)

    print(f"  HTTP {r.status_code}  {r.text[:300]}\n")

    if r.status_code < 300:
        print("=> ANDA. El 401 era de propiedad: los anunciantes son de ERPA "
              "y solo su cuenta los administra por API.")
        print("   El workaround de dos tokens sirve. Avisale a Claude para "
              "que lo construya.")
    elif r.status_code == 401:
        print("=> FALLA IGUAL. El problema no es de qué cuenta escribe: es de "
              "la aplicación.")
        print("   Hay que pedirle a MercadoLibre que habilite la API de "
              "Advertising para la app 531270194124969.")
        print("   Ningún token ni segunda app lo resuelve.")
    else:
        print("=> Contestó otra cosa. Copiale la respuesta a Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
