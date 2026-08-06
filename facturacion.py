#!/usr/bin/env python3
"""
Control mensual de percepciones y retenciones.

    python facturacion.py                 -> cuenta por defecto, ultimo periodo
    python facturacion.py erpa            -> otra cuenta
    python facturacion.py erpa 3          -> ultimos 3 periodos

Todos los meses alguien revisa a mano si MercadoLibre percibio o retuvo donde
no correspondia. Esto lo hace solo.

**Percepcion y retencion no viven en el mismo lado.** La percepcion es del
comprobante y sale de la API de facturacion al toque. La retencion es del pago
y sale del reporte de liberaciones de Mercado Pago, que hay que **pedir y
esperar** (~1 minuto). Por eso este modulo tiene dos caminos distintos.

Que NO es: una liquidacion de impuestos. Una retencion no es plata perdida,
es credito contra IIBB. Lo que este control busca es lo que **no
correspondia** cobrar -- eso si se reclama.
"""

import re
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

import almacen
from meli import Meli, MeliError

MP = "https://api.mercadopago.com"

HOJA_CERT = "certificados"
COLS_CERT = ["cuenta", "jurisdiccion", "percepcion", "retencion",
             "desde", "hasta", "numero"]

# Certificados de ERPA a agosto 2026. Es solo la semilla: la hoja manda, y
# desde la app se editan. Catamarca cubre percepciones pero NO retenciones.
SEMILLA = [
    {"cuenta": "erpa", "jurisdiccion": "Catamarca", "percepcion": "si",
     "retencion": "no", "desde": "2026-05-22", "hasta": "2027-05-31",
     "numero": ""},
    {"cuenta": "erpa", "jurisdiccion": "Corrientes", "percepcion": "si",
     "retencion": "si", "desde": "2026-04-11", "hasta": "2026-10-12",
     "numero": ""},
    {"cuenta": "erpa", "jurisdiccion": "Chubut", "percepcion": "si",
     "retencion": "si", "desde": "", "hasta": "2026-10-18", "numero": ""},
    {"cuenta": "erpa", "jurisdiccion": "Misiones", "percepcion": "si",
     "retencion": "si", "desde": "", "hasta": "2026-09-06", "numero": ""},
]

# Las dos fuentes nombran la misma provincia distinto: la facturacion usa
# codigos (MLA_RG_IIBB_CAT) y Mercado Pago nombres sueltos (buenos_aires).
# Sin normalizar, el cruce contra los certificados no encuentra nada.
JURISDICCIONES = {
    "cat": "Catamarca", "catamarca": "Catamarca",
    "ctes": "Corrientes", "corrientes": "Corrientes",
    "chu": "Chubut", "chubut": "Chubut",
    "mis": "Misiones", "misiones": "Misiones",
    "caba": "CABA", "capital": "CABA", "ciudad": "CABA",
    "ba": "Buenos Aires", "bsas": "Buenos Aires",
    "buenos_aires": "Buenos Aires", "buenosaires": "Buenos Aires",
    "tuc": "Tucuman", "tucuman": "Tucuman",
    "cba": "Cordoba", "cordoba": "Cordoba",
    "sfe": "Santa Fe", "santa_fe": "Santa Fe", "santafe": "Santa Fe",
    "mza": "Mendoza", "mendoza": "Mendoza",
    "slt": "Salta", "salta": "Salta",
    "er": "Entre Rios", "entre_rios": "Entre Rios",
    "chaco": "Chaco", "cha": "Chaco",
    "formosa": "Formosa", "for": "Formosa",
    "jujuy": "Jujuy", "juj": "Jujuy",
    "lapampa": "La Pampa", "la_pampa": "La Pampa",
    "larioja": "La Rioja", "la_rioja": "La Rioja",
    "neuquen": "Neuquen", "nqn": "Neuquen", "nq": "Neuquen",
    "rionegro": "Rio Negro", "rio_negro": "Rio Negro",
    "sanjuan": "San Juan", "san_juan": "San Juan",
    "sanluis": "San Luis", "san_luis": "San Luis",
    "santacruz": "Santa Cruz", "santa_cruz": "Santa Cruz",
    "sgo": "Santiago del Estero", "santiago": "Santiago del Estero",
    "tdf": "Tierra del Fuego", "tierra_del_fuego": "Tierra del Fuego",
}

