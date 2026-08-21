#!/usr/bin/env python3
"""
El precio minimo de cada publicacion de ERPA. Solo lectura salvo `aplicar`.

    python piso_precio.py             -> que haria
    python piso_precio.py 15          -> con margen objetivo 15%

**La regla** (Mariano, 21/08/2026), para los articulos de ERPA —Suprabond,
Bulit y Somerset, los unicos con precio sugerido—:

  1. Si el **sugerido alcanza** para cubrir los costos con el margen objetivo,
     va el sugerido. Menos que eso no se publica nunca.
  2. Si **no alcanza**, se calcula el precio que cubre costo + lo que cobra
     MercadoLibre + 5% de impuestos + 5% de generales.

Nunca baja precios: si la publicacion ya esta por encima, no se toca.

**Que NO hace.** Devuelve el precio minimo, no el optimo. Si un producto cierra
a $29.136 el piso es $29.136, aunque a $32.999 el margen por unidad sea mayor:
subir el precio cuesta ventas y eso no se mide aca. El optimo alrededor de los
escalones lo busca `tramos.py`, que es otra pregunta.

**El escalon de los $33.000 si esta contemplado**: `precio_minimo` evalua el
envio al precio candidato, asi que cuando el piso cae arriba del umbral ya
tiene adentro los $7.641 que pasa a pagar el vendedor. Las subas que cruzan el
umbral se marcan aparte porque son las mas grandes.
"""

import json
import sys
from pathlib import Path

import pandas as pd

import financiacion as fin
import lista_precios as LP
import precio_minimo as pm
import precios_redondeo
import rentabilidad as rent
import tramos
from catalogo import sku_del_atributo

DIR = Path(__file__).resolve().parent

# Las marcas de ERPA. Son las unicas con precio sugerido, pero el filtro va
# por marca **y** por sugerido: hay 225 publicaciones de Suprabond y Bulit sin
# sugerido cargado, que no pueden pasar por la regla y hay que poder verlas.
MARCAS_ERPA = {"SUPRABOND", "BULIT", "SOMERSET", "SUPRABOND SOMERSET"}

# Diferencias menores a esto son redondeo, no una decision de precio.
TOLERANCIA = 1.0


def marca_de(pub):
    for a in pub.get("attributes") or []:
        if a.get("id") == "BRAND":
            return (a.get("value_name") or "").strip().upper()
    return ""


def margen_a(precio, costo, pct, otros=None, iva=0.21):
    """Lo que queda por unidad vendiendo a `precio`."""
    ingreso = precio / (1 + iva)
    _, otros_monto = rent.otros_conceptos_monto(ingreso, otros)
    return (ingreso - costo - precio * pct - tramos.cargo_fijo(precio)
            - tramos.envio_a_cargo(precio) - otros_monto)


