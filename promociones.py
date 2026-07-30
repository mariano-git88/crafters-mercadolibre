#!/usr/bin/env python3
"""
Promociones que MercadoLibre te ofrece y no estas tomando.

    python promociones.py            -> las 300 publicaciones que mas venden
    python promociones.py 800

MercadoLibre le ofrece a cada publicacion un menu de campañas: ofertas
relampago, campañas de temporada, descuentos sugeridos y una que se llama
literalmente **"¡Ganale a la competencia!"**. Cada oferta aparece como
`candidate` hasta que la tomas.

**Lo que hay que buscar es el aporte de ML.** En algunos tipos (`SMART`,
`PRICE_MATCHING`) la respuesta trae `meli_percentage` y `seller_percentage`: es
el descuento repartido. Cuando `meli_percentage` es mayor que cero,
MercadoLibre esta poniendo plata de su bolsillo para bajar el precio al
comprador. Esas son las que hay que mirar primero — el precio le baja al
comprador mas de lo que te cuesta a vos.

**El enganche con el Buy Box.** El tipo `PRICE_MATCHING` es la respuesta
directa a las publicaciones donde perdes por precio: en vez de bajar el precio
vos solo, ML cofinancia la baja. Antes de bajar un precio a mano por el Buy
Box, conviene mirar si esa publicacion tiene un PRICE_MATCHING disponible
(ver `buybox.py`).

Sobre los precios: en los tipos donde `price` viene en cero (`PRICE_DISCOUNT`,
`DEAL`) MercadoLibre no fija el precio, te da un rango y una sugerencia — ahi
se usa `suggested_discounted_price`. En los demas el precio ya viene cerrado.

Lo que queda por unidad se calcula con los cargos reales del SKU (comision y
envio medidos de las ventas). Es **antes del costo de la mercaderia**: sirve
para descartar las promos que directamente dan negativo.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Tipos donde ML puede poner parte del descuento.
CON_APORTE_ML = ("SMART", "PRICE_MATCHING")

NOMBRES = {
    "LIGHTNING": "Oferta relámpago",
    "DEAL": "Campaña de temporada",
    "PRICE_DISCOUNT": "Descuento sugerido",
    "SMART": "Descuento inteligente",
    "PRICE_MATCHING": "¡Ganale a la competencia!",
    "SELLER_CAMPAIGN": "Campaña propia",
    "UNHEALTHY_STOCK": "Stock no saludable",
    "MARKETPLACE_CAMPAIGN": "Campaña de MercadoLibre",
    "PRE_NEGOTIATED": "Precio prenegociado",
}


def campanas_disponibles(ml):
    """Las campañas abiertas a nivel cuenta."""
    try:
        r = ml.get(f"/seller-promotions/users/{ml.user_id}", app_version="v2")
    except MeliError:
        return pd.DataFrame()
    filas = [{
        "id": c.get("id"),
        "tipo": c.get("type"),
        "nombre_tipo": NOMBRES.get(c.get("type"), c.get("type")),
        "nombre": c.get("name") or "",
        "estado": c.get("status"),
        "desde": (c.get("start_date") or "")[:10],
        "hasta": (c.get("finish_date") or "")[:10],
        "cierra_inscripcion": (c.get("deadline_date") or "")[:10],
    } for c in (r.get("results") or [])]
    return pd.DataFrame(filas)


def _precio_promo(p):
    """El precio con promo. Si ML no lo fija, usa el sugerido."""
    precio = p.get("price") or 0
    if precio > 0:
        return float(precio)
    sugerido = p.get("suggested_discounted_price")
    return float(sugerido) if sugerido else None


def analizar(ml, pubs=None, tope=300, cargos=None, unidades=None,
             callback=None):
    """
    Devuelve (df_ofertas, df_campanas).

    Es una llamada por publicacion, asi que `tope` corta por las que mas
    venden. Con el catalogo entero (2.275 activas) son unos 12 minutos.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    act = [p for p in pubs if p.get("status") == "active"]

    # `sold_quantity` es el historico de toda la vida de la publicacion: sirve
    # para ordenar cuando no hay dato del periodo, pero no se mezcla con las
    # unidades del periodo en la misma columna.
    def peso(p):
        sku = (sku_del_atributo(p) or "").strip().upper()
        if unidades and sku in unidades:
            return (1, unidades[sku])
        return (0, p.get("sold_quantity") or 0)

    act.sort(key=peso, reverse=True)
    if tope:
        act = act[:tope]

    tasa_comision, envio_fijo = {}, {}
    if cargos is not None and len(cargos):
        for _, f in cargos.iterrows():
            precio = f["precio_prom"] or 0
            if precio > 0:
                tasa_comision[f["sku"]] = f["comision_prom"] / precio
            envio_fijo[f["sku"]] = f["envio_prom"] or 0.0

    filas = []
    for n, p in enumerate(act, start=1):
        try:
            ofertas = ml.get(f"/seller-promotions/items/{p['id']}",
                             app_version="v2")
        except MeliError:
            ofertas = []
        if callback and n % 25 == 0:
            callback(f"Promociones {n}/{len(act)}...")

        sku = (sku_del_atributo(p) or "").strip().upper()
        for o in (ofertas or []):
            tipo = o.get("type")
            precio_promo = _precio_promo(o)
            original = o.get("original_price") or p.get("price")
            descuento = ((original - precio_promo) / original
                         if (precio_promo and original) else None)

            aporte_ml = o.get("meli_percentage")
            aporte_vend = o.get("seller_percentage")

            queda = None
            if precio_promo is not None and sku in envio_fijo:
                queda = (precio_promo * (1 - tasa_comision.get(sku, 0.0))
                         - envio_fijo[sku])

            if o.get("status") == "started":
                diag = "ya participás"
            elif tipo in CON_APORTE_ML and (aporte_ml or 0) > 0:
                diag = "ML pone parte"
            elif queda is not None and queda <= 0:
                diag = "da negativo"
            else:
                diag = "disponible"

            filas.append({
                "item_id": p["id"],
                "sku": sku,
                "titulo": (p.get("title") or "")[:60],
                "campana_id": o.get("id") or "",
                "tipo": tipo,
                "promocion": NOMBRES.get(tipo, tipo),
                "nombre": o.get("name") or "",
                "estado": o.get("status"),
                "diagnostico": diag,
                "precio_actual": original,
                "precio_promo": precio_promo,
                "descuento": descuento,
                "aporte_ml": (aporte_ml / 100) if aporte_ml else None,
                "aporte_vendedor": (aporte_vend / 100) if aporte_vend else None,
                "queda_por_unidad": queda,
                "unidades": (unidades or {}).get(sku, 0),
                "vendidas_historico": p.get("sold_quantity") or 0,
                "desde": (o.get("start_date") or "")[:10],
                "hasta": (o.get("finish_date") or "")[:10],
            })

    df = pd.DataFrame(filas)
    if len(df):
        # ML devuelve la misma oferta repetida en algunos items (mismo tipo,
        # mismo precio, misma campaña). Se muestra una sola vez.
        df = df.drop_duplicates(
            subset=["item_id", "campana_id", "tipo", "precio_promo"])
        orden = {"ML pone parte": 0, "disponible": 1, "ya participás": 2,
                 "da negativo": 3}
        df["_orden"] = df["diagnostico"].map(orden)
        df = df.sort_values(["_orden", "unidades", "vendidas_historico"],
                            ascending=[True, False, False]).drop(columns=["_orden"])

    return df, campanas_disponibles(ml)