# No son retenciones de IIBB y no se reclaman: el impuesto al cheque es un
# impuesto, no una retencion. Sumarlos infla el total ~60%.
NO_ES_RETENCION = ("debitos_creditos", "tax_debitos_creditos")


def normalizar(bruto):
    """
    'MLA_RG_IIBB_CTES' -> 'Corrientes'. Si no la reconoce **devuelve el texto
    original**, nunca None: una jurisdiccion que no entra en el mapa tiene que
    aparecer en el informe, no desaparecer de la suma.
    """
    if not bruto:
        return ""
    t = str(bruto).strip().lower()
    t = re.sub(r"^mla[_-]", "", t)
    t = re.sub(r"^(rg|reg|regimen)[_-]", "", t)
    t = re.sub(r"^iibb[_-]?", "", t)
    t = re.sub(r"[_-]?iibb$", "", t)
    t = t.strip("_-. ")
    return JURISDICCIONES.get(t, str(bruto).strip())


# --------------------------------------------------------------- certificados

def certificados(cuenta=None):
    """Los certificados de no percepcion/retencion, de la hoja editable."""
    df = pd.DataFrame(almacen.leer_hoja(HOJA_CERT, COLS_CERT))
    if not len(df):
        df = pd.DataFrame(SEMILLA)
    if cuenta:
        df = df[df["cuenta"].astype(str).str.lower() == cuenta.lower()]
    df = df.copy()
    df["jurisdiccion"] = df["jurisdiccion"].map(normalizar)
    return df.reset_index(drop=True)


def guardar_certificados(filas):
    almacen.reescribir_hoja(HOJA_CERT, COLS_CERT, filas)


def _fecha(v, x=None):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return x
    t = str(v).strip()[:10]
    if not t:
        return x
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            pass
    return x


def _cubre(cert, tipo, desde, hasta):
    """
    Cuanto del periodo [desde, hasta] cubre el certificado: 'todo', 'parte' o
    'nada'. La distincion importa: un certificado emitido a mitad del periodo
    **no vuelve reclamable todo el mes**, solo lo posterior. Sin esto, el
    control marca de mas -- medido en ERPA, $3.075.600 mal marcados.
    """
    if str(cert.get(tipo, "")).strip().lower() not in ("si", "sí", "x", "1", "true"):
        return "nada"
    d = _fecha(cert.get("desde"), date(1900, 1, 1))
    h = _fecha(cert.get("hasta"), date(2999, 12, 31))
    if hasta < d or desde > h:
        return "nada"
    return "todo" if (d <= desde and hasta <= h) else "parte"


# --------------------------------------------------- percepciones (inmediato)

# ML y Mercado Pago facturan por separado y cada uno percibe lo suyo. En ERPA
# la de MP es chica (~7% de la de ML) pero incluye las mismas provincias, asi
# que mirar una sola deja plata reclamable afuera.
TODOS_LOS_GRUPOS = ("ML", "MP")


def periodos(ml, cuantos=6, solo_cerrados=True, grupo="ML"):
    """
    Periodos de facturacion, del mas reciente al mas viejo.

    El periodo en curso se saltea: no esta consolidado y ademas
    `perceptions/summary` **contesta HTTP 500** sobre el periodo abierto.
    """
    r = ml.get("/billing/integration/monthly/periods",
               group=grupo, document_type="BILL")
    hoy = date.today().isoformat()
    salida = []
    for p in (r.get("results") or []):
        fin = p["period"]["date_to"]
        if solo_cerrados and fin >= hoy:
            continue
        salida.append({"clave": p.get("key"), "grupo": grupo,
                       "desde": p["period"]["date_from"], "hasta": fin})
        if len(salida) >= cuantos:
            break
    return salida


def periodos_de_todos(ml, cuantos=6):
    """Los periodos de las dos facturaciones juntos."""
    todos = []
    for g in TODOS_LOS_GRUPOS:
        try:
            todos += periodos(ml, cuantos, grupo=g)
        except MeliError:
            pass          # una cuenta puede no facturar por ese grupo
    return todos


