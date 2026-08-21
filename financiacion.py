#!/usr/bin/env python3
"""
Publicaciones Premium que no cubren lo que cuesta dar cuotas.

    python financiacion.py            -> que haria, sin tocar nada
    python financiacion.py --csv      -> lo exporta

**El problema.** Una misma publicacion puede estar en Premium (`gold_pro`,
con cuotas sin interes) y en Clasica (`gold_special`). La Premium paga ~12
puntos mas de comision, asi que **su precio tiene que ser mas alto** o el
negocio pierde plata en cada venta. Medido el 21/08/2026 sobre CRAFTERS: de
128 SKU con las dos versiones, **102 pares no cubren la diferencia** — 35 al
precio exactamente igual, 35 con la Premium **mas barata** que la Clasica, y
32 mas cara pero no lo suficiente.

Y no es un problema teorico: en 30 de esos casos la Premium **le gana en
ventas a la Clasica**, porque con cuotas y precio igual o menor el comprador
elige esa. Son $305.010 por mes de margen que se deja en la mesa.

**Por que no lo agarraba `espejos.py`.** Ese modulo compara precios de
publicaciones del mismo SKU pero **solo entre las del mismo tipo**, a
proposito: "es esperable que la Premium valga mas". Justo el cruce que hay que
mirar es el que excluye.

**La trampa de la cuenta.** Para igualar el neto no alcanza con sumarle al
precio los 12,3 puntos de diferencia de comision: el recargo se aplica sobre
el precio nuevo, que tambien paga comision. Hay que despejar

    precio_pro x (1 - pct_pro) = precio_clasica x (1 - pct_clasica)

que con 25,8% y 13,5% da **+16,6%**, no +12,3%. Cobrar 12,3 deja la operacion
todavia en perdida, y es el error que parece razonable.

**El precio se relee siempre.** El primer intento de este analisis salio de
`catalogo.json` y daba 63 casos en vez de 102: **1.042 de 2.109 publicaciones
tenian ahi un precio viejo**. Para una del ejemplo el cache decia $87.604 y la
API $73.003, o sea una brecha de -$5.366 donde la real era de -$16.200.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

import tramos
from catalogo import sku_del_atributo
from resolver import CON_FINANCIACION, SIN_FINANCIACION

DIR = Path(__file__).resolve().parent
SITE_ID = "MLA"

# Diferencias de menos de esto son redondeo, no una decision de precio.
TOLERANCIA = 1.0

# Cuanto se acepta mover un precio de una sola vez. Subir 16,6% ya es un
# salto grande; mas que esto se muestra pero no se aplica sin mirarlo.
TECHO_DE_SUBIDA = 0.25


# ------------------------------------------------------------------ tarifas

def tarifas(ml, categorias, precio_sonda=50000):
    """
    {(categoria, tipo): porcentaje} desde ML.

    **El porcentaje depende de la categoria y del tipo, no del precio**, asi
    que alcanza una llamada por categoria en vez de una por publicacion. El
    cargo fijo si depende del precio y sale de `tramos.cargo_fijo()`, que esta
    medido contra este mismo endpoint.
    """
    pct = {}
    for c in sorted({x for x in categorias if x}):
        try:
            for x in ml.get(f"/sites/{SITE_ID}/listing_prices",
                            price=precio_sonda, category_id=c):
                t = x.get("listing_type_id")
                if t in (CON_FINANCIACION, SIN_FINANCIACION):
                    pct[(c, t)] = float(
                        (x.get("sale_fee_details") or {}).get("percentage_fee") or 0)
        except Exception:                              # noqa: BLE001
            # Una categoria sin tarifa no puede tumbar el analisis entero:
            # sus publicaciones quedan afuera y se cuentan aparte.
            continue
    return pct


def neto(precio, porcentaje, con_envio=True):
    """Lo que queda por unidad despues de comision, cargo fijo y envio."""
    return tramos.neto(float(precio), float(porcentaje) / 100,
                       con_envio=con_envio)


def spread_que_iguala(pct_pro, pct_clasica):
    """
    Cuanto mas caro tiene que ser el Premium para dejar lo mismo.

    Es el `(1 - pct_clasica) / (1 - pct_pro) - 1` de arriba: **no** es la
    resta de los porcentajes. Devuelve una fraccion (0,166 = +16,6%).
    """
    p, c = float(pct_pro) / 100, float(pct_clasica) / 100
    if p >= 1:
        return 0.0
    return (1 - c) / (1 - p) - 1


def precio_para_igualar(objetivo, pct_pro, tope=None):
    """
    El precio Premium que deja `objetivo` por unidad.

    Se resuelve **buscando**, no despejando, porque el cargo fijo y el envio
    son escalones del precio: la funcion no es lineal y una formula cerrada se
    equivoca justo en los bordes, que es donde estan los productos baratos.
    """
    lo, hi = 1.0, float(tope or max(objetivo * 4, 1000))
    if neto(hi, pct_pro) < objetivo:
        return None                    # ni al tope se llega
    for _ in range(60):
        mid = (lo + hi) / 2
        if neto(mid, pct_pro) < objetivo:
            lo = mid
        else:
            hi = mid
    return round(hi, 2)


# ----------------------------------------------------------------- analisis

def _frescos(ml, pubs, callback=None):
    """
    Relee precio y estado en vivo. Ver el docstring del modulo: el catalogo
    guardado tenia el precio movido en la mitad de las publicaciones.
    """
    ids = [p["id"] for p in pubs]
    vivos = {}
    for i in range(0, len(ids), 20):
        if callback:
            callback(f"Releyendo precios... {i}/{len(ids)}")
        try:
            for w in ml.get("/items", ids=",".join(ids[i:i + 20])):
                b = (w or {}).get("body") or {}
                if b.get("id"):
                    vivos[b["id"]] = b
        except Exception:                              # noqa: BLE001
            continue
    return [vivos.get(p["id"], p) for p in pubs]


def analizar(ml, pubs=None, sugeridos=None, callback=None):
    """
    Los pares Premium/Clasica del mismo SKU, con la brecha por unidad.

    `sugeridos` es el `lista_precios.mapa_precios()`: {sku: {'sugerido': x}}.
    Se usa para proponer el precio nuevo, no para detectar el problema.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    vivos = _frescos(ml, [p for p in pubs if p.get("status") == "active"],
                     callback=callback)
    vivos = [p for p in vivos if p.get("status") == "active"]

    por_sku = defaultdict(list)
    for p in vivos:
        s = str(sku_del_atributo(p) or "").strip().upper()
        if s:
            por_sku[s].append(p)

    mixtos = {s: g for s, g in por_sku.items()
              if any(x.get("listing_type_id") == CON_FINANCIACION for x in g)
              and any(x.get("listing_type_id") == SIN_FINANCIACION for x in g)}
    if callback:
        callback(f"{len(mixtos)} SKU tienen Premium y Clásica a la vez.")

    pct = tarifas(ml, {x.get("category_id") for g in mixtos.values() for x in g})
    sugeridos = sugeridos or {}
    filas = []

    for sku, g in mixtos.items():
        pros = [x for x in g if x.get("listing_type_id") == CON_FINANCIACION]
        clas = [x for x in g if x.get("listing_type_id") == SIN_FINANCIACION]
        # La Clasica de referencia es **la que mas vendio**: es la que el
        # mercado ya validó. Misma regla que usa `espejos.py`.
        ref = max(clas, key=lambda x: (x.get("sold_quantity") or 0,
                                       float(x.get("price") or 0)))
        cat_ref, precio_ref = ref.get("category_id"), float(ref.get("price") or 0)
        p_ref = pct.get((cat_ref, SIN_FINANCIACION))
        if p_ref is None or not precio_ref:
            continue
        neto_ref = neto(precio_ref, p_ref)

        for pr in pros:
            cat, precio = pr.get("category_id"), float(pr.get("price") or 0)
            p_pro = pct.get((cat, CON_FINANCIACION))
            p_cla = pct.get((cat, SIN_FINANCIACION))
            if p_pro is None or not precio:
                continue
            filas.append({
                "sku": sku,
                "titulo": (pr.get("title") or "")[:60],
                "item_id": pr["id"],
                "precio_actual": precio,
                "clasica": ref["id"],
                "precio_clasica": precio_ref,
                "pct_premium": p_pro,
                "pct_clasica": p_cla if p_cla is not None else p_ref,
                "neto_premium": round(neto(precio, p_pro), 2),
                "neto_clasica": round(neto_ref, 2),
                "brecha": round(neto(precio, p_pro) - neto_ref, 2),
                "dif_precio": round(precio - precio_ref, 2),
                "sugerido": (sugeridos.get(sku) or {}).get("sugerido"),
                "vendidas_premium": pr.get("sold_quantity") or 0,
                "vendidas_clasica": ref.get("sold_quantity") or 0,
                # El neto real cambia mas cuando la logistica difiere: el
                # envio no es el mismo y esta cuenta no lo separa por deposito.
                "misma_logistica": ((pr.get("shipping") or {}).get("logistic_type")
                                    == (ref.get("shipping") or {}).get("logistic_type")),
            })

    df = pd.DataFrame(filas)
    return df.sort_values("brecha") if len(df) else df


