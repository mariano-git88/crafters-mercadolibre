#!/usr/bin/env python3
"""
Mejor precio de la competencia por EAN.

Se sube una planilla con EAN (codigo de barras) y devuelve, para cada uno,
quien lo vende mas barato en MercadoLibre y a cuanto.

    python competencia.py            -> prueba con EAN del propio catalogo
    python competencia.py 7793300230309 7793300423084

Como funciona y que limite tiene:

  El buscador libre de ML (`/sites/MLA/search`) devuelve **403**: ML lo cerro
  para aplicaciones. La via que si funciona es el **catalogo**:

      EAN -> /products/search -> catalog_product_id -> /products/{id}/items

  O sea que vemos a todos los que venden ese producto **dentro del catalogo**
  de ML. Si alguien lo publica por fuera del catalogo, no aparece. Para
  productos con codigo de barras conocido la cobertura es buena (11 de 15 en
  las pruebas), pero no es literalmente "todo MercadoLibre".
"""

import re
import sys
from pathlib import Path

import pandas as pd

from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

COLS_EAN = ["ean", "gtin", "codigo de barras", "codigo_barras", "barcode",
            "codigo", "código"]

# Los nicknames se repiten muchisimo entre EAN: sin cache seria una llamada
# por competidor por producto.
_cache_nicks = {}


def limpiar_eans(valor):
    """
    Un EAN por elemento. Las publicaciones de CRAFTERS a veces traen varios
    separados por coma ("779...220,779...723"), y asi no matchean con nada.
    """
    if valor is None:
        return []
    crudo = str(valor).strip()
    if not crudo or crudo.lower() in ("nan", "none"):
        return []
    return [t for t in re.split(r"[,;/\s]+", crudo) if t.isdigit() and len(t) >= 8]


def nickname(ml, seller_id):
    if seller_id in _cache_nicks:
        return _cache_nicks[seller_id]
    try:
        u = ml.get(f"/users/{seller_id}")
        nick = u.get("nickname") or str(seller_id)
        rep = (u.get("seller_reputation") or {}).get("level_id") or ""
    except MeliError:
        nick, rep = str(seller_id), ""
    _cache_nicks[seller_id] = (nick, rep)
    return _cache_nicks[seller_id]


def producto_de_ean(ml, ean):
    """EAN -> producto de catalogo. Devuelve (id, nombre) o (None, None)."""
    try:
        r = ml.get("/products/search", site_id=SITE_ID, q=ean)
    except MeliError:
        return None, None
    res = r.get("results") or []
    if not res:
        return None, None
    return res[0].get("id"), res[0].get("name")


def competidores(ml, product_id):
    """Publicaciones que venden ese producto de catalogo."""
    try:
        r = ml.get(f"/products/{product_id}/items")
    except MeliError:
        return []
    return r.get("results") or []


def analizar(ml, eans, callback=None):
    """
    Para cada EAN devuelve una fila con el mejor precio, quien lo tiene y
    donde estamos nosotros.
    """
    filas = []
    total = len(eans)

    for i, ean in enumerate(eans, start=1):
        if callback:
            callback(i, total, ean)

        pid, nombre = producto_de_ean(ml, ean)
        if not pid:
            filas.append({"ean": ean, "producto": "", "product_id": "",
                          "competidores": 0, "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_catalogo",
                          "detalle": "El EAN no tiene producto en el catálogo de ML."})
            continue

        items = competidores(ml, pid)
        if not items:
            filas.append({"ean": ean, "producto": nombre or "", "product_id": pid,
                          "competidores": 0, "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_vendedores",
                          "detalle": "El producto existe pero nadie lo está vendiendo."})
            continue

        con_precio = sorted((x for x in items if x.get("price")),
                            key=lambda x: x["price"])
        if not con_precio:
            filas.append({"ean": ean, "producto": nombre or "", "product_id": pid,
                          "competidores": len(items), "mejor_precio": None,
                          "mejor_vendedor": "", "reputacion": "",
                          "nuestro_precio": None, "diferencia": None,
                          "posicion": None, "estado": "sin_precios",
                          "detalle": "Ninguna publicación informa precio."})
            continue

        mejor = con_precio[0]
        nick, rep = nickname(ml, mejor.get("seller_id"))

        nuestras = [x for x in con_precio if x.get("seller_id") == ml.user_id]
        nuestro = nuestras[0]["price"] if nuestras else None
        posicion = (con_precio.index(nuestras[0]) + 1) if nuestras else None
        diferencia = ((nuestro - mejor["price"]) / mejor["price"]
                      if nuestro else None)

        somos_nosotros = mejor.get("seller_id") == ml.user_id
        filas.append({
            "ean": ean,
            "producto": (nombre or "")[:70],
            "product_id": pid,
            "competidores": len(con_precio),
            "mejor_precio": mejor["price"],
            "mejor_vendedor": "NOSOTROS" if somos_nosotros else nick,
            "reputacion": rep,
            "nuestro_precio": nuestro,
            "diferencia": diferencia,
            "posicion": posicion,
            "estado": "ok",
            "detalle": ("Somos los más baratos." if somos_nosotros
                        else ("No publicamos este producto en catálogo."
                              if nuestro is None
                              else f"Estamos {diferencia:+.1%} sobre el más barato.")),
        })

    cols = ["ean", "producto", "mejor_precio", "mejor_vendedor", "reputacion",
            "nuestro_precio", "diferencia", "posicion", "competidores",
            "estado", "detalle", "product_id"]
    return pd.DataFrame(filas, columns=cols)


def leer_planilla_eans(archivo):
    """Lee la planilla y devuelve la lista de EAN, ya separados y limpios."""
    nombre = getattr(archivo, "name", str(archivo)).lower()
    df = (pd.read_csv(archivo, dtype=str) if nombre.endswith(".csv")
          else pd.read_excel(archivo, dtype=str))

    normal = {str(c).strip().lower(): c for c in df.columns}
    col = next((normal[c] for c in COLS_EAN if c in normal), None)
    if col is None:
        # Sin encabezado reconocible, buscamos la columna con mas numeros largos.
        mejor, puntaje = None, 0
        for c in df.columns:
            p = df[c].dropna().astype(str).str.match(r"^\d{8,14}$").mean()
            if p > puntaje:
                mejor, puntaje = c, p
        if puntaje < 0.3:
            raise ValueError(
                f"No encontré una columna de EAN. Columnas: {list(df.columns)}")
        col = mejor

    eans = []
    for v in df[col]:
        eans.extend(limpiar_eans(v))
    return list(dict.fromkeys(eans)), col


def main():
    ml = Meli(verbose=False)
    if len(sys.argv) > 1:
        eans = [e for a in sys.argv[1:] for e in limpiar_eans(a)]
    else:
        import json
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
        eans = []
        for p in pubs:
            if p.get("status") != "active":
                continue
            for a in p.get("attributes") or []:
                if a.get("id") == "GTIN":
                    eans.extend(limpiar_eans(a.get("value_name")))
                    break
            if len(eans) >= 8:
                break

    print(f"Analizando {len(eans)} EAN...\n")
    df = analizar(ml, eans,
                  callback=lambda i, t, e: print(f"  {i}/{t} {e}", end="\r"))
    print(" " * 40)
    cols = ["ean", "mejor_precio", "mejor_vendedor", "nuestro_precio",
            "diferencia", "posicion", "competidores", "estado"]
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
