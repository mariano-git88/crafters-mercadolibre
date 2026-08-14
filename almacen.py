#!/usr/bin/env python3
"""
Donde se guardan los tokens de MercadoLibre y el registro de auditoria.

El problema que resuelve: en Streamlit Cloud el disco es efimero. Como el
refresh_token de ML es de un solo uso y rota en cada renovacion, si se pierde
el archivo hay que reautorizar a mano desde el navegador. Lo mismo con la
auditoria: es el unico registro de quien cambio que precio.

Por eso, si hay una Google Sheet configurada, los dos van ahi. Si no, caen a
archivos locales y todo sigue funcionando igual (uso desde la terminal).

Configuracion (en .streamlit/secrets.toml o en los secrets de Streamlit Cloud):

    [gsheets]
    spreadsheet_id = "1AbC..."
    # en local:
    service_account_json_path = ".gsheets/sa.json"
    # en la nube, pegar el JSON entero:
    # [gsheets.service_account]
    # type = "service_account"
    # ...
"""

import json
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
TOKENS_LOCAL = DIR / "tokens.json"
AUDITORIA_LOCAL = DIR / "auditoria.csv"
SECRETS_LOCAL = DIR / ".streamlit" / "secrets.toml"

HOJA_TOKENS = "tokens_ml"
HOJA_AUDITORIA = "auditoria"

# `cuenta` va **al final a proposito**: agregarla adelante correria todas las
# columnas y la fila que ya esta guardada quedaria leida al reves.
CUENTA_POR_DEFECTO = "crafters"

COLUMNAS_TOKENS = ["access_token", "refresh_token", "user_id", "scope",
                   "expira_en", "renovado", "cuenta"]
COLUMNAS_AUDITORIA = ["fecha", "item_id", "campo", "valor_anterior",
                      "valor_nuevo", "resultado", "operador", "nota"]


class AlmacenError(RuntimeError):
    pass


# ------------------------------------------------------------------ reintentos
#
# Google Sheets se cae por momentos. Un 503 justo cuando arrancaba el
# respondedor de preguntas dejo una corrida en rojo (3/8/2026) sin que hubiera
# nada mal configurado: la corrida anterior y la siguiente anduvieron bien.
#
# Lo importante es NO reintentar los errores de configuracion (403 sin
# permiso, 404 ID inexistente): esos no se arreglan solos y reintentarlos solo
# hace esperar al pedo antes de dar el mismo error.
#
# Ojo con que se reintenta: repetir una lectura es gratis, pero repetir un
# append puede duplicar filas, porque un 503 no dice si la escritura llego o
# no. Por eso los append-only de aca abajo NO pasan por esto.

CODIGOS_TRANSITORIOS = {429, 500, 502, 503, 504}
INTENTOS = 4
ESPERA_BASE = 2.0        # espera 2, 4 y 8 segundos: 14 en total

# Cuanto se espera a Google antes de dar la llamada por perdida.
#
# **Sin esto gspread espera para siempre.** En un script de terminal se nota
# (uno ve que no vuelve y corta); en un servicio que corre solo, una conexion
# que queda colgada bloquea el hilo sin error, sin log y sin fin. Con timeout,
# la llamada falla, `_es_transitorio` la reconoce como corte de red y
# `_reintentar` la vuelve a intentar.
TIMEOUT_SHEETS = 30


def _es_transitorio(e):
    """True si conviene reintentar: Google hipo, no configuracion mal puesta."""
    # gspread expone el codigo en la APIError. Preferimos el status HTTP real
    # porque .code sale del JSON del error y vale -1 si no se pudo parsear.
    codigo = getattr(getattr(e, "response", None), "status_code", None)
    if not isinstance(codigo, int):
        codigo = getattr(e, "code", None)
    if isinstance(codigo, int) and codigo > 0:
        return codigo in CODIGOS_TRANSITORIOS

    # Sin codigo HTTP puede ser un corte de red, que tambien se arregla solo.
    # Los errores de gspread que no son de red si traen codigo, asi que esto
    # no se traga una mala configuracion.
    try:
        import requests
        return isinstance(e, (requests.exceptions.ConnectionError,
                              requests.exceptions.Timeout))
    except ImportError:
        return False