def percepciones(ml, clave, grupo="ML"):
    """
    Percepciones de un periodo, una fila por percepcion.

    Tres cosas que no son obvias y cada una rompe el control en silencio:

    - **Sin `group` y `document_type` devuelve 422.** No es que no haya datos.
    - **Hay dos facturaciones, ML y MP**, cada una con sus periodos y sus
      percepciones. Leer solo una deja afuera la otra (ver `TODOS_LOS_GRUPOS`).
    - **`tax_type` NO sirve para saber si es IIBB.** Solo Buenos Aires dice
      literalmente "IIBB"; Catamarca dice "IBCA", Corrientes "IBCO", Neuquen
      "IBNQ". Filtrar por ahi descarta justo las provincias con certificado.
      El que si sirve es `regimen_tax_type`: `MLA_RG_IIBB_*` contra
      `MLA_RG_IVA`.
    """
    r = ml.get(f"/billing/integration/periods/key/{clave}/perceptions/summary",
               group=grupo, document_type="BILL")

    filas = []
    for p in (r.get("summary") or []):
        monto = float(p.get("amount") or 0)
        if not monto:
            continue
        codigo = p.get("regimen_tax_type") or ""
        filas.append({
            "fecha": (p.get("bill_date") or "")[:10],
            "jurisdiccion": normalizar(codigo),
            "codigo": codigo,
            "es_iibb": "IIBB" in codigo.upper(),
            "tributo": p.get("tax_type") or "",
            "concepto": p.get("regimen_tax_type_description") or "",
            "monto": abs(monto),
            "alicuota": float(p.get("aliquot") or 0),
            "base": float(p.get("taxable_amount") or 0),
            "estado": p.get("status") or "",
            "factura": grupo,
            "comprobante": p.get("document_id") or "",
        })
    df = pd.DataFrame(filas)
    # Una percepcion revertida no se cobro: contarla inventa un reclamo.
    if len(df):
        df = df[df["estado"].str.upper() != "REVERSED"].reset_index(drop=True)
    return df


# ------------------------------------------------- retenciones (asincronico)

def _mp(ml, ruta, metodo="GET", cuerpo=None, timeout=180):
    h = {"Authorization": f"Bearer {ml.token}", "Accept": "application/json"}
    if metodo == "POST":
        h["Content-Type"] = "application/json"
        r = requests.post(MP + ruta, headers=h, json=cuerpo, timeout=timeout)
    else:
        r = requests.get(MP + ruta, headers=h, timeout=timeout)
    if r.status_code >= 400:
        raise MeliError(f"Mercado Pago {r.status_code} en {ruta}: {r.text[:200]}")
    return r


def pedir_reporte(ml, desde, hasta):
    """Encola el reporte de liberaciones. Devuelve el id."""
    r = _mp(ml, "/v1/account/settlement_report", "POST", {
        "begin_date": f"{desde}T00:00:00Z",
        "end_date": f"{hasta}T23:59:59Z",
    }, timeout=90)
    return (r.json() or {}).get("id")


def esperar_reporte(ml, id_reporte, callback=None, espera=20, vueltas=30):
    """Espera a que el reporte tenga archivo. Devuelve el nombre, o None."""
    for i in range(vueltas):
        for x in _mp(ml, "/v1/account/settlement_report/list", timeout=60).json():
            if x.get("id") == id_reporte and x.get("file_name"):
                return x["file_name"]
        if callback:
            callback(f"generando el reporte... ({(i + 1) * espera}s)")
        time.sleep(espera)
    return None


