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

import precios_redondeo
import tramos
from catalogo import sku_del_atributo
from resolver import CON_FINANCIACION, SIN_FINANCIACION

DIR = Path(__file__).resolve().parent
SITE_ID = "MLA"

# Diferencias de menos de esto son redondeo, no una decision de precio.
TOLERANCIA = 1.0


def no_cubre(df):
    """
    Las que **de verdad** no cubren la financiacion.

    **El corte no es `brecha <= 0`, es `brecha < -TOLERANCIA`.** Corregir una
    publicacion la deja con la brecha en **cero exacto** —ese es el objetivo:
    que el neto empate— y con el corte en `<= 0` las corregidas seguian
    contando como problema. Despues de una corrida que aplico 143 precios, la
    pantalla mostraba los mismos 106 casos que antes y parecia que no habia
    servido de nada, cuando los netos habian quedado igualados al centavo.
    """
    return df[df["brecha"] < -TOLERANCIA] if df is not None and len(df) else df

# **No hay techo de subida, a proposito** (decision de Mariano, 21/08/2026).
#
# Hubo uno de 25% y lo habia puesto yo sin ningun dato atras. Frenaba justo lo
# que la herramienta viene a arreglar: si la cuenta dice que hay que subir 30%
# para que el neto empate, cortar en 25% no protege nada — deja la publicacion
# vendiendo a perdida y esconde el caso en una fila de "revisar". Paso: el
# inflador Serie 600 quedo sin corregir y desde afuera parecia que el proceso
# no habia hecho nada.
#
# Los topes de las otras pantallas frenan **bajadas**, donde un dato malo te
# hace regalar plata. Aca se sube, y el riesgo no es el tamano del salto sino
# que el numero este mal. Contra eso protegen otras tres cosas: nunca se baja
# un precio, la tabla se ve entera antes de aplicar, y despues de escribir se
# verifica el envio y se revierte si se come la mejora.
SUBIDA_LLAMATIVA = 0.30    # no frena: solo se avisa cuantas pasan de aca


def redondear(precio):
    """
    Precios **sin decimales**, siempre para arriba.

    El precio que se busca es el que **empata el neto** con la Clasica, o sea
    un minimo: redondear para abajo lo deja un peso corto y la publicacion
    sigue —por poco— del lado equivocado. Por eso `piso` y no `cerca`.
    """
    return precios_redondeo.piso(precio)


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
    return redondear(hi)


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
                # Hace falta para decidir si se puede apagar la Premium: si
                # la Clasica no tiene stock, apagarla deja el producto sin
                # ninguna publicacion que venda.
                "stock_premium": pr.get("available_quantity") or 0,
                "stock_clasica": ref.get("available_quantity") or 0,
                "estado_clasica": ref.get("status"),
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
        out = no_cubre(out).copy()
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

        nuevos.append(None if p is None else redondear(p))
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
    # **Mejorar no es lo mismo que resolver.** Con la base 'sugerido' el
    # precio sale de una decision comercial, no de la cuenta, asi que puede
    # subir bastante y seguir por debajo del neto de la Clasica. Si no se
    # dice, el operador aplica y se queda pensando que quedo cerrado.
    out["cubre"] = [
        (nn is not None and nn >= nc - TOLERANCIA)
        for nn, nc in zip(out["neto_nuevo"], out["neto_clasica"])]

    def decidir(f):
        if not f["precio_nuevo"]:
            return "revisar", "no encontré un precio que iguale el neto"
        if abs(f["precio_nuevo"] - f["precio_actual"]) < TOLERANCIA:
            return "ninguna", "ya está en el precio propuesto"
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


# ------------------------------------------------------------------- apagar

def plan_apagado(df, minimo_stock=1):
    """
    La otra salida: en vez de subir el precio, apagar la Premium.

    Sirve cuando el producto no aguanta el recargo. **Es reversible** —la
    publicacion queda `paused` y se puede reactivar— pero se pierden las
    cuotas sin interes para ese producto, que es justamente lo que la hacia
    vender.

    No se apaga si la Clasica no puede tomar la venta: sin stock del otro
    lado el producto queda sin ninguna publicacion vendiendo, que es peor que
    venderlo con menos margen.
    """
    if df is None or not len(df):
        return df
    out = no_cubre(df).copy()
    if not len(out):
        return out

    def decidir(f):
        if str(f.get("estado_clasica") or "active") != "active":
            return "revisar", "la Clásica no está activa: no puede tomar la venta"
        if (f.get("stock_clasica") or 0) < minimo_stock:
            return "revisar", "la Clásica no tiene stock: quedaría sin vender"
        return "apagar", ""

    d = [decidir(f) for _, f in out.iterrows()]
    out["accion"] = [x[0] for x in d]
    out["motivo"] = [x[1] for x in d]
    # Lo que se deja de perder por unidad si la venta se muda a la Clasica.
    out["gana_por_unidad"] = -out["brecha"]
    return out.sort_values("gana_por_unidad", ascending=False)


def apagar(ml, plan_df, operador="", callback=None):
    """
    Pausa las Premium marcadas. Cada una en su propio try.

    **Se relee el estado despues de escribir.** `PUT /items/{id}` puede
    devolver 200 y dejar la publicacion como estaba: mirar solo el codigo da
    por hecho un cambio que no ocurrio. Ver `feedback_exito_falso`.
    """
    if plan_df is None or not len(plan_df):
        return pd.DataFrame()

    pendientes = plan_df[plan_df["accion"] == "apagar"]
    nota = f"apagada por spread de financiación {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)
        item = f["item_id"]
        fila = {"item_id": item, "sku": f.get("sku", ""),
                "titulo": f.get("titulo", ""),
                "precio": f.get("precio_actual"),
                "clasica": f.get("clasica"),
                "gana_por_unidad": f.get("gana_por_unidad", 0)}
        try:
            ok, detalle = ml.actualizar_publicacion(
                item, {"status": "paused"}, {"status": "active"},
                operador=operador, nota=nota)
            if not ok:
                salida.append({**fila, "resultado": "ERROR",
                               "detalle": str(detalle)[:200]})
                continue
            vivo = ml.get(f"/items/{item}", attributes="status")
            real = (vivo or {}).get("status")
            salida.append({
                **fila,
                "resultado": "OK" if real == "paused" else "NO QUEDÓ PAUSADA",
                "detalle": "" if real == "paused" else f"quedó en '{real}'"})
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

    malos = no_cubre(df)
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
