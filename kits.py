#!/usr/bin/env python3
"""
Armar kits: que productos conviene vender juntos.

    python kits.py            -> propone kits con 120 dias de historia
    python kits.py 180        -> con otra ventana

Dos fuentes, y la segunda es la que da alcance:

**Lo que la gente ya compra junto.** Evidencia directa, pero solo cubre los
productos que tienen historia.

**La afinidad entre categorias.** Si Pistolas Encoladoras y Barras de Silicona
se compran juntas mucho mas de lo esperable, entonces *cualquier* pistola con
*cualquier* barra es un kit razonable, aunque ese par puntual nunca se haya
vendido junto. Es lo que permite proponer kits para productos sin historia.

--------------------------------------------------------------------------
Las dos trampas que hacen que esto de cero si no se respetan
--------------------------------------------------------------------------

**MercadoLibre parte el carrito en varias ordenes.** Medido sobre 120 dias:
7.840 ordenes no canceladas y **todas tienen exactamente un producto**. La
compra real es el `pack_id`: agrupando por ahi aparecen 6.872 canastas, 585 de
ellas con 2+ productos. Contar por orden da cero pares, no "pocos".

**Hay que contar por SKU, no por publicacion.** El mismo producto vive en
varias publicaciones (espejos, catalogo, Premium/Clasica): sin agrupar, un kit
consigo mismo parece un hallazgo.

**Y hay que mirar el `lift`, no las veces.** `Pegamentos + Burletes` aparece 41
veces —lo mas frecuente de todo— con lift **0,8**: se cruzan menos de lo
esperable por azar, solo porque son nuestras dos categorias mas grandes.
Ordenar por frecuencia arma el kit equivocado.

--------------------------------------------------------------------------
Publicar el kit
--------------------------------------------------------------------------

**No se puede por API.** El panel lo arma en
`vendedores.mercadolibre.com.ar/publicar/kit?pre_charged_ups={MLAU}`, o sea
**por user product**, igual que sacar del catalogo. Este modulo llega hasta la
propuesta y deja el link armado.
"""

import collections
import itertools
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError
from ventas import traer_ordenes

DIR = Path(__file__).resolve().parent

PANEL_KIT = ("https://vendedores.mercadolibre.com.ar/publicar/kit"
             "?pre_charged_ups={up}")

DIAS = 120
# Debajo de esto un par es ruido: dos coincidencias no son un patron.
MINIMO_JUNTOS = 2
# Cuanto mas seguido de lo esperable por azar. 1.0 = indiferente.
LIFT_MINIMO = 2.0
# Para las reglas de categoria hace falta mas evidencia: generalizan a todos
# los productos de esas dos categorias.
MINIMO_CATEGORIA = 3
LIFT_CATEGORIA = 2.0

# Palabras que delatan que la publicacion YA es un kit. Proponer un kit de un
# kit es armar un combo del combo: aparecio en la primera corrida con
# "Combo Toallon Y Toalla ... Set Hotelero" + "Toallon".
YA_ES_KIT = ("combo", "kit", " set ", "set ", "pack", "x 2 uni", "x 3 uni",
             "x2 uni", "x3 uni", "unidades")


def _sku(p):
    return (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip().upper()


def _es_kit(titulo):
    t = f" {(titulo or '').lower()} "
    return any(k in t for k in YA_ES_KIT)


def _puntaje(veces, lift):
    """
    Con que se ordenan las propuestas.

    **El lift solo no sirve**: con 2 coincidencias da numeros enormes por puro
    azar (aparecio un par con lift 275 sobre 2 compras). Se pondera por la
    evidencia, asi un par de 4 compras con lift 137 le gana a uno de 2 con
    lift 275.
    """
    import math
    return round(lift * math.log1p(veces), 1)


def canastas(ml, dias=DIAS, callback=None):
    """
    Las compras reales de los ultimos `dias`, como conjuntos de SKU.

    **Agrupa por `pack_id`, no por orden.** ML parte el carrito en una orden
    por producto; sin esto, todas las canastas tienen un solo item.
    """
    hasta = datetime.now()
    desde = hasta - timedelta(days=dias)
    if callback:
        callback(f"bajando ventas desde {desde.date()}...")
    ordenes = traer_ordenes(ml, desde, hasta)

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    de_sku = {p["id"]: _sku(p) for p in pubs}

    juntos = collections.defaultdict(set)
    for o in ordenes:
        if o.get("status") == "cancelled":
            continue
        pack = o.get("pack_id") or o.get("id")
        for it in (o.get("order_items") or []):
            iid = (it.get("item") or {}).get("id")
            s = de_sku.get(iid)
            if s:
                juntos[pack].add(s)
    return [c for c in juntos.values() if len(c) > 1]


def _reglas(canastas_, clave_de=None, minimo=MINIMO_JUNTOS, lift_min=LIFT_MINIMO):
    """
    Pares que aparecen juntos mas de lo esperable.

    `clave_de` permite subir el analisis de SKU a categoria sin duplicar
    codigo: es la funcion que mapea un SKU a la clave con la que agrupar.
    """
    grupos = []
    for c in canastas_:
        k = {clave_de(s) for s in c} if clave_de else set(c)
        k = {x for x in k if x}
        if len(k) > 1:
            grupos.append(k)

    n = len(grupos)
    if not n:
        return pd.DataFrame(), 0
    solo = collections.Counter()
    duo = collections.Counter()
    for g in grupos:
        for x in g:
            solo[x] += 1
        for a, b in itertools.combinations(sorted(g), 2):
            duo[(a, b)] += 1

    filas = []
    for (a, b), veces in duo.items():
        if veces < minimo:
            continue
        esperado = (solo[a] / n) * (solo[b] / n)
        lift = (veces / n) / esperado if esperado else 0
        if lift < lift_min:
            continue
        filas.append({
            "a": a, "b": b, "juntos": veces,
            "lift": round(lift, 1),
            # De los que compraron A, cuantos se llevaron B (y al reves).
            "confianza": round(max(veces / solo[a], veces / solo[b]), 2),
        })
    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["lift", "juntos"], ascending=False)
    return df.reset_index(drop=True), n


