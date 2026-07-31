#!/usr/bin/env python3
"""
Reporte semanal de competencia. Lo manda GitHub Actions los lunes.

    python reporte_competencia.py              -> genera y manda
    python reporte_competencia.py --sin-mail   -> solo genera el HTML
    python reporte_competencia.py 50           -> con otro top

Compara los **100 articulos que mas vendiste** en el ultimo mes contra el
catalogo de MercadoLibre, y **contra la corrida de la semana pasada**. Eso
segundo es lo que lo hace util: el precio de un competidor en abstracto no
dice nada, lo que importa es quien se movio.

Tres bloques, en ese orden a proposito:

  1. **Lo que cambio esta semana** — competidores que bajaron el precio y
     productos donde pasamos de ganar a perder. Es lo unico realmente nuevo.
  2. **Donde estamos mas caros**, ordenado por diferencia.
  3. **Donde somos los mas baratos**, que tambien hay que saberlo: a veces se
     esta regalando margen sin necesidad.

Sobre el alcance: solo entran los articulos con **codigo de barras cargado**,
porque sin EAN no hay forma de encontrarlos en el catalogo. El reporte dice
cuantos quedaron afuera para que no parezca un error.
"""

import sys
from datetime import datetime, timedelta

import pandas as pd

import competencia as comp
import correo
from meli import Meli, MeliError

TOP_POR_DEFECTO = 100
DIAS_VENTAS = 30
ORIGEN = "reporte_semanal"

# Una baja de precio del competidor por debajo de esto es ruido de redondeo.
CAMBIO_MINIMO = 0.02


def _pes(v):
    try:
        return f"${float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def comparar_con_anterior(df, dias_atras=14):
    """
    Cruza la corrida de hoy contra la ultima anterior a `dias_atras` dias.

    Devuelve (bajaron, perdimos_ventaja). `bajaron` son los competidores que
    achicaron el precio; `perdimos_ventaja` los productos donde antes eramos
    los mas baratos y ahora no.
    """
    try:
        hist = comp.historial()
    except Exception:                          # noqa: BLE001
        return pd.DataFrame(), pd.DataFrame()

    if not len(hist) or "fecha" not in hist:
        return pd.DataFrame(), pd.DataFrame()

    hist = hist.copy()
    hist["fecha_dt"] = pd.to_datetime(hist["fecha"], errors="coerce")
    corte = datetime.now() - timedelta(days=1)
    previas = hist[hist["fecha_dt"] < corte]
    if not len(previas):
        return pd.DataFrame(), pd.DataFrame()

    # La foto anterior: la corrida mas reciente que no sea la de hoy.
    ultima = previas["fecha_dt"].max()
    antes = previas[previas["fecha_dt"] == ultima].set_index("ean")

    bajaron, perdimos = [], []
    for _, r in df[df["estado"] == "ok"].iterrows():
        ean = str(r["ean"])
        if ean not in antes.index:
            continue
        a = antes.loc[ean]
        if isinstance(a, pd.DataFrame):
            a = a.iloc[0]

        try:
            precio_antes = float(a["mejor_precio"])
        except (TypeError, ValueError):
            continue
        precio_hoy = float(r["mejor_precio"] or 0)
        if not precio_antes or not precio_hoy:
            continue

        var = (precio_hoy - precio_antes) / precio_antes
        if var <= -CAMBIO_MINIMO:
            bajaron.append({
                "producto": r["producto"], "ean": ean,
                "vendedor": r["mejor_vendedor"],
                "antes": precio_antes, "ahora": precio_hoy, "var": var,
                "nuestro": r["nuestro_precio"],
            })

        era_nuestro = str(a.get("mejor_vendedor", "")) == "NOSOTROS"
        if era_nuestro and r["mejor_vendedor"] != "NOSOTROS":
            perdimos.append({
                "producto": r["producto"], "ean": ean,
                "quien": r["mejor_vendedor"], "su_precio": precio_hoy,
                "nuestro": r["nuestro_precio"],
            })

    return (pd.DataFrame(bajaron).sort_values("var") if bajaron
            else pd.DataFrame(),
            pd.DataFrame(perdimos) if perdimos else pd.DataFrame())


# ------------------------------------------------------------------ html

_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222;
     line-height:1.5;max-width:760px;margin:0 auto;padding:16px}