def _reintentar(operacion):
    """Corre la operacion, reintentando solo si Google contesto algo transitorio."""
    for intento in range(1, INTENTOS + 1):
        try:
            return operacion()
        except AlmacenError:
            raise
        except Exception as e:
            if intento == INTENTOS or not _es_transitorio(e):
                raise
            time.sleep(ESPERA_BASE ** intento)


# ------------------------------------------------------------------ config

def _seccion(nombre):
    """
    Lee una seccion de los secrets. Primero de Streamlit (si la app esta
    corriendo), si no de .streamlit/secrets.toml, para que los scripts de
    terminal usen exactamente la misma configuracion.
    """
    try:
        import streamlit as st
        seccion = st.secrets.get(nombre)
        if seccion:
            return dict(seccion)
    except Exception:
        pass

    if SECRETS_LOCAL.exists():
        import tomllib
        try:
            with open(SECRETS_LOCAL, "rb") as f:
                return dict(tomllib.load(f).get(nombre) or {})
        except Exception:
            return {}
    return {}


def _config():
    return _seccion("gsheets")


def credenciales_meli():
    """Credenciales de la app de ML si estan en secrets; si no, {}."""
    return _seccion("mercadolibre")


def hay_sheet():
    cfg = _config()
    return bool(cfg.get("spreadsheet_id") and
                (cfg.get("service_account") or cfg.get("service_account_json_path")))


def _abrir():
    """Abre la planilla. Solo se llama si hay_sheet() dio True."""
    try:
        import gspread
    except ImportError as e:
        raise AlmacenError(
            "Falta la libreria gspread. Instalala con: pip install gspread"
        ) from e

    cfg = _config()
    sa = cfg.get("service_account")
    if sa:
        credenciales = dict(sa)
    else:
        ruta = Path(cfg["service_account_json_path"])
        if not ruta.is_absolute():
            ruta = DIR / ruta
        if not ruta.exists():
            raise AlmacenError(f"No existe el archivo de credenciales: {ruta}")
        credenciales = json.loads(ruta.read_text(encoding="utf-8"))

    cliente = gspread.service_account_from_dict(credenciales)
    try:
        cliente.set_timeout(TIMEOUT_SHEETS)
    except AttributeError:          # gspread viejo: sin timeout, como antes
        pass
    try:
        return _reintentar(lambda: cliente.open_by_key(cfg["spreadsheet_id"]))
    except Exception as e:
        # Dos causas muy distintas, dos mensajes distintos: mandar a revisar
        # los permisos cuando el problema es que Google esta caido hace perder
        # el tiempo buscando donde no hay nada roto.
        if _es_transitorio(e):
            raise AlmacenError(
                f"Google Sheets no respondio en {INTENTOS} intentos. Es una "
                f"falla momentanea de Google, no de la configuracion: la "
                f"proxima corrida deberia andar sola. Detalle: {e}") from e
        raise AlmacenError(
            f"No pude abrir la Google Sheet ({cfg['spreadsheet_id']}). "
            f"Verifica el ID y que este compartida como Editor con el "
            f"client_email del service account. Detalle: {e}") from e


def _hoja(planilla, titulo, columnas):
    import gspread
    try:
        # WorksheetNotFound no trae codigo HTTP, asi que _reintentar la deja
        # pasar de una y la hoja se crea sin esperar de mas.
        return _reintentar(lambda: planilla.worksheet(titulo))
    except gspread.WorksheetNotFound:
        hoja = planilla.add_worksheet(title=titulo, rows=1000,
                                      cols=max(len(columnas), 8))
        hoja.append_row(columnas)
        return hoja


# ------------------------------------------------------------------ tokens

def _cuenta_de(fila):
    """
    La cuenta de una fila guardada. Las filas viejas no tienen la columna, y
    esas son de CRAFTERS: es la unica que existia cuando se escribieron.
    """
    return (str(fila.get("cuenta") or "").strip().lower()
            or CUENTA_POR_DEFECTO)


def _filas_tokens():
    hoja = _hoja(_abrir(), HOJA_TOKENS, COLUMNAS_TOKENS)
    return hoja, _reintentar(hoja.get_all_records)


