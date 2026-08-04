#!/usr/bin/env python3
"""
Optimizador de precios por tramo de comision.

    python tramos.py            -> analiza el catalogo activo
    python tramos.py --umbrales -> vuelve a medir los umbrales contra la API

MercadoLibre cobra un porcentaje **mas un cargo fijo por unidad**, y ese cargo
fijo salta en escalones de precio.

**Ojo con $33.000, que parece una oportunidad y es lo contrario.** Ahi arriba
pasan DOS cosas a la vez, y la segunda es mas grande que la primera:

  - el cargo fijo cae de $3.005 a cero        -> te ahorras $3.005
  - el envio deja de pagarlo el comprador y
    pasa a pagarlo el vendedor (~$7.641)      -> te cuesta $7.641

Medido sobre 5.170 ordenes con dato de envio: por debajo de $33.000 de precio
unitario solo el 6% tiene costo de envio para el vendedor (son opt-in); desde
$33.000, el **99%** lo paga el vendedor.

O sea que cruzar $33.000 cuesta ~$4.600 por unidad. Durante meses este modulo
recomendo exactamente lo contrario, porque calculaba el neto **sin el envio**.

Lo que si encuentra:

  - productos apenas ARRIBA de $33.000 que dejarian mas neto bajando a $32.999
    (y ademas se venden mas baratos, o sea que probablemente vendan mas)
  - subas chicas que cruzan un escalon donde el cargo fijo baja **sin**
    activar el envio a cargo del vendedor

Es solo lectura: sugiere, no toca precios.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from catalogo import sku_del_atributo
from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

# Escalones del cargo fijo, medidos contra /sites/MLA/listing_prices con
# busqueda binaria (jul 2026). `hasta` es exclusivo.
#   precio < 16000  -> $1.250      16000..23999 -> $2.505
#   24000..32999    -> $3.005      >= 33000     -> $0
TRAMOS = [(16000, 1250.0), (24000, 2505.0), (33000, 3005.0),
          (float("inf"), 0.0)]
PORCENTAJE = 0.13          # comision base gold_special

# Envio gratis obligatorio: desde este precio unitario lo paga el vendedor.
# Medido sobre 5.170 ordenes con dato de envio (jul 2026). El corte cae justo
# en $33.000, el mismo del cargo fijo: debajo solo el 6% de las ordenes tiene
# costo de envio para el vendedor (opt-in voluntario), arriba el 99%.
# La mediana de lo que paga el vendedor es $7.641 (promedio $8.443,
# p25 $6.140, p75 $8.250).
UMBRAL_ENVIO_GRATIS = 33000
ENVIO_VENDEDOR = 7641.0

# Cuanto se acepta subir un precio con tal de cruzar un escalon.
MARGEN_SUBIDA = 0.08       # 8%

# Cuanto se acepta BAJAR un precio para esquivar el envio obligatorio. Bajar
# el precio tambien deberia ayudar al volumen, asi que se permite mas margen.
MARGEN_BAJADA = 0.12       # 12%


def cargo_fijo(precio):
    for tope, fijo in TRAMOS:
        if precio < tope:
            return fijo
    return 0.0


def envio_a_cargo(precio, envio_historico=0.0):
    """
    Lo que paga el VENDEDOR de envio si el producto se vende a `precio`.

    Es un **escalon del precio**, no una constante por SKU. Modelarlo como el
    promedio historico del SKU es el error que hacia que este analisis
    recomendara cruzar $33.000: un producto que hoy esta debajo del umbral
    tiene promedio ~0, y al empujarlo por encima se seguia calculando con
    envio cero, justo cuando el envio aparece.

    `envio_historico` es el promedio medido de ese SKU, si se tiene. Se usa
    solo cuando el precio queda **arriba** del umbral, porque el envio depende
    del tamaño y el peso y el dato propio le gana a la mediana global. Si el
    SKU nunca vendio por encima del umbral su promedio es ~0 y no sirve: ahi
    cae a la mediana de la cuenta.
    """
    if precio < UMBRAL_ENVIO_GRATIS:
        return 0.0
    return envio_historico if envio_historico > 0 else ENVIO_VENDEDOR


def neto(precio, pct=PORCENTAJE, con_envio=True):
    """
    Lo que queda por unidad despues de TODO lo que cobra ML: comision
    porcentual, cargo fijo y —esto es lo que faltaba— el envio, que arriba de
    `UMBRAL_ENVIO_GRATIS` lo paga el vendedor.

    `con_envio=False` deja el calculo viejo (solo comisiones). Sirve para
    comparar contra los numeros historicos, no para decidir precios.
    """
    base = precio - (precio * pct + cargo_fijo(precio))
    return base - envio_a_cargo(precio) if con_envio else base


def medir_umbrales(ml, desde=1000, hasta=60000, paso=500):
    """Vuelve a medir los escalones contra la API, por si ML los cambia."""
    def fijo_api(p):
        r = ml.get(f"/sites/{SITE_ID}/listing_prices", price=p)
        g = [x for x in r if x["listing_type_id"] == "gold_special"][0]
        return g["sale_fee_details"].get("fixed_fee")

    saltos, ant = [], None
    for p in range(desde, hasta + 1, paso):
        f = fijo_api(p)
        if ant is not None and f != ant[1]:
            lo, hi = ant[0], p
            while hi - lo > 1:            # afinamos el borde exacto
                m = (lo + hi) // 2
                if fijo_api(m) == ant[1]:
                    lo = m
                else:
                    hi = m
            saltos.append({"umbral": hi, "fijo_antes": ant[1], "fijo_despues": f})
        ant = (p, f)
    return saltos


def _candidatos(precio):
    """
    Precios que vale la pena evaluar para una publicacion que hoy esta en
    `precio`. Devuelve (precio_candidato, motivo).

    Dentro de un tramo el neto crece con el precio, asi que los unicos optimos
    locales estan en los bordes: justo EN un escalon (si cruzarlo abarata lo
    que cobra ML) o justo DEBAJO de uno (si conviene no cruzarlo).
    """
    salidas = []
    coste = lambda x: cargo_fijo(x) + envio_a_cargo(x)

    # Hacia arriba: el proximo escalon, siempre que no se pase del margen Y
    # que cruzarlo abarate de verdad lo que cobra ML.
    #
    # El filtro por coste es imprescindible. Sin el, cualquier escalon cercano
    # "mejora el neto", pero solo porque subiste el precio: si el escalon te
    # deja pagando MAS —cargo fijo mas caro, o envio que antes pagaba el
    # comprador— quedarse un peso abajo deja todavia mas plata.
    for tope, _ in TRAMOS:
        if tope == float("inf") or tope <= precio:
            continue
        if tope <= precio * (1 + MARGEN_SUBIDA) and coste(tope) < coste(precio):
            salidas.append((float(tope), "sube al escalón"))
        break

    # Hacia abajo: el ultimo peso antes del escalon que ya cruzo. Es la salida
    # para los que estan apenas arriba de $33.000 pagando envio.
    bordes = [t for t, _ in TRAMOS if t != float("inf") and t <= precio]
    if bordes:
        candidato = float(max(bordes)) - 1.0
        if candidato >= precio * (1 - MARGEN_BAJADA):
            salidas.append((candidato, "baja para esquivar el envío"))

    return salidas


def analizar(pubs=None):
    """
    Para cada publicacion activa busca el precio cercano —arriba o abajo— que
    deje MAS neto que el actual, contando comision, cargo fijo y envio.
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    filas = []
    for p in pubs:
        if p.get("status") != "active" or not p.get("price"):
            continue
        precio = float(p["price"])
        neto_actual = neto(precio)

        sugerido, neto_sug, motivo = None, neto_actual, ""
        for candidato, razon in _candidatos(precio):
            n = neto(candidato)
            if n > neto_sug:
                sugerido, neto_sug, motivo = candidato, n, razon

        if sugerido is None:
            continue

        filas.append({
            "item_id": p["id"],
            "sku": sku_del_atributo(p) or "",
            "titulo": (p.get("title") or "")[:60],
            "vendidos": p.get("sold_quantity") or 0,
            "precio_actual": precio,
            "neto_actual": round(neto_actual, 2),
            "precio_sugerido": sugerido,
            "neto_sugerido": round(neto_sug, 2),
            "motivo": motivo,
            "cambia_precio": round((sugerido - precio) / precio, 4),
            "sube_precio": round((sugerido - precio) / precio, 4),  # compat
            "gana_por_unidad": round(neto_sug - neto_actual, 2),
            "cargo_fijo_actual": cargo_fijo(precio),
            "cargo_fijo_nuevo": cargo_fijo(sugerido),
            "envio_actual": envio_a_cargo(precio),
            "envio_nuevo": envio_a_cargo(sugerido),
        })

    df = pd.DataFrame(filas)
    if len(df):
        # Lo que mas rinde: mucha ganancia por unidad y producto que rota.
        df["impacto"] = df["gana_por_unidad"] * df["vendidos"].clip(lower=1)
        df = df.sort_values("impacto", ascending=False)
    return df


