#!/usr/bin/env python3
"""
Mover promociones entre campañas y activarlas por regla.

    python promos_campanas.py                          -> lista las campañas
    python promos_campanas.py C-MLA1353496 C-MLA1456586 -> simula replicar
    python promos_campanas.py LGH-MLA1000 --tope 5      -> simula por regla

Dos cosas que hoy se hacen a mano, publicacion por publicacion:

**Replicar** — una campaña propia vence y hay que rearmar la siguiente con los
mismos descuentos. Se copia el **porcentaje**, nunca el precio: entre una
campaña y la otra los precios de lista se movieron, y copiar el importe viejo
aplicaria un descuento distinto al que se quiso.

**Activar por regla** — aceptar de una las ofertas que cumplen una condicion,
por ejemplo "todas las relampago que no pidan mas de 5% de descuento". En los
tipos donde ML fija el precio (LIGHTNING, SMART, PRICE_MATCHING) no hay nada
que negociar: se acepta o no, y lo unico que decide es cuanto pide.

--------------------------------------------------------------------------
Lo que hay que respetar para que no falle
--------------------------------------------------------------------------

**El rango de descuento es por publicacion, no por campaña.** Cada oferta trae
`min_discounted_price` y `max_discounted_price` y no son un porcentaje fijo.
Pasarse contesta 400 `ERROR_CREDIBILITY_DISCOUNTED_PRICE`, que suena a que el
precio es raro pero significa que quedo fuera del rango. Por eso replicar
**recorta al rango del destino** en vez de mandar y rezar.

**`offer_id` es obligatorio salvo en las campañas propias.** Sale del `ref_id`
del GET. En `SELLER_CAMPAIGN` no existe y el POST va sin el.

**El paginado ignora `offset` en silencio.** Va con el token `searchAfter`.

**El alta se propaga a los espejos**: un POST da de alta a todas las
publicaciones que comparten `user_product_id`. Repetirlo por cada espejo no
rompe (el mismo POST corrige un alta existente), pero son llamadas de mas.
"""

import sys
import time
from datetime import datetime

import pandas as pd

from meli import Meli, MeliError

PAUSA = 0.25

# Tipos donde el vendedor elige el descuento. En el resto ML fija el precio.
ELIGE_EL_VENDEDOR = ("SELLER_CAMPAIGN",)

COLUMNAS = ["item_id", "accion", "motivo", "tipo", "campana_id", "oferta_id",
            "precio_original", "precio_promo", "descuento", "min_precio",
            "max_precio", "stock_min", "stock_max"]


def campanas(ml):
    """Las campañas de la cuenta, como DataFrame."""
    import promociones
    return promociones.campanas_disponibles(ml)


def ofertas(ml, campana_id, tipo, estados=("candidate", "started"),
            callback=None):
    """
    Las ofertas de una campaña. {item_id: {...}}

    Se piden los estados por separado porque el listado sin filtro no trae
    todos los que ya estan dados de alta, y **`candidate` va primero**: es el
    unico que trae el rango permitido.
    """
    salida = {}
    for estado in estados:
        token, vueltas = None, 0
        while True:
            p = {"promotion_type": tipo, "app_version": "v2", "limit": 50,
                 "status": estado}
            if token:
                p["search_after"] = token
            try:
                r = ml.get(f"/seller-promotions/promotions/{campana_id}/items",
                           **p)
            except MeliError:
                break
            res = r.get("results") or []
            if not res:
                break
            for x in res:
                orig = x.get("original_price") or 0
                precio = x.get("price") or x.get("suggested_discounted_price")
                salida.setdefault(x["id"], {
                    "item_id": x["id"],
                    "estado_promo": x.get("status"),
                    "oferta_id": x.get("ref_id") or "",
                    "precio_original": orig,
                    "precio_promo": precio,
                    "descuento": (1 - precio / orig) if (orig and precio)
                                 else None,
                    "min_precio": x.get("min_discounted_price"),
                    "max_precio": x.get("max_discounted_price"),
                    # Las relampago piden COMPROMETER stock y el POST lo
                    # exige: sin el contesta 400 "Stock must be greater
                    # than X and less than Y".
                    "stock_min": (x.get("stock") or {}).get("min"),
                    "stock_max": (x.get("stock") or {}).get("max"),
                })
            vueltas += 1
            if callback:
                callback(f"{campana_id} {estado}: {len(salida)}...")
            token = (r.get("paging") or {}).get("searchAfter")
            if not token or vueltas > 80:
                break
    return salida


def _fila(item, accion, motivo, tipo="", campana="", o=None, precio=None,
          desc=None):
    o = o or {}
    return {"item_id": item, "accion": accion, "motivo": motivo, "tipo": tipo,
            "campana_id": campana, "oferta_id": o.get("oferta_id", ""),
            "precio_original": o.get("precio_original"),
            "precio_promo": precio, "descuento": desc,
            "min_precio": o.get("min_precio"), "max_precio": o.get("max_precio"),
            "stock_min": o.get("stock_min"), "stock_max": o.get("stock_max")}


