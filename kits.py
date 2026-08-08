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
from tramos import cargo_fijo, envio_a_cargo
from ventas import traer_ordenes

DIR = Path(__file__).resolve().parent

CACHE_CANASTAS = DIR / "canastas_kits.json"

PANEL_KIT = ("https://vendedores.mercadolibre.com.ar/publicar/kit"
             "?pre_charged_ups={up}")

DIAS = 365          # 12 meses: con 120 dias solo salian pares
# Debajo de esto un par es ruido: dos coincidencias no son un patron.
MINIMO_JUNTOS = 2
# Cuanto mas seguido de lo esperable por azar. 1.0 = indiferente.
LIFT_MINIMO = 2.0
# Para las reglas de categoria hace falta mas evidencia: generalizan a todos
# los productos de esas dos categorias.
MINIMO_CATEGORIA = 3
LIFT_CATEGORIA = 2.0

# Hasta cuantos productos puede tener un kit.
MAX_PRODUCTOS = 4

# Multipacks del mismo SKU: 2, 3 o 4 unidades.
UNIDADES_MULTIPACK = (2, 3, 4)

# Cuanto se puede descontar como maximo, decidido por Mariano el 8/8/2026.
# Nuestras marcas se cuidan mas: el precio del kit es tambien una señal de
# cuanto vale la marca. **Es un techo, no un objetivo**: si la economia del
# kit no banca ni eso, manda lo que banca.
MARCAS_PROPIAS = ("suprabond", "bulit", "somerset")
TOPE_PROPIAS = 0.15
TOPE_OTRAS = 0.25

# Palabras que delatan que la publicacion YA es un kit. Proponer un kit de un
# kit es armar un combo del combo: aparecio en la primera corrida con
# "Combo Toallon Y Toalla ... Set Hotelero" + "Toallon".
YA_ES_KIT = ("combo", "kit", " set ", "set ", "pack", "x 2 uni", "x 3 uni",
             "x2 uni", "x3 uni", "unidades")