def analizar(ml, pubs=None, sugeridos=None, costos=None, objetivo=0.0,
             otros=None, callback=None):
    """
    El piso de cada publicacion de ERPA y si el precio actual lo respeta.

    `objetivo` es el margen minimo sobre el precio (0 = no perder plata).
    """
    if pubs is None:
        pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    activas = [p for p in pubs if p.get("status") == "active"]

    # **El precio se relee en vivo.** El catalogo guardado tiene la mitad de
    # los precios movidos, y con el viejo el piso se compara contra un numero
    # que ya no existe.
    ids = [p["id"] for p in activas]
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
    activas = [vivos.get(p["id"], p) for p in activas]
    activas = [p for p in activas if p.get("status") == "active"]

    if sugeridos is None:
        sugeridos = LP.mapa_precios()
    if costos is None:
        df_c, _ = rent.costos_guardados()
        costos = {str(r["sku"]).strip().upper(): r["costo"]
                  for _, r in df_c.iterrows() if r.get("costo")}

    de_erpa = [p for p in activas
               if marca_de(p) in MARCAS_ERPA
               or (sugeridos.get(str(sku_del_atributo(p) or "").strip().upper())
                   or {}).get("sugerido")]
    if callback:
        callback(f"{len(de_erpa)} publicaciones de ERPA.")
    pct = fin.tarifas(ml, {p.get("category_id") for p in de_erpa})

    filas = []
    for p in de_erpa:
        sku = str(sku_del_atributo(p) or "").strip().upper()
        sug = (sugeridos.get(sku) or {}).get("sugerido")
        costo = costos.get(sku)
        porc = pct.get((p.get("category_id"), p.get("listing_type_id")))
        precio = float(p.get("price") or 0)
        base = {"sku": sku, "item_id": p["id"], "marca": marca_de(p),
                "titulo": (p.get("title") or "")[:55],
                "precio_actual": precio,
                "sugerido": float(sug) if sug else None,
                "costo": float(costo) if costo else None}

        # Sin alguno de los tres datos la regla no se puede aplicar. Se
        # devuelve igual, con el motivo: son las que hay que ir a cargar.
        falta = ("no tiene sugerido cargado" if not sug else
                 "no tiene costo cargado" if not costo else
                 "ML no da la tarifa de su categoría" if porc is None else
                 "sin precio" if not precio else "")
        if falta:
            filas.append({**base, "piso": None, "manda": "", "falta": None,
                          "sube_pct": None, "cruza_umbral": False,
                          "accion": "revisar", "motivo": falta})
            continue

        p_pct = porc / 100
        m_sug = margen_a(float(sug), float(costo), p_pct, otros)
        alcanza = m_sug >= objetivo * float(sug)
        if alcanza:
            piso, manda = float(sug), "sugerido"
        else:
            piso = pm.precio_minimo(float(costo), p_pct, 0.0, iva=0.21,
                                    otros=otros, objetivo=objetivo)
            manda = "costos"
        if piso is None:
            filas.append({**base, "piso": None, "manda": "costos",
                          "falta": None, "sube_pct": None,
                          "cruza_umbral": False, "accion": "revisar",
                          "motivo": "no hay precio que alcance el objetivo"})
            continue

        piso = precios_redondeo.piso(piso)     # sin centavos, y hacia arriba
        dif = piso - precio
        filas.append({
            **base, "piso": piso, "manda": manda,
            "margen_al_sugerido": round(m_sug, 2),
            "falta": round(dif, 2),
            "sube_pct": round(piso / precio - 1, 4) if precio else None,
            "cruza_umbral": bool(precio < tramos.UMBRAL_ENVIO_GRATIS <= piso),
            "accion": "aplicar" if dif > TOLERANCIA else "ninguna",
            "motivo": "" if dif > TOLERANCIA else "ya está en el piso o arriba",
        })
    return pd.DataFrame(filas)


def aplicar(ml, plan_df, operador="", callback=None):
    """Sube al piso las publicaciones marcadas. Cada una en su propio try."""
    if plan_df is None or not len(plan_df):
        return pd.DataFrame()
    pendientes = plan_df[plan_df["accion"] == "aplicar"]
    nota = f"piso de precio {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, f) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, f)
        item = f["item_id"]
        antes, nuevo = float(f["precio_actual"]), float(f["piso"])
        fila = {"item_id": item, "sku": f.get("sku", ""),
                "titulo": f.get("titulo", ""), "precio_antes": antes,
                "precio_nuevo": nuevo, "manda": f.get("manda", "")}
        try:
            ok, detalle = ml.actualizar_publicacion(
                item, {"price": nuevo}, {"price": antes},
                operador=operador, nota=nota)
            salida.append({**fila, "resultado": "OK" if ok else "ERROR",
                           "detalle": "" if ok else str(detalle)[:200]})
        except Exception as e:                         # noqa: BLE001
            salida.append({**fila, "resultado": "ERROR",
                           "detalle": f"{type(e).__name__}: {str(e)[:150]}"})
    return pd.DataFrame(salida)


def main():
    from meli import Meli
    objetivo = 0.0
    for a in sys.argv[1:]:
        try:
            objetivo = float(a) / 100
        except ValueError:
            pass
    ml = Meli(verbose=False)
    df = analizar(ml, objetivo=objetivo, callback=lambda m: print(f"  {m}"))
    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    ap = df[df["accion"] == "aplicar"]
    print(f"\nmargen objetivo: {objetivo:.0%}")
    print(f"publicaciones de ERPA: {len(df)}")
    print(f"por debajo del piso  : {len(ap)}  ({ap['sku'].nunique()} SKU)")
    if len(ap):
        print(f"  manda el sugerido: {int((ap['manda'] == 'sugerido').sum())}")
        print(f"  manda el costo   : {int((ap['manda'] == 'costos').sum())}")
        print(f"  cruzan los {pes(tramos.UMBRAL_ENVIO_GRATIS)}: "
              f"{int(ap['cruza_umbral'].sum())}")
        print(f"  suba mediana     : {ap['sube_pct'].median():.1%}")
    rev = df[df["accion"] == "revisar"]
    if len(rev):
        print(f"\nno se puede calcular en {len(rev)}:")
        print(rev["motivo"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