def proponer(ml=None, dias=DIAS, canastas_=None, callback=None):
    """
    Los kits candidatos, de la evidencia mas fuerte a la mas floja.

    Devuelve una fila por kit con los dos productos, por que se propone, y el
    link del panel para crearlo.
    """
    if canastas_ is None:
        canastas_ = canastas(ml, dias, callback)

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    activas = [p for p in pubs if p.get("status") == "active"]

    # Por SKU: la publicacion mas cara manda como representante (suele ser la
    # que tiene mejor titulo y foto), y de ahi salen precio y user product.
    mejor = {}
    for p in sorted(activas, key=lambda x: x.get("price") or 0, reverse=True):
        s = _sku(p)
        if s:
            mejor.setdefault(s, p)
    cat_de = {s: p.get("category_id") for s, p in mejor.items()}
    vendidas = {s: (p.get("sold_quantity") or 0) for s, p in mejor.items()}

    if callback:
        callback("buscando lo que ya se compra junto...")
    por_sku, n_can = _reglas(canastas_)
    if callback:
        callback("buscando afinidad entre categorías...")
    por_cat, _ = _reglas(canastas_, lambda s: cat_de.get(s),
                         MINIMO_CATEGORIA, LIFT_CATEGORIA)

    nombres = {}

    def cat_nombre(c):
        if c not in nombres and ml is not None:
            try:
                nombres[c] = ml.get(f"/categories/{c}").get("name", c)
            except MeliError:
                nombres[c] = c
        return nombres.get(c, c)

    filas, ya = [], set()

    def agregar(a, b, origen, motivo, fuerza):
        par = tuple(sorted((a, b)))
        if par in ya or a == b:
            return
        pa, pb = mejor.get(a), mejor.get(b)
        if not pa or not pb:
            return
        if _es_kit(pa.get("title")) or _es_kit(pb.get("title")):
            return          # no armar un kit de un kit
        ya.add(par)
        suma = (pa.get("price") or 0) + (pb.get("price") or 0)
        filas.append({
            "origen": origen, "fuerza": fuerza, "motivo": motivo,
            "sku_a": a, "producto_a": (pa.get("title") or "")[:52],
            "sku_b": b, "producto_b": (pb.get("title") or "")[:52],
            "precio_a": pa.get("price"), "precio_b": pb.get("price"),
            "precio_suma": round(suma, 2),
            # 10% es el gancho tipico de un kit; queda editable.
            "precio_kit_sugerido": round(suma * 0.9, 2),
            "vendidas_a": vendidas.get(a, 0), "vendidas_b": vendidas.get(b, 0),
            "item_a": pa["id"], "item_b": pb["id"],
            "user_product_a": pa.get("user_product_id") or "",
            "crear_kit": PANEL_KIT.format(up=pa.get("user_product_id") or ""),
        })

    # 1) Evidencia directa.
    for _, r in por_sku.iterrows():
        agregar(r["a"], r["b"], "se compran juntos",
                f"{int(r['juntos'])} compras juntas, {r['lift']}× más de lo "
                f"esperable, confianza {r['confianza']:.0%}",
                _puntaje(int(r["juntos"]), float(r["lift"])))

    # 2) Generalizacion por categoria: cubre los que no tienen historia.
    for _, r in por_cat.iterrows():
        ca, cb = r["a"], r["b"]
        de_a = sorted([s for s, c in cat_de.items() if c == ca],
                      key=lambda s: -vendidas.get(s, 0))[:6]
        de_b = sorted([s for s, c in cat_de.items() if c == cb],
                      key=lambda s: -vendidas.get(s, 0))[:6]
        motivo = (f"«{cat_nombre(ca)}» y «{cat_nombre(cb)}» se compran juntas "
                  f"{r['lift']}× más de lo esperable ({int(r['juntos'])} veces)")
        for a in de_a:
            for b in de_b:
                agregar(a, b, "categorías afines", motivo, float(r["lift"]))

    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["origen", "fuerza"], ascending=[True, False])
    return df.reset_index(drop=True)


def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else DIAS
    ml = Meli(verbose=False)
    df = proponer(ml, dias, callback=lambda m: print(f"  {m}"))
    if not len(df):
        print("\nNo hay suficiente historia para proponer kits.")
        return 0
    print(f"\n{len(df)} kits propuestos\n")
    for origen, g in df.groupby("origen", sort=False):
        print(f"=== {origen} ({len(g)}) ===")
        for _, r in g.head(8).iterrows():
            print(f"  {r['producto_a'][:40]}")
            print(f"  + {r['producto_b'][:40]}")
            print(f"    {r['motivo']}")
            print(f"    ${r['precio_suma']:,.0f} → kit ${r['precio_kit_sugerido']:,.0f}"
                  .replace(",", "."))
        print()
    df.to_csv(DIR / "kits_propuestos.csv", index=False)
    print("Guardado en kits_propuestos.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
