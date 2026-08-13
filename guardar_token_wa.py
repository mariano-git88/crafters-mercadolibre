#!/usr/bin/env python3
"""
Cambia el token de WhatsApp en los tres lugares donde vive, de una.

    python guardar_token_wa.py

Pide el token sin mostrarlo mientras se escribe. Si preferis pegarlo con el
bloc de notas, guardalo en `token_nuevo.txt` (al lado de este archivo) y corre
el script: lo toma de ahi y despues lo borra.

--------------------------------------------------------------------------
Por que un script para esto
--------------------------------------------------------------------------

El token esta en tres archivos —el de la terminal, el que va a Streamlit Cloud
y el que va a Render y a los GitHub Actions— y cambiarlo a mano en tres
lugares termina con dos actualizados y uno viejo. Ese tercero no se nota hasta
que un cliente escribe y nadie le contesta.

Ademas se prueba ANTES de guardar. Pisar un token que funciona con uno mal
copiado deja el asistente mudo sin que nadie se entere.

--------------------------------------------------------------------------
El token temporal dura 24 horas
--------------------------------------------------------------------------

El que da la pantalla de API Setup se vence al dia siguiente. Para produccion
hace falta uno de usuario del sistema, que no vence:

  business.facebook.com > Configuracion del negocio > Usuarios > Usuarios del
  sistema > Agregar > darle acceso a la app y a la cuenta de WhatsApp >
  Generar token > permisos `whatsapp_business_messaging` y
  `whatsapp_business_management` > caducidad "Nunca".
"""

import getpass
import shutil
import sys
import tomllib
from pathlib import Path

import whatsapp

DIR = Path(__file__).resolve().parent
PEGADO = DIR / "token_nuevo.txt"

ARCHIVOS = [
    DIR / ".streamlit" / "secrets.toml",          # la terminal
    DIR / "secrets_para_github_actions.txt",      # Render y los cron
    DIR / "secrets_para_streamlit_cloud.txt",     # la app
]


def reemplazar(archivo, token):
    """
    Cambia `token` dentro de la seccion [whatsapp], sin tocar nada mas.

    Se edita linea por linea a proposito: reescribir el archivo con una
    libreria de TOML le sacaria los comentarios y el orden, y estos archivos
    se leen a mano.
    """
    if not archivo.exists():
        return False, "no existe"

    lineas = archivo.read_text(encoding="utf-8").splitlines()
    dentro, cambiada = False, False
    for i, l in enumerate(lineas):
        if l.strip().startswith("["):
            dentro = l.strip() == "[whatsapp]"
            continue
        # `verify_token` empieza distinto: si se compara con "in" se pisa el
        # que no es.
        if dentro and l.split("=")[0].strip() == "token":
            lineas[i] = f'token           = "{token}"'
            cambiada = True
    if not cambiada:
        return False, "no encontre token dentro de [whatsapp]"

    nuevo = "\n".join(lineas) + "\n"
    try:
        datos = tomllib.loads(nuevo)
        if datos["whatsapp"]["token"] != token:
            return False, "quedo distinto de lo que se pidio"
    except Exception as e:                                  # noqa: BLE001
        return False, f"quedaria roto el TOML: {e}"

    shutil.copy2(archivo, archivo.with_suffix(archivo.suffix + ".bak"))
    archivo.write_text(nuevo, encoding="utf-8")
    return True, "actualizado"


def main():
    print("Token de WhatsApp\n")

    actual_ok, actual_detalle = whatsapp.verificar_token()
    print(f"  El que hay ahora: {'anda' if actual_ok else 'NO anda'} · "
          f"{actual_detalle}\n")

    if PEGADO.exists():
        token = PEGADO.read_text(encoding="utf-8").strip()
        print(f"  Tomando el token de {PEGADO.name}.")
    else:
        token = getpass.getpass("  Pegá el token nuevo (no se ve): ").strip()

    token = token.strip().strip('"').strip("'")
    if not token:
        print("\n  No pegaste nada. No toco nada.")
        return 1
    if token == whatsapp.config()["token"]:
        print("\n  Es el mismo que ya estaba. No toco nada.")
        PEGADO.unlink(missing_ok=True)
        return 0

    print("\n  Probándolo contra Meta antes de guardarlo…")
    ok, detalle = whatsapp.verificar_token(token)
    if not ok:
        print(f"  NO sirve: {detalle}")
        print("\n  No guardé nada: el que estaba sigue en su lugar.")
        return 1
    print(f"  Anda: {detalle}\n")

    for a in ARCHIVOS:
        hecho, det = reemplazar(a, token)
        print(f"  {'OK  ' if hecho else 'OJO '} {a.name}: {det}")

    PEGADO.unlink(missing_ok=True)
    print("\nListo. Acordate de actualizar tambien:")
    print("  · Render        > Environment > CRAFTERS_SECRETS_TOML")
    print("  · GitHub        > Settings > Secrets > CRAFTERS_SECRETS_TOML")
    print("  · Streamlit Cloud > Settings > Secrets")
    print("(pegando de nuevo el bloque entero del archivo que le corresponde "
          "a cada uno)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