h2{font-size:17px;margin:26px 0 6px;border-bottom:2px solid #C8552F;
   padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th{background:#C8552F;color:#fff;text-align:left;padding:6px 8px;
   font-weight:600}
td{padding:5px 8px;border-bottom:1px solid #eee}
.num{text-align:right;white-space:nowrap}
.mal{color:#b3261e;font-weight:600}
.bien{color:#146c2e;font-weight:600}
.chico{color:#666;font-size:12px}
"""


def _tabla(filas, columnas):
    if not filas:
        return "<p class='chico'>Nada para mostrar.</p>"
    th = "".join(f"<th>{c[0]}</th>" for c in columnas)
    trs = []
    for f in filas:
        tds = "".join(
            f"<td class='{c[2]}'>{c[1](f)}</td>" if len(c) > 2
            else f"<td>{c[1](f)}</td>" for c in columnas)
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><tr>{th}</tr>{''.join(trs)}</table>"


def armar_html(df, detalle, bajaron, perdimos):
    hoy = datetime.now().strftime("%d/%m/%Y")
    ok = df[df["estado"] == "ok"]
    mas_caros = ok[ok["diferencia"].fillna(0) > 0].sort_values(
        "diferencia", ascending=False)
    somos_baratos = ok[ok["mejor_vendedor"] == "NOSOTROS"]
    sin_publicar = ok[ok["nuestro_precio"].isna()]

    p = [f"<style>{_CSS}</style>",
         f"<h1 style='font-size:20px'>Competencia — {hoy}</h1>",
         f"<p class='chico'>{detalle}</p>",
         "<p>De <b>{}</b> productos medidos: <b class='mal'>{}</b> estamos "
         "más caros que el más barato, <b class='bien'>{}</b> somos los más "
         "baratos.</p>".format(len(ok), len(mas_caros), len(somos_baratos))]

    # ------------------------------------------------ 1. lo que cambio
    p.append("<h2>Lo que cambió esta semana</h2>")
    if len(perdimos):
        p.append("<p class='mal'>Dejamos de ser los más baratos en "
                 f"{len(perdimos)}:</p>")
        p.append(_tabla(perdimos.to_dict("records"), [
            ("Producto", lambda f: f["producto"][:52]),
            ("Ahora el más barato", lambda f: f["quien"]),
            ("Su precio", lambda f: _pes(f["su_precio"]), "num"),
            ("El nuestro", lambda f: _pes(f["nuestro"]), "num")]))
    if len(bajaron):
        p.append(f"<p>Competidores que bajaron el precio ({len(bajaron)}):</p>")
        p.append(_tabla(bajaron.head(15).to_dict("records"), [
            ("Producto", lambda f: f["producto"][:52]),
            ("Vendedor", lambda f: f["vendedor"]),
            ("Antes", lambda f: _pes(f["antes"]), "num"),
            ("Ahora", lambda f: _pes(f["ahora"]), "num"),
            ("Var", lambda f: f"{f['var']:+.0%}", "num")]))
    if not len(perdimos) and not len(bajaron):
        p.append("<p class='chico'>Sin cambios respecto de la corrida "
                 "anterior. Si es la primera, no hay contra qué comparar "
                 "todavía.</p>")

    # ------------------------------------------------ 2. mas caros
    p.append("<h2>Dónde estamos más caros</h2>")
    p.append(_tabla(mas_caros.head(20).to_dict("records"), [
        ("Producto", lambda f: str(f["producto"])[:52]),
        ("El más barato", lambda f: f["mejor_vendedor"]),
        ("Su precio", lambda f: _pes(f["mejor_precio"]), "num"),
        ("El nuestro", lambda f: _pes(f["nuestro_precio"]), "num"),
        ("Dif", lambda f: f"{f['diferencia']:+.0%}", "num")]))

    # ------------------------------------------------ 3. mas baratos
    p.append("<h2>Dónde somos los más baratos</h2>")
    p.append("<p class='chico'>Vale mirarlo: si la diferencia con el segundo "
             "es grande, puede haber margen para subir sin perder la "
             "posición.</p>")
    p.append(_tabla(somos_baratos.head(15).to_dict("records"), [
        ("Producto", lambda f: str(f["producto"])[:52]),
        ("Nuestro precio", lambda f: _pes(f["mejor_precio"]), "num"),
        ("Competidores", lambda f: int(f["competidores"] or 0), "num")]))

    if len(sin_publicar):
        p.append(f"<h2>No los publicamos en catálogo ({len(sin_publicar)})</h2>")
        p.append("<p class='chico'>Están en el catálogo de MercadoLibre y "
                 "otros los venden, pero nosotros no tenemos publicación ahí.</p>")
        p.append(_tabla(sin_publicar.head(10).to_dict("records"), [
            ("Producto", lambda f: str(f["producto"])[:52]),
            ("El más barato", lambda f: f["mejor_vendedor"]),
            ("Su precio", lambda f: _pes(f["mejor_precio"]), "num")]))

    p.append("<p class='chico' style='margin-top:24px'>Generado "
             "automáticamente. Solo entran los artículos con código de barras "
             "cargado.</p>")
    return "\n".join(p)


def main():
    top = next((int(a) for a in sys.argv[1:] if a.isdigit()), TOP_POR_DEFECTO)
    sin_mail = "--sin-mail" in sys.argv

    ml = Meli(verbose=False)
    print(f"Buscando los {top} más vendidos de {DIAS_VENTAS} días...")
    eans, detalle, _ = comp.eans_mas_vendidos(ml, n=top, dias=DIAS_VENTAS)
    print(f"  {detalle}")
    if not eans:
        print("Ninguno tiene código de barras cargado. No hay reporte.")
        return 0

    print(f"Comparando {len(eans)} contra el catálogo...")
    df = comp.analizar(ml, eans,
                       callback=lambda i, t, e: print(f"  {i}/{t}", end="\r"))
    print(" " * 40)

    bajaron, perdimos = comparar_con_anterior(df)
    print(f"  bajaron el precio: {len(bajaron)} · "
          f"dejamos de ser los más baratos: {len(perdimos)}")

    ok, det = comp.guardar_comparacion(df, origen=ORIGEN)
    print(f"  historial: {'OK' if ok else 'no se guardó'} — {det}")

    html = armar_html(df, detalle, bajaron, perdimos)
    salida = "reporte_competencia.html"
    with open(salida, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  reporte guardado en {salida}")

    if sin_mail:
        return 0

    medidos = len(df[df["estado"] == "ok"])
    caros = len(df[(df["estado"] == "ok") & (df["diferencia"].fillna(0) > 0)])
    asunto = (f"Competencia — {caros} de {medidos} más caros que el más barato"
              + (f" · {len(perdimos)} perdimos el primer puesto"
                 if len(perdimos) else ""))
    enviado, det = correo.enviar(asunto, html)
    print(f"  mail: {'ENVIADO' if enviado else 'NO enviado'} — {det}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