def parsear_retenciones(texto):
    """
    Saca las retenciones de `TAXES_DISAGGREGATED`, una fila por retencion.

    **El CSV no se puede leer con pandas.** El JSON va entre comillas y las de
    adentro no estan escapadas, asi que la cantidad de campos cambia fila a
    fila (`Expected 56 fields, saw 59`). Por eso va con regex sobre la linea
    cruda.
    """
    filas = []
    for ln in texto.splitlines()[1:]:
        # Ojo con filtrar por tipo de movimiento: hay retenciones fuera de
        # SETTLEMENT (devoluciones, contracargos). Exigirlo se comia filas en
        # silencio. El monto cobrado puede faltar; la retencion no.
        f = re.search(r",(?:SETTLEMENT|REFUND|[A-Z_]+),([0-9.]+),ARS", ln)
        cobrado = float(f.group(1)) if f else 0.0
        fecha = re.search(r"(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}", ln)
        mov = re.search(r",(\d{9,}),\d+,", ln)
        for arr in re.findall(r"\[\s*\{.*?\}\s*\]", ln):
            if "financial_entity" not in arr:
                continue
            for g in re.finditer(
                    r'"financial_entity":"([^"]+)","amount":"([-0-9.]+)"'
                    r',"detail":"([^"]+)"', arr):
                ent, monto, det = g.group(1), abs(float(g.group(2))), g.group(3)
                if "withholding" not in det or ent in NO_ES_RETENCION:
                    continue
                filas.append({
                    "fecha": fecha.group(1) if fecha else "",
                    "movimiento": mov.group(1) if mov else "",
                    "jurisdiccion": normalizar(ent),
                    "codigo": ent,
                    "regimen": det,
                    "monto": monto,
                    "cobrado": cobrado,
                    "alicuota": (100 * monto / cobrado) if cobrado else 0,
                })
    return pd.DataFrame(filas)


def retenciones(ml, desde, hasta, callback=None):
    """Pide, espera y baja. Es el camino largo: no hay atajo."""
    if callback:
        callback("pidiendo el reporte a Mercado Pago...")
    id_r = pedir_reporte(ml, desde, hasta)
    if not id_r:
        raise MeliError("Mercado Pago no devolvio id de reporte")
    nombre = esperar_reporte(ml, id_r, callback)
    if not nombre:
        raise MeliError("el reporte no se genero a tiempo; probar de nuevo")
    if callback:
        callback("bajando el reporte...")
    r = _mp(ml, f"/v1/account/settlement_report/{nombre}")
    return parsear_retenciones(r.text)


# ------------------------------------------------------------------ el control

def _hallazgos_por_certificado(df, certs, tipo, desde, hasta):
    """
    Lo cobrado en una jurisdiccion con certificado vigente.

    **Se compara fecha por fecha, no periodo contra periodo.** Un certificado
    emitido a mitad de mes no vuelve reclamable todo el mes. Medido en ERPA:
    cruzar solo por jurisdiccion marcaba $3.075.600 que en realidad eran
    anteriores al certificado.

    Para percepciones mira **solo IIBB**: las de IVA vienen en la misma
    respuesta y ningun certificado provincial las cubre.
    """
    salida = []
    if tipo == "percepcion" and "es_iibb" in df:
        df = df[df["es_iibb"]]

    for _, c in certs.iterrows():
        sub = df[df["jurisdiccion"] == c["jurisdiccion"]]
        if not len(sub) or _cubre(c, tipo, desde, hasta) == "nada":
            continue

        d = _fecha(c.get("desde"), date(1900, 1, 1))
        h = _fecha(c.get("hasta"), date(2999, 12, 31))
        vig = sub["fecha"].map(lambda f: (lambda x: bool(x) and d <= x <= h)
                               (_fecha(f))) if "fecha" in sub else None

        if vig is None or not vig.notna().any() or not sub["fecha"].astype(bool).any():
            # Sin fecha por fila no se puede afinar: queda para revisar.
            salida.append({
                "gravedad": "revisar",
                "control": f"{tipo} con certificado vigente",
                "jurisdiccion": c["jurisdiccion"],
                "monto": round(float(sub["monto"].sum()), 2),
                "casos": len(sub),
                "detalle": (f"certificado del {c['desde'] or 's/f'} al "
                            f"{c['hasta'] or 's/f'}; sin fecha por movimiento "
                            f"no se puede separar lo anterior"),
            })
            continue

        dentro, fuera = sub[vig], sub[~vig]
        if len(dentro):
            # La percepcion se cobra en la fecha de la factura pero grava las
            # operaciones de todo el periodo. Si el certificado arranco a
            # mitad, parte de la base es anterior: se reclama igual, pero el
            # monto puede discutirse y conviene que se sepa.
            parcial = desde < d <= hasta
            salida.append({
                "gravedad": "reclamable",
                "control": f"{tipo} con certificado vigente",
                "jurisdiccion": c["jurisdiccion"],
                "monto": round(float(dentro["monto"].sum()), 2),
                "casos": len(dentro),
                "detalle": (f"cobrado entre {dentro['fecha'].min()} y "
                            f"{dentro['fecha'].max()}, con certificado vigente "
                            f"({c['desde'] or 's/f'} a {c['hasta'] or 's/f'}): "
                            f"no correspondía" +
                            (f". Ojo: el certificado empezó el {c['desde']}, "
                             f"dentro de este mes, así que parte de la base "
                             f"gravada es anterior" if parcial else "") +
                            (f". Otros ${fuera['monto'].sum():,.0f} del mismo "
                             f"mes son anteriores al certificado y están bien "
                             f"cobrados" if len(fuera) else "")),
            })
    return salida