# --------------------------------------------------------------------- plan

def plan(df, base="sugerido", spread=None, solo_negativos=True):
    """
    Que precio poner en cada Premium. Devuelve el mismo df con `precio_nuevo`.

    `base` decide de donde sale el precio de partida:

      - `'sugerido'`: el precio sugerido del SKU (`ListaPrecio x 2,12`). Es
        una decision comercial ya tomada, asi que respetarla es lo natural.
        **Si el SKU no tiene sugerido cargado, cae a `'clasica'`** en vez de
        quedar afuera: es la mitad del catalogo la que no cruza.
      - `'clasica'`: el precio de la Clasica de referencia.
      - `'igualar'`: el precio exacto que empata el neto, resuelto contra los
        escalones reales. Ignora `spread`.

    `spread` es la fraccion a sumarle a la base (0,166 = +16,6%). Con `None`
    se usa **el que iguala el neto en esa categoria**, que es distinto por
    categoria porque las comisiones lo son. Con `0` se publica la base tal
    cual — sirve para alinear precios sin cobrar la financiacion, que es una
    decision valida y hay que poder tomarla a proposito.
    """
    if df is None or not len(df):
        return df
    out = df.copy()
    if solo_negativos:
        out = out[out["brecha"] <= 0].copy()
    if not len(out):
        return out

    nuevos, motivos, bases = [], [], []
    for _, f in out.iterrows():
        auto = spread_que_iguala(f["pct_premium"], f["pct_clasica"])
        usa = auto if spread is None else float(spread)

        if base == "igualar":
            p = precio_para_igualar(f["neto_clasica"], f["pct_premium"])
            origen = "iguala el neto de la Clásica"
        else:
            sug = f.get("sugerido")
            if base == "sugerido" and sug and float(sug) > 0:
                p, origen = float(sug) * (1 + usa), "sugerido del SKU"
            else:
                p, origen = float(f["precio_clasica"]) * (1 + usa), (
                    "precio de la Clásica"
                    + (" (el SKU no tiene sugerido)" if base == "sugerido" else ""))
            origen = f"{origen} + {usa:.1%}"

        nuevos.append(None if p is None else round(float(p), 2))
        bases.append(origen)
        motivos.append("")

    out["precio_nuevo"] = nuevos
    out["origen"] = bases
    out["cambio"] = [(n / a - 1) if (n and a) else 0
                     for n, a in zip(out["precio_nuevo"], out["precio_actual"])]
    out["neto_nuevo"] = [
        round(neto(n, p), 2) if n else None
        for n, p in zip(out["precio_nuevo"], out["pct_premium"])]
    out["gana_por_unidad"] = [
        round(nn - na, 2) if nn is not None else 0
        for nn, na in zip(out["neto_nuevo"], out["neto_premium"])]

    def decidir(f):
        if not f["precio_nuevo"]:
            return "revisar", "no encontré un precio que iguale el neto"
        if abs(f["precio_nuevo"] - f["precio_actual"]) < TOLERANCIA:
            return "ninguna", "ya está en el precio propuesto"
        if f["cambio"] > TECHO_DE_SUBIDA:
            return "revisar", (f"sube {f['cambio']:.0%}, más que el tope de "
                               f"{TECHO_DE_SUBIDA:.0%}")
        if f["precio_nuevo"] < f["precio_actual"]:
            return "revisar", "el precio propuesto es MENOR que el actual"
        return "aplicar", ""

    decisiones = [decidir(f) for _, f in out.iterrows()]
    out["accion"] = [d[0] for d in decisiones]
    out["motivo"] = [d[1] for d in decisiones]
    return out.sort_values("gana_por_unidad", ascending=False)