# ----------------------------------------------------------------- ejecucion
#
# Hasta aca el modulo es solo lectura. De aca abajo escribe precios en la
# cuenta de verdad.
#
# **Por que el envio no se toca.** Medido el 2026-08-04 sobre MLA1599754027:
# a $33.324 la publicacion tenia `free_shipping: true` y la etiqueta
# `mandatory_free_shipping`; al bajarla a $32.999, en menos de 3 segundos ML
# dejo `free_shipping: false` y saco la etiqueta solo; al devolverle el precio
# volvieron las dos cosas. O sea que el envio gratis obligatorio lo deriva ML
# del precio y **no hay que escribirlo** — de hecho es suyo, no nuestro.
#
# Igual se verifica publicacion por publicacion, porque la regla no es un
# corte limpio en $33.000: hay publicaciones arriba del umbral sin la etiqueta
# (depende tambien de categoria y dimensiones). Si en alguna el envio NO se
# apaga, bajar el precio pasa de ganar ~$1.500 por unidad a perder ~$6.200, y
# por eso esa se revierte sola.

TECHO_DE_CAMBIO = 0.15     # tope duro: no se ejecuta un cambio mas grande

CAMPOS_VIVO = ["id", "price", "status", "shipping"]


def _estado_vivo(pub):
    envio = pub.get("shipping") or {}
    return {
        "item_id": pub.get("id"),
        "precio_vivo": float(pub.get("price") or 0),
        "status": pub.get("status"),
        "envio_gratis": bool(envio.get("free_shipping")),
    }