def replicar(ml, origen, tipo_origen, destino, tipo_destino, callback=None,
             solo_activas=True):
    """
    Plan para llevar los descuentos de una campaña a otra.

    **Copia el porcentaje, no el importe.** Si en el origen una publicacion
    tenia 20% off, en el destino se aplica 20% sobre su precio de HOY.

    Solo puede replicar hacia una campaña propia: en los otros tipos el precio
    lo fija ML y no se puede elegir.
    """
    if tipo_destino not in ELIGE_EL_VENDEDOR:
        raise MeliError(
            f"no se puede replicar hacia {tipo_destino}: ahí el precio lo fija "
            f"MercadoLibre. Solo sirve hacia una campaña propia.")

    if callback:
        callback("leyendo la campaña de origen...")
    a = ofertas(ml, origen, tipo_origen,
                ("started",) if solo_activas else ("started", "candidate"),
                callback)
    if callback:
        callback("leyendo la campaña de destino...")
    b = ofertas(ml, destino, tipo_destino, callback=callback)

    filas = []
    for item, o in a.items():
        d = o.get("descuento")
        if not d or d <= 0:
            filas.append(_fila(item, "saltear",
                               "en el origen no tiene descuento aplicado"))
            continue

        dest = b.get(item)
        if not dest:
            filas.append(_fila(item, "no elegible",
                               "MercadoLibre no la acepta en la campaña "
                               "destino", tipo_destino, destino,
                               desc=d))
            continue

        orig = dest.get("precio_original") or 0
        if not orig:
            filas.append(_fila(item, "saltear", "sin precio de referencia",
                               tipo_destino, destino, dest, desc=d))
            continue

        objetivo = round(orig * (1 - d), 2)
        lo, hi = dest.get("min_precio"), dest.get("max_precio")
        motivo = f"{d:.1%} de descuento, igual que en la campaña de origen"
        if lo and objetivo < lo:
            objetivo, motivo = lo, (
                f"{d:.1%} del origen se pasaba del máximo permitido; queda en "
                f"{1 - lo / orig:.1%}, el mayor que acepta ML")
        elif hi and objetivo > hi:
            objetivo, motivo = hi, (
                f"{d:.1%} del origen no llegaba al mínimo; queda en "
                f"{1 - hi / orig:.1%}, el menor que acepta ML")

        ya = dest.get("precio_promo")
        if dest.get("estado_promo") == "started" and ya and \
                abs(ya - objetivo) < 0.01:
            filas.append(_fila(item, "ya está", "ya tiene ese mismo precio en "
                               "el destino", tipo_destino, destino, dest,
                               objetivo, 1 - objetivo / orig))
            continue

        filas.append(_fila(item, "alta", motivo, tipo_destino, destino, dest,
                           objetivo, 1 - objetivo / orig))

    df = pd.DataFrame(filas, columns=COLUMNAS)
    return df.sort_values(["accion", "descuento"], ascending=[True, False]) \
             .reset_index(drop=True)


def por_regla(ml, campana_id, tipo, tope_descuento=0.05, piso_descuento=None,
              solo_candidatas=True, callback=None):
    """
    Las ofertas de una campaña que cumplen una condicion de descuento.

    `tope_descuento=0.05` -> solo las que piden **hasta 5%**. Es la regla que
    pidio Mariano para las relampago: si ML no pide mas que eso, se aceptan
    todas.

    `piso_descuento` sirve para el caso inverso, cuando lo que se quiere es
    entrar solo si el descuento es grande.
    """
    estados = ("candidate",) if solo_candidatas else ("candidate", "started")
    todas = ofertas(ml, campana_id, tipo, estados, callback)

    filas = []
    for item, o in todas.items():
        d = o.get("descuento")
        if d is None:
            filas.append(_fila(item, "saltear",
                               "ML no informa precio de promoción", tipo,
                               campana_id, o))
            continue
        if tope_descuento is not None and d > tope_descuento:
            filas.append(_fila(item, "no cumple",
                               f"pide {d:.1%} y el tope es "
                               f"{tope_descuento:.1%}", tipo, campana_id, o,
                               o.get("precio_promo"), d))
            continue
        if piso_descuento is not None and d < piso_descuento:
            filas.append(_fila(item, "no cumple",
                               f"solo {d:.1%} y el piso es "
                               f"{piso_descuento:.1%}", tipo, campana_id, o,
                               o.get("precio_promo"), d))
            continue
        filas.append(_fila(item, "alta",
                           f"pide {d:.1%}, dentro del tope de "
                           f"{tope_descuento:.1%}" if tope_descuento is not None
                           else f"pide {d:.1%}",
                           tipo, campana_id, o, o.get("precio_promo"), d))

    df = pd.DataFrame(filas, columns=COLUMNAS)
    return df.sort_values(["accion", "descuento"]).reset_index(drop=True)