def resumen(df):
    if not len(df):
        return {}
    return {
        "ofertas": len(df),
        "publicaciones": int(df["item_id"].nunique()),
        "con_aporte_ml": int((df["diagnostico"] == "ML pone parte").sum()),
        "disponibles": int((df["diagnostico"] == "disponible").sum()),
        "participando": int((df["diagnostico"] == "ya participás").sum()),
        "negativas": int((df["diagnostico"] == "da negativo").sum()),
        "por_tipo": Counter(df["promocion"]),
    }


def main():
    tope = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
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

    df, camp = analizar(ml, tope=tope, cargos=cargos, unidades=unidades,
                        callback=lambda m: print(f"  {m}", end="\r"))
    print(" " * 70)

    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    if len(camp):
        print("=" * 74)
        print("CAMPAÑAS ABIERTAS EN LA CUENTA")
        print("=" * 74)
        for _, c in camp.iterrows():
            print(f"  {c['nombre_tipo']:<28} {c['estado']:<10} "
                  f"{c['nombre'][:32]}")
            if c["hasta"]:
                print(f"     hasta {c['hasta']} · inscripción hasta "
                      f"{c['cierra_inscripcion']}")

    if not len(df):
        print("\nSin ofertas disponibles.")
        return 0

    r = resumen(df)
    print("\n" + "=" * 74)
    print(f"OFERTAS POR PUBLICACIÓN  (top {tope} por ventas)")
    print("=" * 74)
    print(f"  Publicaciones con ofertas  {r['publicaciones']:>6}")
    print(f"  Ofertas en total           {r['ofertas']:>6}")
    print(f"  Con aporte de ML           {r['con_aporte_ml']:>6}  <- mirar primero")
    print(f"  Disponibles sin tomar      {r['disponibles']:>6}")
    print(f"  Ya participando            {r['participando']:>6}")
    print(f"  Dan negativo               {r['negativas']:>6}")

    print("\n  Por tipo:")
    for k, n in r["por_tipo"].most_common():
        print(f"    {k:<30} {n:>5}")

    con_ml = df[df["diagnostico"] == "ML pone parte"]
    if len(con_ml):
        print(f"\n  DONDE MERCADOLIBRE PONE PARTE ({len(con_ml)}):")
        for _, f in con_ml.head(12).iterrows():
            print(f"    {f['item_id']}  {f['promocion']}  "
                  f"{pes(f['precio_actual'])} -> {pes(f['precio_promo'])}")
            print(f"       {f['titulo']}")
            print(f"       pone ML {f['aporte_ml']:.1%} · ponés vos "
                  f"{f['aporte_vendedor']:.1%} · te queda "
                  f"{pes(f['queda_por_unidad'])} · {int(f['unidades'])} u")

    df.to_csv(DIR / "promociones.csv", index=False)
    print(f"\nGuardado en promociones.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