def _hallazgos_alicuota(df, tipo):
    """
    Alicuotas que se salen del resto dentro de la misma jurisdiccion y
    regimen. No hay padron que consultar, asi que el patron sale del propio
    dato: si 90 movimientos van al 0,4% y uno al 3%, ese uno se mira.
    """
    salida = []
    if not len(df) or "alicuota" not in df:
        return salida
    llave = ["jurisdiccion"] + (["regimen"] if "regimen" in df else [])
    for k, g in df.groupby(llave):
        if len(g) < 5:
            continue
        tipica = g["alicuota"].median()
        if tipica <= 0:
            continue
        raros = g[(g["alicuota"] - tipica).abs() / tipica > 0.25]
        if not len(raros):
            continue
        j = k[0] if isinstance(k, tuple) else k
        salida.append({
            "gravedad": "revisar",
            "control": f"alícuota de {tipo} fuera de lo habitual",
            "jurisdiccion": j,
            "monto": round(float(raros["monto"].sum()), 2),
            "casos": len(raros),
            "detalle": (f"lo habitual es {tipica:.3f}% y estos van de "
                        f"{raros['alicuota'].min():.3f}% a "
                        f"{raros['alicuota'].max():.3f}%"),
        })
    return salida


def _hallazgos_doble(df):
    """
    Dos regimenes distintos reteniendo sobre el mismo movimiento y la misma
    jurisdiccion. Puede ser legitimo (son regimenes distintos), por eso queda
    en 'revisar' y no en 'reclamable'.
    """
    if not len(df) or "movimiento" not in df:
        return []
    g = df.groupby(["movimiento", "jurisdiccion"])["regimen"].nunique()
    dobles = g[g > 1]
    if not len(dobles):
        return []
    salida = []
    for j in dobles.index.get_level_values("jurisdiccion").unique():
        movs = [m for m, jj in dobles.index if jj == j]
        sub = df[df["movimiento"].isin(movs) & (df["jurisdiccion"] == j)]
        regs = sorted(sub["regimen"].unique())
        salida.append({
            "gravedad": "revisar",
            "control": "dos regímenes sobre el mismo movimiento",
            "jurisdiccion": j,
            "monto": round(float(sub["monto"].sum()), 2),
            "casos": len(movs),
            "detalle": (f"{' + '.join(regs)} = "
                        f"{sub.groupby('movimiento')['alicuota'].sum().median():.2f}% "
                        f"sobre la misma base"),
        })
    return salida