# ------------------------------------------------------------------ aplicar

def aplicar(ml, plan_df, operador="", callback=None):
    """
    Escribe los precios de las filas con accion 'aplicar'.

    **Cada publicacion en su propio try**: una falla no puede matar el lote,
    que es plata real y hay que poder retomar donde quedo.

    **Y se verifica el envio.** Subir el precio puede cruzar
    `UMBRAL_ENVIO_GRATIS` hacia arriba y hacer que ML prenda el envio gratis
    obligatorio, que lo paga el vendedor: el cambio que parecia ganar termina
    perdiendo. Cuando el precio nuevo cruza el umbral se relee la publicacion
    y, si el envio quedo a cargo nuestro y eso da vuelta la cuenta, se
    revierte esa fila.
    """
    if plan_df is None or not len(plan_df):
        return pd.DataFrame()

    pendientes = plan_df[plan_df["accion"] == "aplicar"]
    nota = f"spread de financiación {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)
        item = f["item_id"]
        antes, nuevo = float(f["precio_actual"]), float(f["precio_nuevo"])
        fila = {"item_id": item, "sku": f.get("sku", ""),
                "titulo": f.get("titulo", ""), "precio_antes": antes,
                "precio_nuevo": nuevo,
                "gana_por_unidad": f.get("gana_por_unidad", 0)}
        cruza = antes < tramos.UMBRAL_ENVIO_GRATIS <= nuevo

        try:
            ok, detalle = ml.actualizar_publicacion(
                item, {"price": nuevo}, {"price": antes},
                operador=operador, nota=nota)
            if not ok:
                salida.append({**fila, "resultado": "ERROR",
                               "detalle": str(detalle)[:200]})
                continue
            if not cruza:
                salida.append({**fila, "resultado": "OK", "detalle": ""})
                continue

            # Cruzo el umbral hacia arriba: si el envio quedo a nuestro cargo
            # y se come la mejora, el cambio no sirve.
            vivo = ml.get(f"/items/{item}", attributes="price,shipping")
            gratis = ((vivo.get("shipping") or {}).get("free_shipping"))
            if not gratis or f.get("gana_por_unidad", 0) > tramos.ENVIO_VENDEDOR:
                salida.append({**fila, "resultado": "OK",
                               "detalle": ("envío gratis prendido, pero la "
                                           "mejora lo cubre" if gratis else "")})
                continue

            vok, vdet = ml.actualizar_publicacion(
                item, {"price": antes}, {"price": nuevo}, operador=operador,
                nota=f"{nota} - revierte, el envío se comía la mejora")
            salida.append({
                **fila,
                "resultado": "REVERTIDA" if vok else "REVERTIR FALLÓ",
                "detalle": ("al cruzar $33.000 ML prendió el envío gratis y se "
                            "comía la mejora; se volvió al precio anterior"
                            if vok else f"la vuelta atrás falló: {str(vdet)[:120]}")})
        except Exception as e:                         # noqa: BLE001
            salida.append({**fila, "resultado": "ERROR",
                           "detalle": f"{type(e).__name__}: {str(e)[:150]}"})
    return pd.DataFrame(salida)