def cuentas_con_token():
    """Que cuentas tienen autorizacion guardada."""
    if not hay_sheet():
        return [CUENTA_POR_DEFECTO] if TOKENS_LOCAL.exists() else []
    try:
        _, filas = _filas_tokens()
    except Exception:
        return []
    return sorted({_cuenta_de(f) for f in filas if f.get("access_token")})


def leer_tokens(cuenta=CUENTA_POR_DEFECTO):
    """
    Los tokens de una cuenta, o None si esa cuenta no esta autorizada.

    Cada cuenta es una fila. La columna `cuenta` se agrego despues, asi que
    las filas que no la tienen se leen como CRAFTERS — era la unica cuenta
    cuando se escribieron.
    """
    cuenta = (cuenta or CUENTA_POR_DEFECTO).strip().lower()
    if hay_sheet():
        try:
            _, filas = _filas_tokens()
            propias = [f for f in filas if _cuenta_de(f) == cuenta]
            if propias:
                d = dict(propias[-1])     # siempre vale el ultimo guardado
                d["expira_en"] = float(d.get("expira_en") or 0)
                d["user_id"] = int(d.get("user_id") or 0)
                d["cuenta"] = cuenta
                return d
            return None
        except AlmacenError:
            raise
        except Exception as e:
            raise AlmacenError(f"No pude leer los tokens de la Sheet: {e}") from e

    if cuenta == CUENTA_POR_DEFECTO and TOKENS_LOCAL.exists():
        return json.loads(TOKENS_LOCAL.read_text(encoding="utf-8"))
    local = TOKENS_LOCAL.with_name(f"tokens_{cuenta}.json")
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    return None


def guardar_tokens(datos, cuenta=CUENTA_POR_DEFECTO):
    """
    Guarda los tokens de una cuenta **sin pisar los de las otras**.

    Antes la hoja se vaciaba entera y se escribia una sola fila. Con varias
    cuentas eso borraria las demas, y como el refresh_token es de un solo uso
    la cuenta pisada quedaria muerta hasta reautorizarla a mano.
    """
    cuenta = (cuenta or CUENTA_POR_DEFECTO).strip().lower()
    datos = dict(datos)
    datos["cuenta"] = cuenta

    if hay_sheet():
        try:
            hoja, filas = _filas_tokens()
            otras = [f for f in filas if _cuenta_de(f) != cuenta]
            nuevas = otras + [datos]

            # Se reintenta la secuencia completa, no cada paso: como arranca
            # borrando, repetirla deja el mismo resultado. Y hay que insistir,
            # porque el refresh_token es de un solo uso: si el que acabamos de
            # usar no queda guardado, hay que reautorizar a mano.
            def escribir():
                hoja.clear()
                hoja.append_row(COLUMNAS_TOKENS)
                for f in nuevas:
                    hoja.append_row([str(f.get(c, "")) for c in COLUMNAS_TOKENS])

            _reintentar(escribir)
            return datos
        except Exception as e:
            raise AlmacenError(f"No pude guardar los tokens en la Sheet: {e}") from e

    destino = (TOKENS_LOCAL if cuenta == CUENTA_POR_DEFECTO
               else TOKENS_LOCAL.with_name(f"tokens_{cuenta}.json"))
    # Escritura atomica: si se corta a la mitad no perdemos el refresh_token.
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    tmp.replace(destino)
    return datos


# ------------------------------------------------------------------ auditoria

def append_auditoria(filas):
    """
    Agrega filas al registro de auditoria. Append-only a proposito: si algo
    sale mal, esto es lo unico que dice como estaba antes.

    Nunca hace fallar la operacion principal: si el registro no se puede
    escribir, avisa pero el cambio en ML ya esta hecho.
    """
    if not filas:
        return True, ""

    if hay_sheet():
        try:
            # El append NO se reintenta a proposito: un 503 no dice si la
            # escritura llego, y reintentar duplicaria el registro. La
            # apertura de la planilla si reintenta, que es donde mas pega.
            hoja = _hoja(_abrir(), HOJA_AUDITORIA, COLUMNAS_AUDITORIA)
            hoja.append_rows([[str(f.get(c, "")) for c in COLUMNAS_AUDITORIA]
                              for f in filas])
            return True, ""
        except Exception as e:
            # Igual dejamos rastro local para no perder el registro.
            _append_local(filas)
            return False, f"No pude escribir la auditoria en la Sheet: {e}"

    _append_local(filas)
    return True, ""


