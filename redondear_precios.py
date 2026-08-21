#!/usr/bin/env python3
"""
Saca los centavos de los precios ya publicados.

    python redondear_precios.py            -> que haria, sin tocar nada
    python redondear_precios.py --aplicar  -> los escribe

Las herramientas ya publican sin decimales, pero **eso no arregla lo que ya
esta**: medido el 21/08/2026, **467 de 2.109 publicaciones activas** tenian
centavos y siguen asi hasta que algo las toque.

**Redondear no es inofensivo cerca de un escalon.** El costo que cobra ML es
escalonado: el cargo fijo salta en $16.000, $24.000 y $33.000, y desde
**$33.000 el envio gratis lo paga el vendedor** —$7.641 de mediana—. Una
publicacion en $32.999,60 redondeada "al mas proximo" pasa a $33.000 y cruza
los dos escalones de una: se gana $0,40 de precio y se pierden miles en envio.

Por eso la regla no es "al mas proximo" a secas:

    se redondea al mas proximo, **salvo que eso cruce un escalon hacia
    arriba**; en ese caso se baja al entero anterior.

Bajar nunca cruza un escalon hacia arriba, asi que la salida siempre es segura.
"""

import json
import sys
from pathlib import Path

import pandas as pd

import precios_redondeo
import tramos

DIR = Path(__file__).resolve().parent


def _costo(precio):
    """Lo que cobra ML por vender a `precio`, sin la parte porcentual."""
    return tramos.cargo_fijo(precio) + tramos.envio_a_cargo(precio)


def redondeo_seguro(precio):
    """
    El entero al que conviene mover `precio`. Devuelve (nuevo, cruzaba).

    `cruzaba` es True cuando el redondeo natural habria cruzado un escalon y
    hubo que bajar en vez de subir. Sirve para mostrarlo: son los casos donde
    "al mas proximo" costaba plata.
    """
    p = float(precio)
    if p.is_integer():
        return p, False
    natural = precios_redondeo.cerca(p)
    if _costo(natural) <= _costo(p):
        return natural, False
    return precios_redondeo.techo(p), True


def analizar(ml, pubs=None, callback=None):
    """Las publicaciones activas con centavos y a que precio irian."""
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    # **El precio se relee en vivo.** El catalogo guardado tenia la mitad de
    # los precios movidos: redondear sobre un precio viejo escribe un numero
    # que nadie decidio.
    ids = [p["id"] for p in pubs if p.get("status") == "active"]
    filas = []
    for i in range(0, len(ids), 20):
        if callback:
            callback(f"Leyendo precios... {i}/{len(ids)}")
        try:
            lote = ml.get("/items", ids=",".join(ids[i:i + 20]))
        except Exception:                              # noqa: BLE001
            continue
        for w in lote:
            b = (w or {}).get("body") or {}
            precio = b.get("price")
            if b.get("status") != "active" or precio is None:
                continue
            if float(precio).is_integer():
                continue
            nuevo, cruzaba = redondeo_seguro(precio)
            filas.append({
                "item_id": b["id"],
                "titulo": (b.get("title") or "")[:60],
                "precio_actual": float(precio),
                "precio_nuevo": nuevo,
                "diferencia": round(nuevo - float(precio), 2),
                "bajado_por_escalon": cruzaba,
                "listing_type_id": b.get("listing_type_id"),
                "accion": "aplicar" if nuevo != float(precio) else "ninguna",
            })
    return pd.DataFrame(filas)


def aplicar(ml, plan_df, operador="", callback=None):
    """Escribe los precios redondeados. Cada uno en su propio try."""
    if plan_df is None or not len(plan_df):
        return pd.DataFrame()

    pendientes = plan_df[plan_df["accion"] == "aplicar"]
    nota = f"redondeo sin centavos {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)
        item = f["item_id"]
        antes, nuevo = float(f["precio_actual"]), float(f["precio_nuevo"])
        fila = {"item_id": item, "titulo": f.get("titulo", ""),
                "precio_antes": antes, "precio_nuevo": nuevo}
        try:
            ok, detalle = ml.actualizar_publicacion(
                item, {"price": nuevo}, {"price": antes},
                operador=operador, nota=nota)
            salida.append({**fila,
                           "resultado": "OK" if ok else "ERROR",
                           "detalle": "" if ok else str(detalle)[:200]})
        except Exception as e:                         # noqa: BLE001
            salida.append({**fila, "resultado": "ERROR",
                           "detalle": f"{type(e).__name__}: {str(e)[:150]}"})
    return pd.DataFrame(salida)


def main():
    from meli import Meli
    ml = Meli(verbose=False)
    df = analizar(ml, callback=lambda m: print(f"  {m}"))
    if not len(df):
        print("No hay precios con centavos.")
        return 0

    listas = df[df["accion"] == "aplicar"]
    bajadas = listas[listas["bajado_por_escalon"]]
    print(f"\n{len(listas)} publicaciones con centavos.")
    print(f"  se bajaron para no cruzar un escalón: {len(bajadas)}")
    print(f"  movimiento total: ${listas['diferencia'].sum():,.2f}")
    print(df.head(15).to_string(index=False))

    if "--aplicar" not in sys.argv:
        print("\nCorré con --aplicar para escribirlos.")
        return 0
    res = aplicar(ml, df, operador="script",
                  callback=lambda i, t, f: print(f"  {i}/{t} {f['item_id']}"))
    ok = int((res["resultado"] == "OK").sum())
    print(f"\n{ok} de {len(res)} aplicados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
