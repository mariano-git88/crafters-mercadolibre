#!/usr/bin/env python3
"""
Autorizacion inicial contra MercadoLibre. Se corre UNA SOLA VEZ.

    python autorizar.py                  -> muestra el link para autorizar
    python autorizar.py "URL_PEGADA"     -> canjea el codigo y guarda los tokens

Despues de esto, todos los demas scripts renuevan el token solos y no hay
que volver a tocar nada por ~6 meses.
"""

import sys
import urllib.parse

import almacen
from meli import (Meli, MeliError, canjear_code, leer_credenciales,
                  tokens_desde_respuesta, url_de_autorizacion)


def extraer_code(pegado):
    """
    Acepta la URL entera que quedo en el navegador o solo el code pelado.
    """
    pegado = pegado.strip().strip('"').strip("'")
    if "code=" in pegado:
        query = urllib.parse.urlparse(pegado).query or pegado.split("?", 1)[-1]
        valores = urllib.parse.parse_qs(query).get("code")
        if valores:
            return valores[0]
    return pegado


def main():
    try:
        cred = leer_credenciales()
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        return 1

    # `--cuenta <nombre>` autoriza una cuenta que no sea la de siempre. Cada
    # cuenta es una fila propia en la hoja de tokens, asi que autorizar una
    # **no pisa** a las demas.
    argv = sys.argv[1:]
    cuenta = almacen.CUENTA_POR_DEFECTO
    if "--cuenta" in argv:
        i = argv.index("--cuenta")
        cuenta = argv[i + 1].strip().lower() if i + 1 < len(argv) else cuenta
        argv = argv[:i] + argv[i + 2:]

    # El code se puede pasar como argumento (comodo para correr desde el chat)
    # o de forma interactiva si el script tiene teclado disponible.
    pegado = " ".join(argv).strip()

    print(f"Los tokens se guardan en: {almacen.describir()}")
    print(f"Cuenta: {cuenta}")
    ya = almacen.cuentas_con_token()
    if ya:
        print(f"Ya autorizadas: {', '.join(ya)}")
    print()

    if not pegado:
        try:
            if almacen.leer_tokens(cuenta):
                print(f"OJO: la cuenta '{cuenta}' ya esta autorizada.")
                print("Si igual queres rehacer la autorizacion, segui los pasos.")
        except almacen.AlmacenError as e:
            print(f"AVISO: {e}\n")

        url = url_de_autorizacion(cred["app_id"], cred["redirect_uri"],
                                  state=cuenta)

        print("\n" + "=" * 78)
        print(f"PASO 1 - Abri este link en el navegador, logueado con la cuenta")
        print(f"         de MercadoLibre de **{cuenta.upper()}** (tiene que ser el")
        print("         usuario ADMIN, no un colaborador). Si ya estas logueado")
        print("         con otra cuenta, usa una ventana privada:\n")
        print(url)
        print("\n" + "=" * 78)
        print("PASO 2 - Dale 'Permitir'. El navegador te va a mandar a:\n")
        print(f"         {cred['redirect_uri']}/?code=TG-xxxxxxxx...\n")
        print("         Es NORMAL que esa pagina de error o no cargue: lo unico que")
        print("         importa es la direccion que quedo arriba en el navegador.")
        print("\nPASO 3 - Copia esa direccion COMPLETA y volve a correr:\n")
        extra = "" if cuenta == almacen.CUENTA_POR_DEFECTO else f" --cuenta {cuenta}"
        print(f'         python autorizar.py "LA_DIRECCION_QUE_COPIASTE"{extra}\n')
        print("         (el codigo dura pocos minutos, no lo dejes para despues)")
        print("=" * 78 + "\n")
        return 0

    code = extraer_code(pegado)
    print(f"\nCodigo detectado: {code[:12]}...")

    try:
        respuesta = canjear_code(cred, code)
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        print("Causas tipicas:")
        print("  - El code ya se uso o vencio -> volve a correr el script.")
        print("  - El redirect_uri de credentials.txt no es IDENTICO al que")
        print("    cargaste en el panel de la app (revisa http/https, / final).")
        print("  - Entraste con un usuario colaborador en vez del admin.")
        return 1

    # Guardamos usando el mismo formato que usa el cliente para renovar.
    datos = tokens_desde_respuesta(respuesta)
    try:
        almacen.guardar_tokens(datos, cuenta)
    except almacen.AlmacenError as e:
        print(f"\nERROR guardando los tokens: {e}")
        return 1

    print(f"\nListo. Tokens guardados en {almacen.describir()}")
    print(f"  user_id : {datos['user_id']}")
    print(f"  scopes  : {datos['scope']}")
    print(f"  vence   : en {respuesta['expires_in'] / 3600:.1f} horas (se renueva solo)")

    if "offline_access" not in datos["scope"]:
        print("\n  AVISO IMPORTANTE: no vino el scope 'offline_access'.")
        print("  Sin eso el token NO se puede renovar solo y en 6 horas deja de andar.")
        print("  Hay que habilitar offline_access en el panel de la app y reautorizar.")

    # Prueba real contra la API para confirmar que quedo todo bien.
    print("\nProbando la conexion...")
    try:
        yo = Meli(verbose=False).get("/users/me")
        print(f"  OK -> {yo['nickname']} | {yo.get('site_id')} | "
              f"reputacion: {yo.get('seller_reputation', {}).get('level_id', 'n/d')}")
    except MeliError as e:
        print(f"  Se guardaron los tokens pero la prueba fallo: {e}")
        return 1

    print("\nYa esta. Proximo paso: `python explorar.py`\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