def plan(ml, seleccion, callback=None):
    """
    Rearma la sugerencia contra el precio que la publicacion tiene **ahora**.

    El analisis sale de `catalogo.json`, que puede tener dias: si el precio se
    movio desde entonces, el precio sugerido que se ve en pantalla ya no es el
    que corresponde. Escribirlo igual seria fijar un precio calculado sobre un
    dato viejo, que es justo el error caro en esta pantalla.

    Devuelve un DataFrame con una fila por publicacion y la columna `accion`:
    'aplicar' o 'omitir' (con `motivo` explicando cual).
    """
    ids = [str(i) for i in seleccion["item_id"]]
    if not ids:
        return pd.DataFrame()

    vivos = {}
    for i, pub in enumerate(ml.items_detalle(ids, atributos=CAMPOS_VIVO), 1):
        vivos[pub.get("id")] = _estado_vivo(pub)
        if callback and i % 20 == 0:
            callback(f"Leyendo precios actuales... {i}/{len(ids)}")

    filas = []
    for _, r in seleccion.iterrows():
        item = str(r["item_id"])
        base = {"item_id": item, "sku": r.get("sku", ""),
                "titulo": r.get("titulo", ""),
                "vendidos": r.get("vendidos", 0),
                "precio_pantalla": float(r.get("precio_actual") or 0)}

        v = vivos.get(item)
        if v is None:
            filas.append({**base, "accion": "omitir",
                          "motivo": "no pude leer la publicación"})
            continue

        precio = v["precio_vivo"]
        base.update({"precio_actual": precio, "envio_gratis": v["envio_gratis"]})

        if v["status"] != "active":
            filas.append({**base, "accion": "omitir",
                          "motivo": f"no está activa ({v['status']})"})
            continue

        # La sugerencia se recalcula: la de pantalla puede ser de otro precio.
        nuevo, mejor, motivo = None, neto(precio), ""
        for cand, razon in _candidatos(precio):
            n = neto(cand)
            if n > mejor:
                nuevo, mejor, motivo = cand, n, razon

        if nuevo is None:
            filas.append({**base, "accion": "omitir",
                          "motivo": "al precio de hoy ya no conviene cambiarla"})
            continue

        cambio = (nuevo - precio) / precio
        if abs(cambio) > TECHO_DE_CAMBIO:
            filas.append({**base, "accion": "omitir",
                          "motivo": f"el cambio sería de {cambio:+.0%}, "
                                    f"más que el tope de {TECHO_DE_CAMBIO:.0%}"})
            continue

        # Nunca empujar una publicacion POR ENCIMA del umbral desde aca: ahi
        # aparece el envio a cargo del vendedor y el analisis no lo pidio.
        if precio < UMBRAL_ENVIO_GRATIS <= nuevo:
            filas.append({**base, "accion": "omitir",
                          "motivo": "la subiría por encima del umbral de envío"})
            continue

        filas.append({
            **base, "accion": "aplicar", "motivo": motivo,
            "precio_nuevo": nuevo,
            "cambia_precio": round(cambio, 4),
            "gana_por_unidad": round(mejor - neto(precio), 2),
            # Solo estas hay que verificarlas: son las que dependen de que ML
            # apague el envio gratis.
            "cruza_umbral": bool(precio >= UMBRAL_ENVIO_GRATIS > nuevo),
        })

    return pd.DataFrame(filas)