def _append_local(filas):
    import csv
    nuevo = not AUDITORIA_LOCAL.exists()
    with open(AUDITORIA_LOCAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_AUDITORIA)
        if nuevo:
            w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS_AUDITORIA})


# ------------------------------------------------------------------ hojas genericas
#
# Lo de abajo lo usa el control de stock, que necesita varias hojas propias.
# Con fallback a CSV local para poder trabajar sin Sheet configurada.

def _csv_local(titulo):
    return DIR / f"{titulo}.csv"


def leer_hoja(titulo, columnas):
    """Devuelve la hoja como lista de dicts. Si no existe, lista vacia."""
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)
            return _reintentar(hoja.get_all_records)
        except Exception as e:
            raise AlmacenError(f"No pude leer la hoja '{titulo}': {e}") from e

    ruta = _csv_local(titulo)
    if not ruta.exists():
        return []
    import csv
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def columna_hoja(titulo, columnas, nombre):
    """
    Devuelve los valores de UNA columna. Se usa para las claves de
    idempotencia: traer solo esa columna es mucho mas liviano que bajar
    todas las filas cada vez que corre la sincronizacion.
    """
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)
            idx = columnas.index(nombre) + 1
            valores = _reintentar(lambda: hoja.col_values(idx))
            return [v for v in valores[1:] if v]
        except Exception as e:
            raise AlmacenError(f"No pude leer la columna '{nombre}': {e}") from e
    return [str(f.get(nombre, "")) for f in leer_hoja(titulo, columnas)
            if f.get(nombre)]


def append_hoja(titulo, columnas, filas):
    """Agrega filas al final. Devuelve (ok, detalle)."""
    if not filas:
        return True, ""
    if hay_sheet():
        try:
            # Sin reintento, por lo mismo que append_auditoria: repetir un
            # append duplica filas. El control de stock evita duplicados
            # comparando claves ANTES de escribir, y un reintento a ciegas
            # aca adentro se saltearia ese control.
            hoja = _hoja(_abrir(), titulo, columnas)
            hoja.append_rows([[str(f.get(c, "")) for c in columnas] for f in filas])
            return True, ""
        except Exception as e:
            return False, f"No pude escribir en '{titulo}': {e}"

    import csv
    ruta = _csv_local(titulo)
    nuevo = not ruta.exists()
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        if nuevo:
            w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in columnas})
    return True, ""


def reescribir_hoja(titulo, columnas, filas):
    """Reemplaza el contenido completo. Se usa al resolver devoluciones."""
    if hay_sheet():
        try:
            hoja = _hoja(_abrir(), titulo, columnas)

            # Igual que en guardar_tokens: empieza borrando, asi que repetirla
            # no duplica nada.
            def escribir():
                hoja.clear()
                hoja.append_row(columnas)
                if filas:
                    hoja.append_rows([[str(f.get(c, "")) for c in columnas]
                                      for f in filas])

            _reintentar(escribir)
            return True, ""
        except Exception as e:
            return False, f"No pude reescribir '{titulo}': {e}"

    import csv
    with open(_csv_local(titulo), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in columnas})
    return True, ""


# ------------------------------------------------------------------ estado

def describir():
    """Texto corto para mostrar en la app: donde se esta guardando todo."""
    if hay_sheet():
        cfg = _config()
        return f"Google Sheet ({str(cfg.get('spreadsheet_id'))[:12]}...)"
    return "archivos locales (no sobreviven a un reinicio en la nube)"


if __name__ == "__main__":
    print(f"Modo de almacenamiento: {describir()}")
    if hay_sheet():
        try:
            t = leer_tokens()
            print("Tokens en la Sheet:",
                  f"user_id={t['user_id']}, vence {time.ctime(t['expira_en'])}"
                  if t else "todavia no hay (correr autorizar.py)")
        except AlmacenError as e:
            print(f"ERROR: {e}")