def vencimientos(certs, dias=45):
    """Certificados por vencer. Si vence y nadie renueva, empiezan a cobrar."""
    hoy = date.today()
    salida = []
    for _, c in certs.iterrows():
        h = _fecha(c.get("hasta"))
        if not h:
            continue
        faltan = (h - hoy).days
        if faltan > dias:
            continue
        salida.append({
            "gravedad": "vencido" if faltan < 0 else "revisar",
            "control": "certificado por vencer",
            "jurisdiccion": c["jurisdiccion"],
            "monto": 0.0,
            "casos": 1,
            "detalle": (f"venció hace {-faltan} días ({c['hasta']})"
                        if faltan < 0 else
                        f"vence en {faltan} días ({c['hasta']})"),
        })
    return salida


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def meses_disponibles(ml, cuantos=12):
    """
    Los meses que se pueden controlar, del mas reciente al mas viejo.

    **El periodo de facturacion de ML no es el mes calendario** (julio 2026
    fue del 13 al 29). La contabilidad cierra por mes, asi que el control va
    por mes y adentro busca los periodos que caen ahi.
    """
    vistos = set()
    for p in periodos_de_todos(ml, cuantos * 2):
        f = _fecha(p["hasta"])
        if f:
            vistos.add((f.year, f.month))
    return [{"anio": a, "mes": m, "etiqueta": f"{MESES[m - 1]} {a}"}
            for a, m in sorted(vistos, reverse=True)[:cuantos]]


def rango_del_mes(anio, mes):
    d = date(anio, mes, 1)
    h = date(anio + (mes == 12), (mes % 12) + 1, 1) - timedelta(days=1)
    return d.isoformat(), h.isoformat()


def controlar(ml, cuenta, anio, mes, con_retenciones=True, callback=None):
    """
    El control de un mes. Devuelve (hallazgos, percepciones, retenciones).

    Las percepciones salen de los periodos de facturacion que cierran en ese
    mes; las retenciones, del mes calendario completo. Son fuentes distintas y
    **sus rangos no coinciden exactamente**: por eso el cruce contra los
    certificados va por la fecha de cada movimiento, no por el rango.

    `con_retenciones=False` corre solo la parte inmediata: sirve para ver algo
    en pantalla sin esperar el minuto del reporte de Mercado Pago.
    """
    certs = certificados(cuenta)
    desde, hasta = rango_del_mes(anio, mes)
    d, h = _fecha(desde), _fecha(hasta)

    if callback:
        callback("leyendo percepciones (facturación ML y MP)...")
    partes = []
    for p in periodos_de_todos(ml, 24):
        f = _fecha(p["hasta"])
        if not f or (f.year, f.month) != (anio, mes):
            continue
        x = percepciones(ml, p["clave"], p["grupo"])
        if len(x):
            partes.append(x)
    per = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()

    ret = pd.DataFrame()
    if con_retenciones:
        ret = retenciones(ml, desde, hasta, callback)

    hall = []
    if len(per):
        hall += _hallazgos_por_certificado(per, certs, "percepcion", d, h)
        hall += _hallazgos_alicuota(per, "percepción")
    if len(ret):
        hall += _hallazgos_por_certificado(ret, certs, "retencion", d, h)
        hall += _hallazgos_alicuota(ret, "retención")
        hall += _hallazgos_doble(ret)
    hall += vencimientos(certs)

    orden = {"reclamable": 0, "vencido": 1, "revisar": 2}
    hall.sort(key=lambda x: (orden.get(x["gravedad"], 9), -x["monto"]))
    return pd.DataFrame(hall), per, ret


def main():
    args = list(sys.argv[1:])
    cuenta = args[0] if args and not args[0].isdigit() else almacen.CUENTA_POR_DEFECTO
    cuantos = int([a for a in args if a.isdigit()][0]) if any(
        a.isdigit() for a in args) else 1

    ml = Meli(verbose=False, cuenta=cuenta)
    pes = lambda v: f"${v:,.0f}".replace(",", ".")

    for m in meses_disponibles(ml, cuantos):
        print(f"\n=== {cuenta.upper()}  {m['etiqueta']} ===")
        hall, per, ret = controlar(ml, cuenta, m["anio"], m["mes"],
                                   callback=lambda x: print(f"  {x}"))
        print(f"\n  percepciones {pes(per['monto'].sum() if len(per) else 0)}"
              f"   retenciones {pes(ret['monto'].sum() if len(ret) else 0)}")
        if not len(hall):
            print("\n  Sin observaciones.")
            continue
        print()
        for _, f in hall.iterrows():
            marca = {"reclamable": "⚠", "vencido": "⚠"}.get(f["gravedad"], " ")
            print(f"  {marca} [{f['gravedad']}] {f['control']} — "
                  f"{f['jurisdiccion']}  {pes(f['monto'])}")
            print(f"      {f['detalle']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