def main():
    from meli import Meli
    import lista_precios

    ml = Meli(verbose=False)
    try:
        sug = lista_precios.mapa_precios()
    except Exception:                                  # noqa: BLE001
        sug = {}
        print("(sin lista de precios guardada: se usa el precio de la Clásica)")

    df = analizar(ml, sugeridos=sug, callback=lambda m: print(f"  {m}"))
    if not len(df):
        print("No hay SKU con Premium y Clásica a la vez.")
        return 0

    malos = df[df["brecha"] <= 0]
    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"\n{len(df)} pares Premium/Clásica en {df['sku'].nunique()} SKU.")
    print(f"{len(malos)} no cubren la financiación "
          f"({malos['sku'].nunique()} SKU).")
    print(f"  al mismo precio : {int((malos['dif_precio'].abs() < TOLERANCIA).sum())}")
    print(f"  más baratas     : {int((malos['dif_precio'] < -TOLERANCIA).sum())}")
    print(f"  más caras, corto: {int((malos['dif_precio'] > TOLERANCIA).sum())}")

    p = plan(df)
    listas = p[p["accion"] == "aplicar"]
    print(f"\nSe pueden corregir de una: {len(listas)} · "
          f"mejora {pes(listas['gana_por_unidad'].sum())} por unidad vendida")
    print(p[["sku", "item_id", "precio_actual", "precio_nuevo", "cambio",
             "gana_por_unidad", "accion", "motivo"]].head(20).to_string(index=False))

    if "--csv" in sys.argv:
        p.to_csv(DIR / "financiacion_plan.csv", index=False)
        print(f"\nGuardado en financiacion_plan.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
