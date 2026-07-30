#!/usr/bin/env python3
"""
Buy Box del catalogo: en que publicaciones ganas la venta y en cuales no.

    python buybox.py            -> todas las publicaciones de catalogo
    python buybox.py 200        -> solo las 200 que mas vendieron (mas rapido)

**Por que esto importa mas que cualquier otro analisis.** 1.009 de las 2.275
publicaciones activas de CRAFTERS compiten en una pagina de catalogo. En esas
paginas todos los vendedores comparten la MISMA publicacion y MercadoLibre
elige a uno solo para mostrar: el que gana se lleva practicamente todas las
ventas, y el resto queda escondido detras de "otras opciones de compra". No es
una diferencia de posicion, es vender o no vender.

`/items/{id}/price_to_win` dice, para cada publicacion: si estas ganando, a que
precio ganarias, a que precio esta vendiendo el que gana hoy, y que palancas
tenes sin usar (Full, envio gratis, cuotas).

**La lectura que no es obvia.** El `price_to_win` casi nunca es igual al precio
del ganador. Suele ser bastante mas bajo. Eso NO es un error: MercadoLibre
pondera el precio junto con los beneficios de la publicacion, asi que si el
ganador tiene Full y vos no, para empatarle tenes que compensar con precio. La
diferencia entre lo que cobra el ganador y lo que tendrias que cobrar vos es,
literalmente, **lo que te cuesta en pesos no tener esas palancas**.

De ahi salen dos diagnosticos que piden cosas opuestas:

  - **Perdes por precio**: el ganador esta mas barato. Se arregla con precio.
  - **Perdes estando mas barato**: ya cobras menos que el ganador y aun asi
    perdes. Bajar mas el precio es tirar plata — lo que falta son las palancas.
    Aca es donde Full deja de ser una idea y se vuelve una cuenta concreta.

El calculo de lo que queda al precio para ganar usa los cargos reales de cada
SKU (comision y envio medidos de las ventas, no una tabla teorica). Es **antes
del costo de la mercaderia**, que la API no conoce: sirve para descartar los
casos donde ganar el Buy Box directamente da negativo.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent
CACHE = DIR / "buybox_cache.json"

# Los competidores mueven precios todo el tiempo: mas viejo que esto, no sirve.
VIGENCIA_HORAS = 12

# Si alcanza con bajar menos que esto, es una decision facil.
BAJA_CHICA = 0.05
# Arriba de esto, ganar el Buy Box probablemente no valga la pena.
BAJA_GRANDE = 0.20

ESTADOS = {
    "winning": "ganando",
    "sharing_first_place": "compartiendo",
    "competing": "compitiendo",
    "not_listed": "no compite",
}


def _leer_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _vigente(entrada):
    if not entrada or "bajado" not in entrada:
        return False
    edad = time.time() - entrada["bajado"]
    return edad < VIGENCIA_HORAS * 3600


def traer_price_to_win(ml, item_ids, refrescar=False, callback=None):
    """
    item_id -> respuesta de /items/{id}/price_to_win (version v2).

    Es una llamada por publicacion (~0,3 s), asi que con el catalogo entero son
    unos 5 minutos. Se cachea 12 horas: los precios de los competidores se
    mueven, pero no cada media hora.
    """
    cache = _leer_cache()
    pendientes = [i for i in item_ids
                  if refrescar or not _vigente(cache.get(i))]

    for n, iid in enumerate(pendientes, start=1):
        try:
            r = ml.get(f"/items/{iid}/price_to_win", version="v2")
            r["bajado"] = time.time()
            cache[iid] = r
        except MeliError:
            cache[iid] = {"status": "error", "bajado": time.time()}
        if callback and n % 20 == 0:
            callback(f"Buy Box {n}/{len(pendientes)}...")

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {i: cache.get(i, {}) for i in item_ids}


def palancas(entrada):
    """(las que ya usa, las que tiene disponibles sin usar)."""
    usadas, libres = [], []
    for b in (entrada.get("boosts") or []):
        if not isinstance(b, dict):
            continue
        nombre = b.get("description") or b.get("id")
        if b.get("status") == "boosted":
            usadas.append(nombre)
        else:
            libres.append(nombre)
    return usadas, libres


def analizar(ml, pubs=None, tope=None, cargos=None, unidades=None,
             refrescar=False, callback=None):
    """
    Devuelve el DataFrame de publicaciones de catalogo.

    `cargos` es el DataFrame de `rentabilidad.cargos_por_sku()`: si viene, se
    calcula que queda por unidad al precio para ganar. `unidades` es un dict
    SKU -> unidades del periodo, para priorizar por lo que realmente vende.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    cat = [p for p in pubs
           if p.get("status") == "active" and p.get("catalog_listing")]

    # Priorizamos por ventas: si hay que cortar, que se corte por lo que menos
    # importa. `sold_quantity` es historico de toda la vida de la publicacion,
    # sirve para ordenar cuando no hay dato del periodo, pero **no se mezcla
    # con el en la misma columna**: son dos medidas distintas y ponerlas juntas
    # hace parecer que una publicacion sin ventas recientes vende muchisimo.
    def peso(p):
        sku = (sku_del_atributo(p) or "").strip().upper()
        if unidades and sku in unidades:
            return (1, unidades[sku])
        return (0, p.get("sold_quantity") or 0)

    cat.sort(key=peso, reverse=True)
    if tope:
        cat = cat[:tope]

    if callback:
        callback(f"Consultando el Buy Box de {len(cat)} publicaciones...")
    datos = traer_price_to_win(ml, [p["id"] for p in cat],
                               refrescar=refrescar, callback=callback)

    # Cargos por SKU para saber que queda al precio para ganar.
    tasa_comision, envio_fijo = {}, {}
    if cargos is not None and len(cargos):
        for _, f in cargos.iterrows():
            precio = f["precio_prom"] or 0
            if precio > 0:
                tasa_comision[f["sku"]] = f["comision_prom"] / precio
            envio_fijo[f["sku"]] = f["envio_prom"] or 0.0

    filas = []
    for p in cat:
        d = datos.get(p["id"]) or {}
        estado_api = d.get("status")
        sku = (sku_del_atributo(p) or "").strip().upper()

        actual = d.get("current_price")
        ptw = d.get("price_to_win")
        ganador = (d.get("winner") or {}).get("price")
        usadas, libres = palancas(d)

        bajar = (actual - ptw) if (actual is not None and ptw is not None) else None
        bajar_pct = (bajar / actual) if (bajar is not None and actual) else None
        # Lo que cuesta no tener las palancas del ganador: el ganador puede
        # cobrar mas caro que lo que vos necesitas cobrar para empatarle.
        penalizacion = ((ganador - ptw)
                        if (ganador is not None and ptw is not None) else None)

        queda = None
        if ptw is not None and sku in envio_fijo:
            queda = ptw * (1 - tasa_comision.get(sku, 0.0)) - envio_fijo[sku]

        if estado_api == "winning":
            diag = "ganando"
        elif estado_api == "sharing_first_place":
            diag = "compartiendo"
        elif estado_api == "not_listed":
            diag = "no compite"
        elif estado_api == "error":
            diag = "sin dato"
        elif (actual is not None and ganador is not None
              and actual <= ganador):
            # Ya sos mas barato y perdes igual: el problema no es el precio.
            diag = "perdés estando más barato"
        elif bajar_pct is not None and bajar_pct <= BAJA_CHICA:
            diag = "alcanza con bajar poco"
        elif bajar_pct is not None and bajar_pct >= BAJA_GRANDE:
            diag = "habría que bajar mucho"
        else:
            diag = "perdés por precio"

        filas.append({
            "item_id": p["id"],
            "sku": sku,
            "titulo": (p.get("title") or "")[:60],
            "diagnostico": diag,
            "precio_actual": actual,
            "precio_para_ganar": ptw,
            "precio_ganador": ganador,
            "bajar": bajar,
            "bajar_pct": bajar_pct,
            "penalizacion_palancas": penalizacion,
            "queda_al_precio_para_ganar": queda,
            "palancas_sin_usar": ", ".join(libres),
            "palancas_activas": ", ".join(usadas),
            "competidores_primeros": d.get("competitors_sharing_first_place"),
            "share_de_visitas": d.get("visit_share"),
            "unidades": (unidades or {}).get(sku, 0),
            "vendidas_historico": p.get("sold_quantity") or 0,
            "producto_catalogo": p.get("catalog_product_id"),
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df

    orden = {"perdés estando más barato": 0, "alcanza con bajar poco": 1,
             "perdés por precio": 2, "habría que bajar mucho": 3,
             "compartiendo": 4, "ganando": 5, "no compite": 6, "sin dato": 7}
    df["_orden"] = df["diagnostico"].map(orden)
    df = df.sort_values(["_orden", "unidades", "vendidas_historico"],
                        ascending=[True, False, False]).drop(columns=["_orden"])
    return df


def resumen(df):
    if not len(df):
        return {}
    perdiendo = df[df["diagnostico"].isin(
        ["perdés estando más barato", "alcanza con bajar poco",
         "perdés por precio", "habría que bajar mucho"])]
    return {
        "publicaciones": len(df),
        "ganando": int((df["diagnostico"] == "ganando").sum()),
        "compartiendo": int((df["diagnostico"] == "compartiendo").sum()),
        "perdiendo": len(perdiendo),
        "mas_barato_y_perdiendo": int(
            (df["diagnostico"] == "perdés estando más barato").sum()),
        "baja_chica": int((df["diagnostico"] == "alcanza con bajar poco").sum()),
        "unidades_perdiendo": int(perdiendo["unidades"].sum()),
        "penalizacion_mediana": float(
            perdiendo["penalizacion_palancas"].median())
        if perdiendo["penalizacion_palancas"].notna().any() else None,
    }


def main():
    tope = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    ml = Meli(verbose=False)

    import rentabilidad as rent
    print("Trayendo cargos reales por SKU...")
    try:
        ordenes = rent.traer_historico(ml, 90)
        envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
        cargos = rent.cargos_por_sku(ordenes, envios)
        unidades = dict(zip(cargos["sku"], cargos["unidades_vendidas"]))
    except MeliError:
        cargos, unidades = None, None

    df = analizar(ml, tope=tope, cargos=cargos, unidades=unidades,
                  callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)

    if not len(df):
        print("No hay publicaciones de catálogo.")
        return 0

    r = resumen(df)
    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("=" * 74)
    print("BUY BOX DEL CATÁLOGO")
    print("=" * 74)
    print(f"  Publicaciones de catálogo   {r['publicaciones']:>6}")
    print(f"  Ganando                     {r['ganando']:>6}")
    print(f"  Compartiendo primer lugar   {r['compartiendo']:>6}")
    print(f"  Perdiendo                   {r['perdiendo']:>6}")
    print(f"    ... estando más barato    {r['mas_barato_y_perdiendo']:>6}  <- no es precio")
    print(f"    ... con bajar poco        {r['baja_chica']:>6}  <- lo más fácil")
    print(f"  Unidades que venden las que pierden: {r['unidades_perdiendo']:>6}")
    if r["penalizacion_mediana"] is not None:
        print(f"  Penalización mediana por falta de palancas: "
              f"{pes(r['penalizacion_mediana'])}")

    facil = df[df["diagnostico"] == "alcanza con bajar poco"]
    if len(facil):
        print(f"\n  LO MÁS FÁCIL — bajar menos de {BAJA_CHICA:.0%} y ganar "
              f"({len(facil)}):")
        for _, f in facil.head(10).iterrows():
            print(f"    {f['item_id']}  {pes(f['precio_actual'])} -> "
                  f"{pes(f['precio_para_ganar'])} ({f['bajar_pct']:.1%}) · "
                  f"{int(f['unidades'])} u")
            print(f"       {f['titulo']}")
            if pd.notna(f["queda_al_precio_para_ganar"]):
                print(f"       queda por unidad antes del costo: "
                      f"{pes(f['queda_al_precio_para_ganar'])}")

    barato = df[df["diagnostico"] == "perdés estando más barato"]
    if len(barato):
        print(f"\n  PERDÉS ESTANDO MÁS BARATO ({len(barato)}) — acá bajar el "
              f"precio no sirve:")
        for _, f in barato.head(10).iterrows():
            print(f"    {f['item_id']}  vos {pes(f['precio_actual'])} vs "
                  f"ganador {pes(f['precio_ganador'])} · {int(f['unidades'])} u")
            print(f"       {f['titulo']}")
            print(f"       te falta: {f['palancas_sin_usar'] or '—'}")

    df.to_csv(DIR / "buybox.csv", index=False)
    print(f"\nGuardado en buybox.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
