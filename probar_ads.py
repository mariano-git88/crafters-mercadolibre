#!/usr/bin/env python3
"""
Prueba si SE PUEDE ESCRIBIR en la publicidad, con cualquier app y cualquier
cuenta.

    python probar_ads.py                       -> prueba con el token guardado
    python probar_ads.py --link                -> link para autorizar otra cuenta
    python probar_ads.py "URL_PEGADA"          -> canjea el code y prueba
    python probar_ads.py --link --app ID:SECRET:REDIRECT
    python probar_ads.py "URL_PEGADA" --app ID:SECRET:REDIRECT

Sirve para las tres cosas que hay para intentar **sin pasar por soporte de
MercadoLibre**:

  1. Reprobar con otra cuenta (ERPA) viendo los scopes **enteros**. La primera
     vez salieron cortados en 200 caracteres y quedo la duda de si el token
     tenia el de publicidad, o sea de si la prueba valia.
  2. Probar una **app nueva** creada en el devcenter con todos los scopes de
     publicidad tildados, sin tocar credentials.txt ni la app que hoy anda.
  3. Confirmar que la app actual sigue sin poder.

**No guarda ningun token.** Se usan en memoria y se descartan: la hoja
`tokens_ml` es de donde leen tambien los crons y el reporte de los lunes, y no
hay motivo para tocarla hasta saber si algo de esto funciona.

Las dos escrituras que prueba son **no-op**: le mandan a la campana y al
anuncio el estado que ya tienen. Si el permiso existe, no cambian nada.
"""

import sys
import urllib.parse

import requests

from meli import canjear_code, leer_credenciales, url_de_autorizacion

BASE = "https://api.mercadolibre.com"

# Campana "General AON" de Crafters, que ya esta pausada, y un anuncio activo.
CAMPANA = 344790967
ANUNCIO = "MLA1546165612"

PRUEBAS = [
    ("campaña", f"/marketplace/advertising/MLA/product_ads/campaigns/{CAMPANA}",
     {"status": "paused"}),
    ("anuncio", f"/marketplace/advertising/MLA/product_ads/ads/{ANUNCIO}",
     {"status": "active"}),
]


def credenciales(argv):
    """--app ID:SECRET:REDIRECT para probar otra app sin tocar el archivo."""
    if "--app" in argv:
        crudo = argv[argv.index("--app") + 1]
        partes = crudo.split(":", 2)
        if len(partes) != 3:
            raise SystemExit("Formato: --app APPID:SECRET:https://redirect")
        return {"app_id": partes[0], "secret_key": partes[1],
                "redirect_uri": partes[2]}
    return leer_credenciales()


def extraer_code(pegado):
    pegado = pegado.strip().strip('"').strip("'")
    if "code=" in pegado:
        query = urllib.parse.urlparse(pegado).query or pegado.split("?", 1)[-1]
        valores = urllib.parse.parse_qs(query).get("code")
        if valores:
            return valores[0]
    return pegado


def probar(token, etiqueta):
    yo = requests.get(f"{BASE}/users/me",
                      headers={"Authorization": f"Bearer {token}"},
                      timeout=30).json()
    print(f"\n=== {etiqueta}: {yo.get('nickname')} (user_id {yo.get('id')})")

    codigos = []
    for nombre, ruta, cuerpo in PRUEBAS:
        r = requests.put(
            BASE + ruta,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json", "Api-Version": "2"},
            json=cuerpo, timeout=60)
        print(f"  {nombre:<8} HTTP {r.status_code}  "
              f"{r.text[:200] if r.text else '(cuerpo vacío)'}")
        if r.status_code == 503:
            # El 503 viene sin cuerpo; el request-id es lo unico con lo que
            # ML puede rastrearlo si hay que reclamar.
            print(f"           X-Request-Id: "
                  f"{r.headers.get('X-Request-Id', '-')}")
        codigos.append(r.status_code)

    if any(c < 300 for c in codigos):
        print("\n  => ANDA. Encontramos por dónde: avisale a Claude.")
    elif 401 in codigos:
        print("\n  => Sigue sin permiso de escritura.")
    else:
        print("\n  => Ni 401 ni éxito. Copiale la respuesta a Claude.")
    return codigos


def main():
    argv = sys.argv[1:]
    cred = credenciales(argv)
    pegado = next((a for a in argv
                   if "code=" in a or a.startswith("TG-")), None)

    if "--link" in argv:
        print("Abrí una ventana privada, entrá a MercadoLibre con la cuenta "
              "que quieras probar y pegá este link:\n")
        print("   " + url_de_autorizacion(cred["app_id"],
                                          cred["redirect_uri"], state="ads"))
        print("\nDespués copiá la URL donde terminás y corré:\n")
        extra = " --app ..." if "--app" in argv else ""
        print(f'   python probar_ads.py "URL_QUE_QUEDO"{extra}')
        return 0

    if pegado:
        print("Canjeando el código...")
        datos = canjear_code(cred, extraer_code(pegado))
        scopes = datos.get("scope", "")
        print(f"  scopes ({len(scopes.split())}): {scopes}")
        print("  ¿tiene alguno de publicidad?: "
              + ("SÍ" if "ads" in scopes else "NO — la prueba NO vale"))
        probar(datos["access_token"], f"app {cred['app_id']}")
        return 0

    from meli import Meli
    probar(Meli(verbose=False).token, "token guardado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