def _sku(p):
    return (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip().upper()


def _marca(pub):
    for a in (pub.get("attributes") or []):
        if a.get("id") == "BRAND":
            return (a.get("value_name") or "").strip()
    return ""


def tope_descuento(pubs_del_kit):
    """
    El techo de descuento del kit segun las marcas que lo componen.

    Si mezcla, manda **la mas restrictiva**: alcanza con que una sea nuestra
    para que el kit no pueda descontarse mas del 15%.
    """
    for p in pubs_del_kit:
        if _marca(p).lower() in MARCAS_PROPIAS:
            return TOPE_PROPIAS
    return TOPE_OTRAS


def tipo_de_publicacion(pubs_del_kit):
    """
    Clasica o Premium. Si todos coinciden, ese; **si hay mezcla, Clasica**,
    que es la barata: la Premium paga ~12 puntos mas de comision y no tiene
    sentido pagarlos por arrastre de un solo componente.
    """
    tipos = {p.get("listing_type_id") for p in pubs_del_kit}
    if tipos == {"gold_pro"}:
        return "gold_pro"
    return "gold_special"


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


def canastas(ml, dias=DIAS, callback=None, refrescar=False):
    """
    Las compras reales de los ultimos `dias`, como conjuntos de SKU.

    **Agrupa por `pack_id`, no por orden.** ML parte el carrito en una orden
    por producto; sin esto, todas las canastas tienen un solo item.

    Se cachea: bajar 12 meses son ~25.000 ordenes y varios minutos, y la app
    no puede hacer eso en cada corrida.
    """
    if CACHE_CANASTAS.exists() and not refrescar:
        d = json.loads(CACHE_CANASTAS.read_text(encoding="utf-8"))
        if d.get("dias") == dias:
            return [set(c) for c in d["canastas"]]

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
    salida = [c for c in juntos.values() if len(c) > 1]
    CACHE_CANASTAS.write_text(json.dumps(
        {"dias": dias, "bajado": hasta.strftime("%Y-%m-%d %H:%M"),
         "canastas": [sorted(c) for c in salida]}, ensure_ascii=False),
        encoding="utf-8")
    return salida


def cuando_se_bajo():
    """Cuando se bajaron las canastas, para mostrarlo en la app."""
    if not CACHE_CANASTAS.exists():
        return None
    try:
        return json.loads(CACHE_CANASTAS.read_text(encoding="utf-8")).get("bajado")
    except (OSError, json.JSONDecodeError):
        return None


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


# ------------------------------------------------------------------ economia

def _comision_variable(precio_prom, comision_prom):
    """
    El porcentaje de comision SIN el cargo fijo.

    `comision_prom` mezcla las dos cosas. Restar el cargo fijo del tramo en que
    se vendio deja el porcentaje, que es lo unico que escala con el precio.
    """
    if not precio_prom:
        return None
    variable = (comision_prom or 0) - cargo_fijo(precio_prom)
    return max(variable / precio_prom, 0.0)


def economia(precios, pct, precio_kit=None):
    """
    Que se ahorra vendiendo junto en vez de por separado.

    **El ahorro real es el cargo fijo, no la comision**: la comision es un
    porcentaje y da igual cobrarla en una venta o en tres. El cargo fijo se
    paga **por venta**, asi que un pack de tres paga uno en vez de tres.

    **Pero cruzar $33.000 lo da vuelta.** Ahi el cargo fijo se hace cero y
    aparecen ~$7.641 de envio a cargo nuestro. Un pack que cruza el umbral
    puede costar mas que vender suelto — es la misma trampa de
    `tramos.cruzar_escalon`, ahora del lado del armado.

    Devuelve el detalle y el ahorro (positivo = conviene el kit).
    """
    suma = sum(precios)
    kit = precio_kit if precio_kit is not None else suma

    # Por separado: cada uno paga SU cargo fijo y SU envio.
    fijo_sep = sum(cargo_fijo(x) for x in precios)
    envio_sep = sum(envio_a_cargo(x) for x in precios)
    var_sep = sum(x * pct for x in precios)

    # Como kit: una sola venta.
    fijo_kit = cargo_fijo(kit)
    envio_kit = envio_a_cargo(kit)
    var_kit = kit * pct

    costo_sep = fijo_sep + envio_sep + var_sep
    costo_kit = fijo_kit + envio_kit + var_kit
    return {
        "precio_suelto": round(suma, 2),
        "precio_kit": round(kit, 2),
        "cargo_fijo_suelto": round(fijo_sep, 2),
        "cargo_fijo_kit": round(fijo_kit, 2),
        "envio_suelto": round(envio_sep, 2),
        "envio_kit": round(envio_kit, 2),
        "costo_ml_suelto": round(costo_sep, 2),
        "costo_ml_kit": round(costo_kit, 2),
        # Lo que queda para financiar el descuento sin perder plata.
        "ahorro": round(costo_sep - costo_kit, 2),
        "cruza_umbral": bool(cargo_fijo(kit) == 0 and fijo_sep > 0),
    }


def descuento_que_banca(precios, pct, margen_extra=0.0):
    """
    Cuanto se puede descontar sin ganar menos que vendiendo suelto.

    Es el ahorro convertido en porcentaje del precio. Poner mas que esto es
    financiar el kit del propio bolsillo, que puede estar bien como gancho
    pero **tiene que ser una decision, no un descuido**.
    """
    e = economia(precios, pct)
    suma = e["precio_suelto"]
    if not suma:
        return 0.0
    # El descuento tambien baja la comision variable, asi que se banca un poco
    # mas que el ahorro nominal.
    return max((e["ahorro"] + margen_extra) / (suma * (1 - pct)), 0.0)


def multipacks(pubs=None, cargos=None, unidades=UNIDADES_MULTIPACK):
    """
    Packs del MISMO producto: 2, 3 o 4 unidades.

    No necesitan historia de compra conjunta: la razon es puramente economica
    —un pack paga un cargo fijo en vez de N— y por eso cubren productos que
    nunca se vendieron acompañados.

    Para cada SKU se prueban las cantidades y **se elige la que mas ahorra**,
    descartando las que cruzan los $33.000.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    activas = [p for p in pubs if p.get("status") == "active"]
    mejor = {}
    for p in sorted(activas, key=lambda x: x.get("price") or 0, reverse=True):
        s = _sku(p)
        if s:
            mejor.setdefault(s, p)

    pcts = {}
    if cargos is not None and len(cargos):
        for _, r in cargos.iterrows():
            v = _comision_variable(r.get("precio_prom"), r.get("comision_prom"))
            if v is not None:
                pcts[str(r["sku"]).strip().upper()] = v
    pct_tipico = (sum(pcts.values()) / len(pcts)) if pcts else 0.16

    filas = []
    for s, p in mejor.items():
        precio = p.get("price") or 0
        if not precio or _es_kit(p.get("title")):
            continue
        pct = pcts.get(s, pct_tipico)
        opciones = []
        for n in unidades:
            e = economia([precio] * n, pct)
            if e["cruza_umbral"]:
                continue          # pasarse de $33.000 mete el envio
            opciones.append((e["ahorro"], n, e))
        if not opciones:
            continue
        ahorro, n, e = max(opciones)
        if ahorro <= 0:
            continue
        # El descuento es el MENOR entre lo que la economia banca y el techo
        # de la marca: nunca regalar mas de lo que el kit ahorra, ni mas de lo
        # que la marca tolera.
        banca = descuento_que_banca([precio] * n, pct)
        tope = tope_descuento([p])
        desc = min(banca, tope)
        ahorro_fijo = e["cargo_fijo_suelto"] - e["cargo_fijo_kit"]
        ahorro_envio = e["envio_suelto"] - e["envio_kit"]
        # De donde sale el ahorro cambia cuanto confiar en el:
        #  - el CARGO FIJO se paga por venta y se ahorra siempre.
        #  - el ENVIO se ahorra solo si el comprador, sin el pack, hubiera
        #    hecho N compras separadas. Si igual se las llevaba todas juntas
        #    en un carrito, ML ya cobraba un envio solo. Es real, pero apoyado
        #    en un supuesto.
        de_donde = ("cargo fijo" if ahorro_envio <= 0 else
                    "envío" if ahorro_fijo <= 0 else "cargo fijo y envío")
        pes = lambda v: f"${v:,.0f}".replace(",", ".")
        filas.append({
            "origen": "multipack", "unidades": n, "sku": s,
            "ahorro_de": de_donde,
            "ahorro_cargo_fijo": round(ahorro_fijo, 2),
            "ahorro_envio": round(ahorro_envio, 2),
            "supuesto": ("" if ahorro_envio <= 0 else
                         "el ahorro de envío supone que, sin el pack, serían "
                         "compras separadas"),
            "producto": (p.get("title") or "")[:60],
            "precio_unidad": precio,
            "precio_suelto": e["precio_suelto"],
            "ahorro_ml": e["ahorro"],
            "descuento_que_banca": round(banca, 4),
            "tope_marca": tope,
            "marca": _marca(p),
            "descuento": round(desc, 4),
            "limita": "la marca" if tope < banca else "la economía del kit",
            "tipo_publicacion": tipo_de_publicacion([p]),
            "precio_kit_sugerido": round(e["precio_suelto"] * (1 - desc), 2),
            "motivo": (
                f"{n} unidades en una venta ahorran {pes(e['ahorro'])} de "
                + (f"cargo fijo ({pes(e['cargo_fijo_suelto'])} → "
                   f"{pes(e['cargo_fijo_kit'])})" if de_donde == "cargo fijo"
                   else f"envío ({pes(e['envio_suelto'])} → "
                        f"{pes(e['envio_kit'])})" if de_donde == "envío"
                   else f"cargo fijo y envío")),
            "vendidas": p.get("sold_quantity") or 0,
            "item": p["id"], "user_product": p.get("user_product_id") or "",
            "crear_kit": PANEL_KIT.format(up=p.get("user_product_id") or ""),
        })
    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["ahorro_ml", "vendidas"], ascending=False)
    return df.reset_index(drop=True)


def conjuntos(canastas_, hasta=MAX_PRODUCTOS, minimo=MINIMO_JUNTOS):
    """
    Grupos de 2, 3 y 4 productos que se compran juntos mas de lo esperable.

    Crece por niveles: un trio solo se prueba si sus tres pares ya pasaron el
    corte. Sin esa poda habria que evaluar todas las combinaciones de 700 SKU
    —millones— y la mayoria no aparece nunca.

    El `lift` de un grupo se compara contra lo que se esperaria si los
    productos fueran independientes: multiplicar sus frecuencias.
    """
    n = len(canastas_)
    if not n:
        return pd.DataFrame()

    solo = collections.Counter()
    for c in canastas_:
        for x in c:
            solo[x] += 1

    filas = []
    # Nivel 2: todos los pares.
    actual = collections.Counter()
    for c in canastas_:
        for par in itertools.combinations(sorted(c), 2):
            actual[par] += 1
    vivos = {g for g, v in actual.items() if v >= minimo}

    tam = 2
    while vivos and tam <= hasta:
        for g in vivos:
            veces = actual[g]
            esperado = 1.0
            for x in g:
                esperado *= solo[x] / n
            lift = (veces / n) / esperado if esperado else 0
            if lift < LIFT_MINIMO:
                continue
            filas.append({"productos": tam, "skus": list(g), "juntos": veces,
                          "lift": round(lift, 1),
                          "confianza": round(
                              veces / min(solo[x] for x in g), 2)})
        if tam == hasta:
            break
        # Nivel siguiente: solo combinaciones cuyos sub-grupos sobrevivieron.
        candidatos = collections.Counter()
        for c in canastas_:
            ordenado = sorted(c)
            if len(ordenado) <= tam:
                continue
            for g in itertools.combinations(ordenado, tam + 1):
                if all(sub in vivos for sub in
                       itertools.combinations(g, tam)):
                    candidatos[g] += 1
        actual = candidatos
        vivos = {g for g, v in candidatos.items() if v >= minimo}
        tam += 1

    df = pd.DataFrame(filas)
    if len(df):
        df["puntaje"] = [_puntaje(r["juntos"], r["lift"])
                         for _, r in df.iterrows()]
        df = df.sort_values(["productos", "puntaje"], ascending=[False, False])
    return df.reset_index(drop=True)


def kits_de_varios(canastas_, cargos=None, pubs=None):
    """
    Los conjuntos de 2 a 4 productos, con precio y economia.

    Un kit de 3 o 4 productos distintos ahorra mas que uno de 2 —son tres o
    cuatro cargos fijos en vez de uno— pero tambien es mas facil que la suma
    cruce los $33.000 y aparezca el envio. Por eso cada uno se evalua.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    activas = [p for p in pubs if p.get("status") == "active"]
    mejor = {}
    for p in sorted(activas, key=lambda x: x.get("price") or 0, reverse=True):
        s = _sku(p)
        if s:
            mejor.setdefault(s, p)

    pcts = {}
    if cargos is not None and len(cargos):
        for _, r in cargos.iterrows():
            v = _comision_variable(r.get("precio_prom"), r.get("comision_prom"))
            if v is not None:
                pcts[str(r["sku"]).strip().upper()] = v
    pct_tipico = (sum(pcts.values()) / len(pcts)) if pcts else 0.16

    filas = []
    for _, g in conjuntos(canastas_).iterrows():
        skus = g["skus"]
        ps = [mejor.get(s) for s in skus]
        if any(x is None for x in ps) or any(_es_kit(x.get("title")) for x in ps):
            continue
        precios = [x.get("price") or 0 for x in ps]
        if not all(precios):
            continue
        pct = sum(pcts.get(s, pct_tipico) for s in skus) / len(skus)
        e = economia(precios, pct)
        banca = descuento_que_banca(precios, pct)
        tope = tope_descuento(ps)
        desc = min(banca, tope)
        filas.append({
            "origen": "se compran juntos", "productos": int(g["productos"]),
            "juntos": int(g["juntos"]), "lift": g["lift"],
            "confianza": g["confianza"],
            "detalle": " + ".join((x.get("title") or "")[:32] for x in ps),
            "skus": ", ".join(skus),
            "precio_suelto": e["precio_suelto"],
            "ahorro_ml": e["ahorro"],
            "ahorro_de": ("cargo fijo"
                          if e["envio_suelto"] - e["envio_kit"] <= 0
                          else "envío" if e["cargo_fijo_suelto"] -
                          e["cargo_fijo_kit"] <= 0 else "cargo fijo y envío"),
            "cruza_umbral": e["cruza_umbral"],
            "descuento_que_banca": round(banca, 4),
            "tope_marca": tope,
            "marcas": ", ".join(sorted({_marca(x) for x in ps if _marca(x)})),
            "descuento": round(desc, 4),
            "limita": "la marca" if tope < banca else "la economía del kit",
            "tipo_publicacion": tipo_de_publicacion(ps),
            "precio_kit_sugerido": round(e["precio_suelto"] * (1 - desc), 2),
            "motivo": (f"{int(g['juntos'])} compras juntas, {g['lift']}× más "
                       f"de lo esperable, confianza {g['confianza']:.0%}"),
            "items": ", ".join(x["id"] for x in ps),
            "user_product": ps[0].get("user_product_id") or "",
            "crear_kit": PANEL_KIT.format(up=ps[0].get("user_product_id") or ""),
        })
    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values(["productos", "ahorro_ml"], ascending=[False, False])
    return df.reset_index(drop=True)


def rentabilidad_del_kit(kits_df, cargos=None, costos=None):
    """
    Margen del kit contra vender los mismos productos por separado.

    Sirve sobre todo para los que **cruzan los $33.000**: ahi el kit deja de
    pagar cargo fijo pero empieza a pagar el envio, y la pregunta no es si
    ahorra —no ahorra— sino **cuanto cuesta** y si el volumen extra lo
    justifica.

    El costo del producto se toma **sin el descuento de proveedor**: se esta
    decidiendo un precio, y si se baja contando con un descuento que puede no
    estar, la venta pasa a perdida (ver `rentabilidad.costo_efectivo`).
    """
    import rentabilidad as rent

    if costos is None:
        costos, _ = rent.costos_guardados()
    cd = {}
    if costos is not None and len(costos):
        for _, r in costos.iterrows():
            try:
                cd[str(r["sku"]).strip().upper()] = float(r["costo"])
            except (TypeError, ValueError):
                pass

    pcts = {}
    if cargos is not None and len(cargos):
        for _, r in cargos.iterrows():
            v = _comision_variable(r.get("precio_prom"), r.get("comision_prom"))
            if v is not None:
                pcts[str(r["sku"]).strip().upper()] = v
    pct_tipico = (sum(pcts.values()) / len(pcts)) if pcts else 0.16

    filas = []
    for _, k in kits_df.iterrows():
        skus = [s.strip().upper() for s in str(k.get("skus", "")).split(",")
                if s.strip()]
        if not skus:
            continue
        cs = [cd.get(s) for s in skus]
        if any(c is None for c in cs):
            filas.append({"detalle": k.get("detalle"), "veredicto": "sin costo",
                          "motivo": "falta el costo de algún componente"})
            continue

        precios = []
        # El precio de cada componente sale del suelto repartido igual que en
        # la propuesta: se recalcula desde el catalogo en `kits_de_varios`.
        suelto = float(k.get("precio_suelto") or 0)
        if not suelto:
            continue
        pct = sum(pcts.get(s, pct_tipico) for s in skus) / len(skus)
        costo = sum(rent.costo_efectivo(c) for c in cs)

        # Reparto el precio suelto proporcional al costo, que es lo mas
        # cercano a los precios reales sin volver a leer el catalogo.
        total_costo = sum(cs) or 1
        precios = [suelto * (c / total_costo) for c in cs]

        e = economia(precios, pct)
        # Devuelve (detalle, total): sin el [1] se resta una tupla y revienta.
        otros_s = rent.otros_conceptos_monto(suelto)[1]
        otros_k = rent.otros_conceptos_monto(e["precio_kit"])[1]
        margen_suelto = suelto - costo - e["costo_ml_suelto"] - otros_s
        margen_kit = e["precio_kit"] - costo - e["costo_ml_kit"] - otros_k
        dif = margen_kit - margen_suelto

        # **Lo que descalifica es perder plata, no perder margen.** Un kit que
        # gana menos por venta pero sigue en positivo puede convenir igual: el
        # precio del pack empuja volumen y ese margen se cobra mas veces. El
        # corte duro es el cero.
        # El replace de miles va sobre CADA numero, nunca sobre la frase
        # entera: aplicado al texto se come las comas de la redaccion.
        def pes(v):
            return f"${v:,.0f}".replace(",", ".")

        if margen_kit <= 0:
            ver = "NO"
            mot = (f"margen negativo ({pes(margen_kit)}): se pierde plata en "
                   f"cada venta, el volumen no lo arregla")
        elif dif > 0:
            ver = "conviene"
            mot = f"gana {pes(dif)} más por venta"
        else:
            extra = (-dif) / margen_kit if margen_kit else 0
            ver = "probar"
            mot = (f"gana {pes(-dif)} menos por venta pero sigue en positivo "
                   f"({pes(margen_kit)}, {margen_kit / e['precio_kit']:.0%}); "
                   f"empata vendiendo {extra:.0%} más")

        filas.append({
            "detalle": k.get("detalle"), "productos": k.get("productos"),
            "precio_suelto": round(suelto, 2),
            "costo": round(costo, 2),
            "margen_suelto": round(margen_suelto, 2),
            "margen_kit": round(margen_kit, 2),
            "diferencia": round(dif, 2),
            "margen_kit_pct": round(margen_kit / e["precio_kit"], 4)
                              if e["precio_kit"] else None,
            "veredicto": ver, "motivo": mot,
            "cruza_umbral": bool(k.get("cruza_umbral")),
        })
    df = pd.DataFrame(filas)
    if len(df) and "diferencia" in df:
        df = df.sort_values("diferencia", ascending=False)
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ registro

HOJA_KITS = "kits"
COLS_KITS = ["fecha", "tipo", "productos", "detalle", "skus", "items",
             "precio_suelto", "precio_kit", "descuento", "ahorro_ml",
             "ahorro_de", "cruza_umbral", "veredicto", "motivo", "estado",
             "operador", "user_product", "link"]


def registrar(filas, operador="", estado="propuesto"):
    """
    Deja constancia en el Sheet de los kits propuestos y de los armados.

    **No crea nada en MercadoLibre**: armar el kit es del panel (ver el
    encabezado). Esto es el registro de que se decidio y quien lo decidio, que
    es lo que hoy no existe en ningun lado.

    `append_hoja` recibe **diccionarios** y devuelve `(ok, detalle)`; si no se
    mira el retorno, el registro falla en silencio.
    """
    import almacen
    from datetime import datetime as _dt

    if hasattr(filas, "iterrows"):
        filas = [r.to_dict() for _, r in filas.iterrows()]
    if not filas:
        return True, "nada para registrar"

    ahora = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    listas = []
    for f in filas:
        listas.append({
            "fecha": ahora,
            "tipo": f.get("origen") or f.get("tipo") or "",
            "productos": f.get("productos") or f.get("unidades") or "",
            "detalle": f.get("detalle") or f.get("producto") or "",
            "skus": f.get("skus") or f.get("sku") or "",
            "items": f.get("items") or f.get("item") or "",
            "precio_suelto": f.get("precio_suelto"),
            "precio_kit": f.get("precio_kit_sugerido"),
            "descuento": f.get("descuento_que_banca"),
            "ahorro_ml": f.get("ahorro_ml"),
            "ahorro_de": f.get("ahorro_de", ""),
            "cruza_umbral": f.get("cruza_umbral", ""),
            "veredicto": f.get("veredicto", ""),
            "motivo": f.get("motivo", ""),
            "estado": estado,
            "operador": operador,
            "user_product": f.get("user_product", ""),
            "link": f.get("crear_kit", ""),
        })
    ok, detalle = almacen.append_hoja(HOJA_KITS, COLS_KITS, listas)
    return ok, (detalle or f"{len(listas)} kits registrados")


def registrados():
    """Lo que ya se registró, para no proponer dos veces lo mismo."""
    import almacen
    try:
        return pd.DataFrame(almacen.leer_hoja(HOJA_KITS, COLS_KITS))
    except Exception:            # noqa: BLE001
        return pd.DataFrame()