def resumen(plan):
    """Cuantas por accion y cuanto descuento promedio, para mostrar antes."""
    if not len(plan):
        return {}
    alta = plan[plan["accion"] == "alta"]
    return {
        "a dar de alta": len(alta),
        "ya estaban": int((plan["accion"] == "ya está").sum()),
        "no elegibles": int((plan["accion"] == "no elegible").sum()),
        "no cumplen la regla": int((plan["accion"] == "no cumple").sum()),
        "salteadas": int((plan["accion"] == "saltear").sum()),
        "descuento promedio": (float(alta["descuento"].mean())
                               if len(alta) else 0.0),
    }


def aplicar(ml, plan, operador="", callback=None, tope=None):
    """
    Da de alta las filas marcadas 'alta'. **Escribe en la cuenta de verdad.**

    Una que falla no corta la corrida y cada alta queda en la auditoria con el
    `offer_id` nuevo, que es lo unico con lo que despues se puede dar de baja.
    """
    import almacen

    hacer = plan[plan["accion"] == "alta"]
    if tope:
        hacer = hacer.head(tope)
    filas, total = [], len(hacer)

    for n, (_, f) in enumerate(hacer.iterrows(), start=1):
        cuerpo = {"promotion_type": f["tipo"]}
        if f.get("campana_id"):
            cuerpo["promotion_id"] = f["campana_id"]
        # Las campañas propias no tienen offer_id; el resto lo exige.
        if f["tipo"] not in ELIGE_EL_VENDEDOR and f.get("oferta_id"):
            cuerpo["offer_id"] = f["oferta_id"]
        if pd.notna(f["precio_promo"]):
            cuerpo["deal_price"] = round(float(f["precio_promo"]), 2)
        # Las relampago exigen cuanto stock se compromete. Va el maximo, que
        # es el disponible: comprometer menos limita la oferta sin ahorrar
        # nada, porque el descuento se aplica igual a lo que se venda.
        if pd.notna(f.get("stock_max")):
            cuerpo["stock"] = int(f["stock_max"])

        nueva = ""
        try:
            r = ml.post(f"/seller-promotions/items/{f['item_id']}",
                        payload=cuerpo, app_version="v2")
            nueva = (r or {}).get("offer_id", "")
            resultado, detalle = "OK", ""
        except Exception as e:                     # noqa: BLE001
            resultado, detalle = "ERROR", f"{type(e).__name__}: {str(e)[:220]}"

        almacen.append_auditoria([{
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "item_id": f["item_id"],
            "campo": f"promocion:{f['tipo']}",
            "valor_anterior": f["precio_original"],
            "valor_nuevo": f["precio_promo"],
            "resultado": resultado if resultado == "OK" else f"ERROR: {detalle}",
            "operador": operador,
            "nota": f"{f['motivo']} · campaña {f['campana_id']} · "
                    f"offer_id={nueva or '—'}",
        }])

        filas.append({**{c: f.get(c) for c in COLUMNAS},
                      "oferta_id_nueva": nueva, "resultado": resultado,
                      "detalle": detalle})
        if callback:
            callback(n, total, f["item_id"])
        time.sleep(PAUSA)

    return pd.DataFrame(filas)


def main():
    ml = Meli(verbose=False)
    pct, args, saltear = None, [], False
    for i, a in enumerate(sys.argv[1:]):
        if saltear:                     # el valor del --tope no es una campaña
            saltear = False
            continue
        if a == "--tope":
            pct = float(sys.argv[i + 2]) / 100
            saltear = True
        elif not a.startswith("--"):
            args.append(a)

    df = campanas(ml)
    if not args:
        print(df.to_string(index=False) if len(df) else "sin campañas")
        return 0

    tipos = dict(zip(df["id"], df["tipo"])) if len(df) else {}

    if len(args) >= 2:
        plan = replicar(ml, args[0], tipos.get(args[0], "SELLER_CAMPAIGN"),
                        args[1], tipos.get(args[1], "SELLER_CAMPAIGN"),
                        callback=lambda m: print(f"  {m}"))
        print(f"\nReplicar {args[0]} -> {args[1]}")
    else:
        plan = por_regla(ml, args[0], tipos.get(args[0], "LIGHTNING"),
                         tope_descuento=pct if pct is not None else 0.05,
                         callback=lambda m: print(f"  {m}"))
        print(f"\nPor regla sobre {args[0]}")

    print()
    for k, v in resumen(plan).items():
        print(f"  {k}: {v:.1%}" if "promedio" in k else f"  {k}: {v}")
    print("\n(simulacro: no se escribió nada)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
