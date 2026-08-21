#!/usr/bin/env python3
"""
Precio minimo viable: a que precio hay que estar para no perder plata.

    python precio_minimo.py            -> margen objetivo 15%
    python precio_minimo.py 25         -> margen objetivo 25%

Es la herramienta inversa a `buybox.py`. Buy Box pregunta "hasta donde puedo
bajar"; esta pregunta **"desde donde no puedo bajar"**, que con los costos
reales de CRAFTERS resulto ser el problema grande: 197 SKU vendieron a perdida
en 90 dias.

**El detalle que hace toda la diferencia: los escalones.** MercadoLibre cobra
un porcentaje mas un cargo fijo por unidad, y ese cargo salta en escalones
(ver `tramos.py`). Eso hace que el precio minimo NO se pueda despejar con una
sola cuenta: hay que resolverlo por tramo y quedarse con el menor precio que
efectivamente cierra.

**El envio es el otro escalon, y es mas grande que el cargo fijo.** Desde
$33.000 el envio deja de pagarlo el comprador y pasa a pagarlo el vendedor:
~$7.641 de mediana, contra los $3.005 de cargo fijo que se ahorran. O sea que
cruzar $33.000 **encarece** el producto en ~$4.600 por unidad.

Durante meses esta funcion recibio el envio como una constante por SKU (el
promedio historico) y por eso devolvia precios minimos justo arriba de
$33.000, creyendo que ahi se ahorraba plata. Un producto que hoy vende debajo
del umbral tiene promedio ~0, y al empujarlo por encima se seguia calculando
con envio cero. Ahora el envio pasa por `tramos.envio_a_cargo()`, que lo
evalua **al precio candidato**.

La cuenta, con el ingreso ya sin IVA:

    margen = ingreso*(1 - otros) - precio*pct - cargo_fijo(precio) \\
             - envio_a_cargo(precio) - costo

y se busca el precio mas chico donde `margen >= objetivo * precio`.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import precios_redondeo

from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent


def _bandas_topes():
    from tramos import TRAMOS
    return TRAMOS


def _bandas():
    """Los tramos de cargo fijo como (desde, hasta, fijo)."""
    bandas, desde = [], 0.0
    for tope, fijo in _bandas_topes():
        bandas.append((desde, float(tope), float(fijo)))
        desde = float(tope)
    return bandas


def precio_minimo(costo, pct, envio, iva=0.21, otros=None, objetivo=0.15):
    """
    El precio mas bajo que deja `objetivo` de margen sobre el precio.

    Devuelve None si no hay precio que alcance: pasa cuando la comision
    porcentual mas los conceptos porcentuales mas el objetivo se comen todo
    el ingreso, y ahi subir el precio no arregla nada.

    **Hay TRES escalones distintos que resolver, no uno.** El cargo fijo de ML
    salta por tramos de precio; el costo logistico es 10% **o $9.000, lo que
    sea menor**, asi que arriba de cierto ingreso deja de ser porcentual y
    pasa a ser un monto fijo; y desde $33.000 el **envio** deja de pagarlo el
    comprador y pasa a pagarlo el vendedor. La ecuacion cambia de forma en
    cada combinacion, asi que se resuelve en cada una y se toma el menor
    precio que de verdad cierra en su propio tramo.

    `envio` es el promedio historico medido del SKU. **No se usa tal cual**:
    se pasa por `tramos.envio_a_cargo()`, que decide si a ese precio el envio
    corre por cuenta del vendedor. Usarlo como constante es lo que hacia que
    esta funcion devolviera precios minimos apenas por encima de $33.000
    creyendo que ahi se ahorraba el cargo fijo, cuando en realidad ahi arranca
    un envio de ~$7.641 que se come el ahorro cuatro veces.

    Funciona porque `UMBRAL_ENVIO_GRATIS` cae justo en un borde de `TRAMOS`:
    dentro de cada banda el envio es constante.
    """
    from rentabilidad import OTROS_CONCEPTOS, TOPE_LOGISTICO
    from tramos import UMBRAL_ENVIO_GRATIS, envio_a_cargo

    assert any(t == UMBRAL_ENVIO_GRATIS for t, _ in _bandas_topes()), (
        "El umbral de envio gratis dejo de coincidir con un borde de TRAMOS: "
        "el envio ya no es constante dentro de cada banda y hay que partir "
        "las bandas antes de resolver.")

    o = dict(OTROS_CONCEPTOS)
    if otros:
        o.update(otros)

    # Ingreso a partir del cual el logistico queda topeado.
    corte_log = (TOPE_LOGISTICO / o["logistico"]) if o["logistico"] else float("inf")

    # Cada regimen aporta: (parte porcentual del ingreso, monto fijo extra).
    regimenes = [
        # logistico porcentual: vale mientras el ingreso no pase el corte
        (o["impuestos"] + o["logistico"] + o["general"], 0.0, 0.0, corte_log),
        # logistico topeado: vale de ahi para arriba
        (o["impuestos"] + o["general"], TOPE_LOGISTICO, corte_log, float("inf")),
    ]

    candidatos = []
    for tasa, extra, ing_desde, ing_hasta in regimenes:
        k = (1 - tasa) / (1 + iva) - pct - objetivo
        if k <= 0:
            continue
        for desde, hasta, fijo in _bandas():
            # El envio es constante dentro de la banda: se evalua en `desde`.
            base = fijo + envio_a_cargo(desde, envio) + costo + extra
            p = base / k
            ingreso = p / (1 + iva)
            # Solo vale si el precio cae en la banda de cargo fijo Y en el
            # regimen logistico con los que se calculo.
            if desde <= p < hasta and ing_desde <= ingreso < ing_hasta:
                candidatos.append(p)
            # Los bordes tambien son candidatos: cruzar un escalon puede hacer
            # viable un precio que dentro del tramo anterior no cerraba.
            for borde in (desde, ing_desde * (1 + iva)):
                if borde <= 0:
                    continue
                ing_b = borde / (1 + iva)
                tasa_b, extra_b = ((o["impuestos"] + o["logistico"]
                                    + o["general"], 0.0)
                                   if ing_b < corte_log
                                   else (o["impuestos"] + o["general"],
                                         TOPE_LOGISTICO))
                margen_b = (borde * (1 - tasa_b) / (1 + iva) - borde * pct
                            - cargo_fijo_de(borde)
                            - envio_a_cargo(borde, envio) - costo - extra_b)
                if margen_b >= objetivo * borde:
                    candidatos.append(borde)

    return min(candidatos) if candidatos else None


def cargo_fijo_de(precio):
    from tramos import cargo_fijo
    return cargo_fijo(precio)


def analizar(costos_df, cargos_df, pubs, iva=0.21, otros_conceptos=None,
             objetivo=0.15, precios_lista=None):
    """
    Por SKU: precio actual, precio minimo viable y cuanto habria que subir.

    Usa el porcentaje de comision **real** de cada SKU, despejado de lo que ML
    cobro (asi vale igual para Clasica que para Premium), y el envio promedio
    medido de las ventas.

    **El costo va pleno, sin el descuento del proveedor**, y es a proposito:
    esta funcion decide a que precio estar, y un precio calculado contando con
    un descuento que puede no estar convierte la venta en perdida. El descuento
    se mira en rentabilidad, que es donde se pregunta cuanto se gano.

    `precios_lista`: dict sku -> {'sugerido': ...} de `lista_precios`. Cambia
    la pregunta de fondo. Sin la lista, esto despejaba el precio desde el costo
    y proponia subir hasta ahi. Con la lista hay un **piso comercial** dado, y
    lo que hay que hacer es que ninguna publicacion quede por debajo.

    **Es un minimo, no un objetivo.** Estar por encima esta permitido y no se
    corrige: se puede cobrar lo que se quiera mientras no se baje del piso.
    Por eso `precio_objetivo` es `max(precio_actual, minimo_de_lista)` y no el
    minimo a secas — bajar al piso a un producto que hoy esta mas caro seria
    resignar margen sin que nadie lo haya pedido.

    Los SKU que **no estan en la lista** no tienen piso comercial: ahi sigue
    mandando el minimo despejado del costo, que es el unico criterio que hay.
    """
    from rentabilidad import OTROS_CONCEPTOS, otros_conceptos_monto
    from resolver import indexar_por_sku, resolver_precio
    from tramos import cargo_fijo

    otros = dict(OTROS_CONCEPTOS)
    if otros_conceptos:
        otros.update(otros_conceptos)
    precios_lista = precios_lista or {}

    pct, envio, unidades = {}, {}, {}
    for _, f in cargos_df.iterrows():
        p = f["precio_prom"] or 0
        if p > 0:
            pct[f["sku"]] = max(((f["comision_prom"] or 0) - cargo_fijo(p)) / p,
                                0.0)
        envio[f["sku"]] = f["envio_prom"] or 0.0
        unidades[f["sku"]] = int(f["unidades_vendidas"] or 0)

    indice = indexar_por_sku(pubs)

    filas = []
    for _, f in costos_df.iterrows():
        sku, costo = f["sku"], float(f["costo"])
        res = resolver_precio(sku, indice)
        if not res.ok:
            continue
        pub = res.destinos[0]
        actual = pub.get("price")
        if not actual:
            continue

        p = pct.get(sku)
        if p is None:
            # Sin ventas no hay comision medida: se usa la base de Clasica.
            from tramos import PORCENTAJE
            p = PORCENTAJE
        e = envio.get(sku, 0.0)

        minimo = precio_minimo(costo, p, e, iva=iva, otros=otros,
                               objetivo=objetivo)

        def margen_a(precio):
            ingreso = precio / (1 + iva)
            _, otros_monto = otros_conceptos_monto(ingreso, otros)
            return (ingreso - otros_monto - precio * p
                    - cargo_fijo(precio) - e - costo)

        m_hoy = margen_a(actual)
        m_min = margen_a(minimo) if minimo else None

        if minimo is None:
            diag = "no cierra a ningún precio"
        elif actual >= minimo:
            diag = "ok"
        else:
            diag = "hay que subir"

        # ---------------------------------------------- contra la lista
        #
        # El precio de la lista es un MINIMO, no un techo: estar por encima
        # esta bien y no hay que corregirlo. El unico estado que pide accion
        # es estar por debajo.
        sugerido = (precios_lista.get(sku) or {}).get("sugerido")
        m_sug = margen_a(sugerido) if sugerido else None

        if not sugerido:
            estado_lista = "sin lista"
        elif abs(actual - sugerido) <= 0.01 * sugerido:
            estado_lista = "en el mínimo"
        elif actual < sugerido:
            estado_lista = "debajo del mínimo"
        else:
            estado_lista = "arriba del mínimo"

        # El precio a publicar. Con lista: nunca por debajo del minimo, pero
        # si el precio de hoy ya esta por encima se respeta, porque subir por
        # encima del minimo esta permitido y bajarlo al minimo seria resignar
        # margen sin motivo. Sin lista (otros proveedores) manda el minimo
        # despejado del costo, que es el unico criterio que hay.
        objetivo_precio = max(actual, sugerido) if sugerido else minimo

        from buybox import marca as marca_de
        filas.append({
            "sku": sku,
            "item_id": pub["id"],
            "marca": marca_de(pub),
            "titulo": (pub.get("title") or "")[:60],
            "diagnostico": diag,
            "precio_actual": actual,
            "precio_minimo": minimo,
            "precio_sugerido": sugerido,
            "precio_objetivo": objetivo_precio,
            "estado_lista": estado_lista,
            # Si el precio de lista no llega al margen objetivo, el problema
            # no es el precio sino el costo: subirlo mas no esta permitido.
            "lista_cubre_margen": (bool(m_sug is not None
                                        and m_sug >= objetivo * sugerido)
                                   if sugerido else None),
            "margen_al_sugerido": m_sug,
            "mover_a_lista": ((sugerido - actual) if sugerido else 0.0),
            "mover_a_lista_pct": (((sugerido - actual) / actual)
                                  if (sugerido and actual) else 0.0),
            "subir": (minimo - actual) if (minimo and minimo > actual) else 0.0,
            "subir_pct": ((minimo - actual) / actual
                          if (minimo and minimo > actual) else 0.0),
            "costo": costo,
            "envio_prom": e,
            "comision_pct": p,
            "margen_hoy": m_hoy,
            "margen_hoy_pct": m_hoy / actual if actual else None,
            "margen_al_minimo": m_min,
            "unidades": unidades.get(sku, 0),
            # Cruzar el escalon del cargo fijo puede ser justamente el motivo
            # de la suba: conviene que se vea.
            "cruza_escalon": (cargo_fijo(actual) != cargo_fijo(minimo)
                              if minimo else False),
            "perdida_periodo": (m_hoy * unidades.get(sku, 0)
                                if m_hoy < 0 else 0.0),
        })

    df = pd.DataFrame(filas)
    if not len(df):
        return df
    orden = {"hay que subir": 0, "no cierra a ningún precio": 1, "ok": 2}
    df["_o"] = df["diagnostico"].map(orden)
    return df.sort_values(["_o", "perdida_periodo"],
                          ascending=[True, True]).drop(columns=["_o"])


def resumen(df):
    if not len(df):
        return {}
    subir = df[df["diagnostico"] == "hay que subir"]
    r = {
        "total": len(df),
        "ok": int((df["diagnostico"] == "ok").sum()),
        "a_subir": len(subir),
        "no_cierran": int((df["diagnostico"] == "no cierra a ningún precio").sum()),
        "perdiendo_hoy": int((df["margen_hoy"] < 0).sum()),
        "perdida_periodo": float(df["perdida_periodo"].sum()),
        "suba_mediana": float(subir["subir_pct"].median()) if len(subir) else 0.0,
        "cruzan_escalon": int(subir["cruza_escalon"].sum()) if len(subir) else 0,
    }
    if "estado_lista" in df:
        con = df[df["estado_lista"].ne("sin lista")]
        r.update({
            "con_lista": len(con),
            "sin_lista": int((df["estado_lista"] == "sin lista").sum()),
            "al_precio_de_lista": int(
                (df["estado_lista"] == "en el mínimo").sum()),
            # El unico que pide accion: por debajo del minimo no se puede estar.
            "debajo_de_lista": int(
                (df["estado_lista"] == "debajo del mínimo").sum()),
            # Estar arriba esta permitido; se cuenta para saber cuantos son,
            # no porque haya algo que corregir.
            "arriba_de_lista": int(
                (df["estado_lista"] == "arriba del mínimo").sum()),
            # Los que aunque se publiquen al precio de lista no llegan al
            # margen: ahi el problema es el costo, no el precio.
            "lista_no_alcanza": int((con["lista_cubre_margen"] == False).sum()),  # noqa: E712
        })
    return r


# Tope duro de suba, aunque el criterio pida mas. Con la estructura completa
# el modelo pide subas enormes en muchos SKU; esto obliga a que las mas
# violentas pasen por una decision explicita y no por un lote de 800.
TECHO_DE_SUBA = 1.00


def seleccionar(df, suba_maxima=0.30, unidades_minimas=1, marcas=None,
                items=None, solo_perdida=True):
    """
    Las publicaciones a las que subirles el precio.

    `solo_perdida` deja afuera las que hoy ganan plata pero no llegan al
    objetivo: son las menos urgentes y las que mas ruido meten en un lote.
    """
    if not len(df):
        return df

    sel = df[
        (df["diagnostico"] == "hay que subir")
        & (df["subir_pct"] > 0)
        & (df["subir_pct"] <= min(suba_maxima, TECHO_DE_SUBA))
        & (df["unidades"] >= unidades_minimas)
    ].copy()

    if solo_perdida:
        sel = sel[sel["margen_hoy"] < 0]
    if marcas:
        sel = sel[sel["marca"].isin(marcas)]
    if items is not None:
        sel = sel[sel["item_id"].isin(list(items))]

    return sel.sort_values("perdida_periodo")


def planilla_de_precios(seleccion, columna="precio_objetivo"):
    """
    Convierte la seleccion en la planilla que consume `actualizador.simular()`.

    Se reusa ese motor a proposito: ya tiene el resolver de SKU, el aviso de
    cambios mayores al 50% y la auditoria. No hace falta otra ruta de
    escritura.

    Por defecto usa `precio_objetivo`, que es el precio de lista cuando el SKU
    esta en la lista de Suprabond y el minimo despejado cuando no. Pasar
    `columna="precio_minimo"` para el comportamiento viejo.
    """
    col = columna if columna in seleccion else "precio_minimo"
    return pd.DataFrame({
        "sku": seleccion["sku"],
        # Sin centavos, y hacia ARRIBA: es un minimo, redondear para
        # abajo lo perforaria.
        "precio": seleccion[col].map(precios_redondeo.piso),
    })


def seleccionar_a_precio_de_lista(df, unidades_minimas=0, marcas=None,
                                  items=None, solo_subas=True):
    """
    Las publicaciones que estan por DEBAJO del minimo que dice la lista.

    Es la accion mas directa que habilita la lista: no hay que despejar nada,
    el precio ya esta decidido y lo unico que falta es llevarlo ahi.

    **Las que estan por encima del minimo no aparecen, y no es un olvido**: el
    numero de la lista es un piso, no un techo. Estar mas caro esta permitido
    y bajarlas al minimo seria resignar margen sin que nadie lo haya pedido.
    `solo_subas=False` las incluye igual, para revisarlas a mano.
    """
    if not len(df) or "estado_lista" not in df:
        return df.iloc[0:0] if len(df) else df

    sel = df[df["estado_lista"].isin(
        ["debajo del mínimo"] if solo_subas
        else ["debajo del mínimo", "arriba del mínimo"])].copy()

    if unidades_minimas:
        sel = sel[sel["unidades"] >= unidades_minimas]
    if marcas:
        sel = sel[sel["marca"].isin(marcas)]
    if items is not None:
        sel = sel[sel["item_id"].isin(list(items))]

    return sel.sort_values("mover_a_lista_pct", ascending=False)


def main():
    objetivo = (float(sys.argv[1]) / 100
                if len(sys.argv) > 1 else 0.15)
    ml = Meli(verbose=False)

    import rentabilidad as rent
    costos, cuando = rent.costos_guardados()
    if not len(costos):
        print("No hay planilla de costos guardada. Subila desde la app.")
        return 1
    print(f"Costos: {len(costos)} SKU (actualizada {cuando})")

    ordenes = rent.traer_historico(ml, 90)
    envios = rent.traer_costos_envio(ml, ordenes, muestra_por_sku=5)
    cargos = rent.cargos_por_sku(ordenes, envios)
    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))

    import lista_precios as lp
    mapa, cuando_lista = lp.mapa_precios(), ""
    try:
        _, cuando_lista = lp.guardada()
    except Exception:  # noqa: BLE001
        pass
    if mapa:
        print(f"Lista de precios: {len(mapa)} SKU")
    else:
        print("Sin lista de precios cargada: manda el minimo despejado.")

    df = analizar(costos, cargos, pubs, objetivo=objetivo, precios_lista=mapa)
    r = resumen(df)
    pes = lambda v: "—" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")

    print("\n" + "=" * 72)
    print(f"PRECIO MINIMO VIABLE  (margen objetivo {objetivo:.0%})")
    print("=" * 72)
    print(f"  SKU analizados            {r['total']:>6}")
    print(f"  Ya estan bien             {r['ok']:>6}")
    print(f"  Hay que subir             {r['a_subir']:>6}")
    print(f"  No cierran a ningun precio{r['no_cierran']:>6}")
    print(f"  Perdiendo plata hoy       {r['perdiendo_hoy']:>6}")
    print(f"  Plata perdida en el periodo  {pes(r['perdida_periodo']):>14}")
    print(f"  Suba mediana necesaria       {r['suba_mediana']:>13.0%}")
    print(f"  De esas, cruzan escalon   {r['cruzan_escalon']:>6}")

    if r.get("con_lista"):
        print("\n" + "-" * 72)
        print("  CONTRA EL MINIMO DE LA LISTA")
        print("-" * 72)
        print(f"  Con minimo de lista       {r['con_lista']:>6}")
        print(f"    justo en el minimo      {r['al_precio_de_lista']:>6}")
        print(f"    DEBAJO (hay que subir)  {r['debajo_de_lista']:>6}")
        print(f"    arriba (esta permitido) {r['arriba_de_lista']:>6}")
        print(f"  Sin lista (otro proveedor){r['sin_lista']:>6}")
        print(f"\n  El minimo NO alcanza para el margen objetivo: "
              f"{r['lista_no_alcanza']:>4}")
        print(f"    (ahi el problema es el costo, no el precio: el minimo se")
        print(f"     puede superar, asi que subir sigue siendo una opcion)")

    subir = df[df["diagnostico"] == "hay que subir"]
    if len(subir):
        print(f"\n  Los 12 que mas plata pierden:")
        for _, f in subir.head(12).iterrows():
            print(f"    {f['sku']:<24} {pes(f['precio_actual'])} -> "
                  f"{pes(f['precio_minimo'])} ({f['subir_pct']:+.0%})"
                  f"{'  [cruza escalon]' if f['cruza_escalon'] else ''}")
            print(f"       {f['titulo']}")
            print(f"       margen hoy {pes(f['margen_hoy'])}/u · "
                  f"{int(f['unidades'])} u · perdio "
                  f"{pes(abs(f['perdida_periodo']))}")

    df.to_csv(DIR / "precio_minimo.csv", index=False)
    print(f"\nGuardado en precio_minimo.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