def aplicar(ml, plan_df, operador="", callback=None):
    """
    Escribe los precios de las filas con accion 'aplicar'.

    Dos cosas que no son opcionales en una escritura masiva sobre plata real:

    - **Una falla no puede matar el lote.** Cada publicacion va en su propio
      try: si una revienta, se registra y se sigue con la siguiente.
    - **Las que cruzan el umbral se verifican.** Despues de bajar el precio se
      relee el envio. Si quedo prendido, el cambio deja de convenir y se
      revierte esa publicacion al precio anterior.
    """
    if plan_df is None or not len(plan_df):
        return pd.DataFrame()

    pendientes = plan_df[plan_df["accion"] == "aplicar"]
    nota = f"tramos de comisión {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)

        item, antes, nuevo = f["item_id"], f["precio_actual"], f["precio_nuevo"]
        fila = {"item_id": item, "sku": f.get("sku", ""),
                "titulo": f.get("titulo", ""), "precio_antes": antes,
                "precio_nuevo": nuevo,
                "gana_por_unidad": f.get("gana_por_unidad", 0)}

        try:
            ok, detalle = ml.actualizar_publicacion(
                item, {"price": nuevo}, {"price": antes},
                operador=operador, nota=nota)
            if not ok:
                salida.append({**fila, "resultado": "ERROR",
                               "detalle": str(detalle)[:200]})
                continue

            if not f.get("cruza_umbral"):
                salida.append({**fila, "resultado": "OK", "detalle": ""})
                continue

            # Cruzo el umbral: ML tiene que haber apagado el envio gratis.
            estado = _estado_vivo(ml.get(f"/items/{item}",
                                         attributes=",".join(CAMPOS_VIVO)))
            if not estado["envio_gratis"]:
                salida.append({**fila, "resultado": "OK",
                               "detalle": "envío gratis apagado"})
                continue

            # No se apago: el cambio pasa a perder plata. Se revierte.
            vok, vdet = ml.actualizar_publicacion(
                item, {"price": antes}, {"price": nuevo},
                operador=operador, nota=f"{nota} - revierte, envío seguía gratis")
            salida.append({
                **fila, "resultado": "REVERTIDA" if vok else "REVERTIR FALLÓ",
                "detalle": ("ML no apagó el envío gratis; se volvió al precio "
                            "anterior" if vok else
                            f"ML no apagó el envío gratis y la vuelta atrás "
                            f"falló: {str(vdet)[:120]}")})

        except Exception as e:
            salida.append({**fila, "resultado": "ERROR",
                           "detalle": f"{type(e).__name__}: {str(e)[:180]}"})

    return pd.DataFrame(salida)


def main():
    if "--umbrales" in sys.argv:
        ml = Meli(verbose=False)
        print("Midiendo los escalones contra la API...")
        for s in medir_umbrales(ml):
            print(f"   ${s['umbral']:>7,}  fijo ${s['fijo_antes']} -> "
                  f"${s['fijo_despues']}")
        return 0

    df = analizar()
    if not len(df):
        print("Ninguna publicación está cerca de un escalón de comisión.")
        return 0

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"Publicaciones que convendría reprecificar: {len(df)}\n")
    for razon, g in df.groupby("motivo"):
        print(f"  {razon}: {len(g)}")
    print("\nLas 12 de mayor impacto:")
    for _, f in df.head(12).iterrows():
        print(f"  {f['sku'] or f['item_id']}  [{f['motivo']}]")
        print(f"     {pes(f['precio_actual'])} -> {pes(f['precio_sugerido'])} "
              f"({f['cambia_precio']:+.1%})  |  neto por unidad "
              f"{pes(f['neto_actual'])} -> {pes(f['neto_sugerido'])} "
              f"(+{pes(f['gana_por_unidad'])})")
        print(f"     cargo fijo {pes(f['cargo_fijo_actual'])} -> "
              f"{pes(f['cargo_fijo_nuevo'])} | envío "
              f"{pes(f['envio_actual'])} -> {pes(f['envio_nuevo'])} | "
              f"vendidas {int(f['vendidos'])}")

    df.to_csv(DIR / "tramos.csv", index=False)
    print(f"\nGuardado en tramos.csv ({len(df)} publicaciones)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
