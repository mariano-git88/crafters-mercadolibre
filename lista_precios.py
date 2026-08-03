#!/usr/bin/env python3
"""
La lista de precios del proveedor: que cuesta y a que precio hay que publicar.

    python lista_precios.py "_assets/Lista_Marketing_30-07 (1).xlsx"
    python lista_precios.py archivo.xlsx --guardar   -> la deja en la Sheet

Hasta ahora el sistema tenia el costo por SKU y nada mas: cada seccion
despejaba sola a que precio convenia estar. Esta lista agrega la otra mitad,
que es una decision comercial y no una cuenta: **el precio al que Suprabond
quiere que se publique online**.

Tres columnas importan:

  - `ListaPrecio` .............. lo que CRAFTERS paga por el producto.
  - `PRECIO_SUGERIDO_ONLINE` ... el precio al que hay que publicar. Es
                                 `ListaPrecio x 2,12` para los 728 productos,
                                 sin excepcion.
  - `CodigoBarra` .............. el EAN, que sirve para cruzar.

`PrecioSugerido` (x 1,609) es el del canal comercio y aca no se usa.

**El problema de fondo era cruzar los codigos.** La lista viene con los codigos
de Suprabond (`SBD TR PR 100 E`) y MercadoLibre tiene los de CRAFTERS
(`CR0160000SBDTRPR100E`). No comparten ni un caracter a simple vista, y cruzar
por `Producto_id` da **cero** coincidencias.

Mirando los que si cruzaban por EAN aparecio la regla, y es exacta:

    CR + grupo(3 digitos) + ceros de relleno + codigo sin espacios,
    todo padeado a 20 caracteres

    SBD TR PR 100 E  ->  CR + 016 + 0000 + SBDTRPR100E   = CR0160000SBDTRPR100E
    KIT R OP         ->  CR + 016 + 000000000 + KITROP   = CR016000000000KITROP

Con esa regla cruzan 666 de 728. El EAN levanta 4 mas (codigos que se
renombraron), y los 58 restantes **no estan publicados en MercadoLibre**: son
combos, exhibidores y sets del canal comercio. No es un error del cruce.

Sobre el IVA: el sugerido se toma **con IVA**, o sea comparable directo contra
el precio de MercadoLibre. Es lo que dicen los datos —mas de la mitad del
catalogo esta publicado a exactamente 0,9852 veces el sugerido, y eso solo
cierra si estan en la misma base. Si algun dia cambia, esta `SUGERIDO_CON_IVA`.
"""

import re
import sys
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parent

HOJA = "lista_precios"
COLUMNAS = ["sku", "producto_id", "costo", "sugerido", "ean",
            "descripcion", "via", "fecha"]

# El sugerido viene con IVA: es precio final al publico, comparable contra el
# precio de MercadoLibre sin tocarlo.
SUGERIDO_CON_IVA = True

# Cuanto se puede descontar sobre el precio de publicacion en una promocion
# puntual. Suprabond deja 10-15%; se toma el techo y despues cada pantalla
# informa cuanto haria falta de verdad en cada caso.
DESCUENTO_PERMITIDO = 0.15

# Largo fijo del codigo de CRAFTERS. Los 1.313 SKU "simples" del catalogo lo
# respetan; los mas cortos (402) son de otra familia y no salen de esta lista.
LARGO_SKU = 20

COL_COSTO = "ListaPrecio"
COL_SUGERIDO = "PRECIO_SUGERIDO_ONLINE"
COL_PRODUCTO = "Producto_id"
COL_EAN = "CodigoBarra"
COL_DESC = "Descripcion"


# ------------------------------------------------------------------ cruce

def compactar(codigo):
    """El codigo de Suprabond sin espacios ni puntuacion: 'SBD TR 25 B' -> 'SBDTR25B'."""
    return re.sub(r"[^A-Z0-9]", "", str(codigo).upper())


def cola_de(sku, sin_sufijos=False):
    """
    Lo que queda de un SKU de CRAFTERS al sacarle el prefijo y los ceros.

    `sin_sufijos` ademas saca los de pack (' X 3 UNIDADES') y los combos
    (' + otro SKU'). **Ojo con usarlo para cruzar**: el pack de 3 y la unidad
    suelta son productos distintos con precios distintos, asi que colapsarlos
    convierte 55 cruces buenos en ambiguedades. Sirve solo como ultimo
    recurso, cuando no hubo match exacto.
    """
    base = str(sku).upper().strip()
    if sin_sufijos:
        base = base.split(" + ")[0].split(" X ")[0].strip()
    m = re.match(r"^CR(\d{3})0*(.+)$", base)
    return m.group(2) if m else None


def _sku_de_publicacion(pub):
    for a in (pub.get("attributes") or []):
        if (a.get("id") or "") == "SELLER_SKU" and a.get("value_name"):
            return str(a["value_name"]).strip().upper()
    return (pub.get("seller_custom_field") or "").strip().upper()


def _gtin_de_publicacion(pub):
    for a in (pub.get("attributes") or []):
        if (a.get("id") or "") == "GTIN" and a.get("value_name"):
            return str(a["value_name"]).strip()
    return ""


def indices_del_catalogo(pubs):
    """
    Tres indices para cruzar: cola exacta, cola sin sufijos y EAN.

    La exacta es la que manda. La otra existe para los pocos codigos donde el
    SKU trae el pack pegado y no hay una publicacion suelta que le compita.
    Se arman una sola vez y se reusan para las 728 filas.
    """
    exacta, base, por_ean = {}, {}, {}
    for p in pubs:
        sku = _sku_de_publicacion(p)
        if not sku:
            continue
        c = cola_de(sku)
        if c:
            exacta.setdefault(c, set()).add(sku)
        cb = cola_de(sku, sin_sufijos=True)
        if cb:
            base.setdefault(cb, set()).add(sku)
        ean = _gtin_de_publicacion(p)
        if ean:
            por_ean.setdefault(ean, set()).add(sku)
    return exacta, base, por_ean


def resolver_sku(producto_id, ean, exacta, base, por_ean):
    """
    (sku, via). `sku` es None si el producto no esta publicado en ML.

    El orden importa y es este:

      1. **codigo exacto** — la regla del padeado a 20, sin tocar sufijos.
         Es la que resuelve la enorme mayoria y no se equivoca.
      2. **EAN** — para los codigos que se renombraron de un lado y no del otro.
      3. **codigo sin sufijos** — ultimo recurso, y solo si deja un unico
         candidato. Aca es donde el pack de 3 podria hacerse pasar por la
         unidad suelta, asi que si hay mas de uno se declara ambiguo y no se
         adivina: un precio mal asignado es peor que un SKU sin precio.
    """
    compacto = compactar(producto_id)
    ean = str(ean or "").strip()

    cands = exacta.get(compacto, set())
    if len(cands) == 1:
        return next(iter(cands)), "patron"

    por_e = por_ean.get(ean, set()) if ean else set()
    if cands and por_e:
        inter = cands & por_e
        if len(inter) == 1:
            return next(iter(inter)), "patron+ean"
    if len(por_e) == 1:
        return next(iter(por_e)), "ean"

    if not cands and not por_e:
        cands_b = base.get(compacto, set())
        if len(cands_b) == 1:
            return next(iter(cands_b)), "patron sin sufijo"
        if len(cands_b) > 1:
            return None, "ambiguo"
        return None, "no publicado"

    return None, "ambiguo"


# ------------------------------------------------------------------ lectura

def leer(archivo, pubs):
    """
    Lee la lista y le pega el SKU de CRAFTERS a cada fila.

    Devuelve el DataFrame entero, incluidas las filas sin resolver: saber que
    58 productos de la lista no estan publicados es informacion util, no
    basura para tirar.
    """
    df = pd.read_excel(archivo)

    faltan = [c for c in (COL_COSTO, COL_SUGERIDO, COL_PRODUCTO)
              if c not in df.columns]
    if faltan:
        raise ValueError(
            f"A la lista le faltan columnas: {faltan}. "
            f"Tiene: {list(df.columns)}")

    exacta, base, por_ean = indices_del_catalogo(pubs)

    filas = []
    for _, f in df.iterrows():
        ean = str(f.get(COL_EAN) or "").strip()
        sku, via = resolver_sku(f[COL_PRODUCTO], ean, exacta, base, por_ean)
        costo = pd.to_numeric(f[COL_COSTO], errors="coerce")
        sugerido = pd.to_numeric(f[COL_SUGERIDO], errors="coerce")
        filas.append({
            "sku": sku or "",
            "producto_id": str(f[COL_PRODUCTO]).strip(),
            "costo": float(costo) if pd.notna(costo) else None,
            "sugerido": float(sugerido) if pd.notna(sugerido) else None,
            "ean": ean,
            "descripcion": str(f.get(COL_DESC) or "").strip()[:120],
            "via": via,
        })
    return _desempatar(pd.DataFrame(filas))


# Cuanto se le cree a cada via cuando dos filas se pelean el mismo SKU.
CONFIANZA = {"patron": 3, "patron+ean": 3, "ean": 2, "patron sin sufijo": 1}


def _desempatar(df):
    """
    Deja un solo precio por SKU. Los empates quedan SIN precio, no con uno.

    Pasa en tres casos reales de esta lista:

      - `PIN S4 2` (pincel 2") y `PIN S4 3` (pincel 3") caen en el mismo SKU
        porque el EAN de uno apunta a la publicacion del otro. El codigo
        acierta y el EAN arrastra mal, asi que **gana el codigo**.
      - `SGC CBO 6` y `SGC CBO 6 E` (formula extra), lo mismo.
      - `LLV 7VA 1/2` y `LLV 7VA 12` son 1/2 pulgada y 12mm: **dos productos
        distintos que compactan al mismo codigo**, porque la barra se cae al
        sacar la puntuacion. Ninguna via puede desempatarlos y se llevan 8% de
        diferencia de precio, asi que los dos quedan sin asignar. Ponerle a la
        llave de 12mm el precio de la de 1/2" seria peor que no tener precio:
        el error viajaria callado hasta el precio publicado.
    """
    if not len(df):
        return df

    df = df.copy()
    df["_conf"] = df["via"].map(CONFIANZA).fillna(0)

    for sku, grupo in df[df["sku"].ne("")].groupby("sku"):
        if len(grupo) < 2:
            continue
        mejor = grupo["_conf"].max()
        ganadores = grupo[grupo["_conf"] == mejor]
        if len(ganadores) == 1:
            perdedores = grupo.index.difference(ganadores.index)
            df.loc[perdedores, ["sku", "via"]] = ["", "duplicado"]
        else:
            # Empate: nadie se lo queda.
            df.loc[grupo.index, ["sku", "via"]] = ["", "ambiguo"]

    return df.drop(columns=["_conf"])


def resumen_cruce(df):
    """Cuanto de la lista quedo utilizable, para mostrarlo al cargar."""
    ok = df[df["sku"].ne("")]
    return {
        "filas": len(df),
        "resueltos": len(ok),
        "por_patron": int(df["via"].isin(
            ["patron", "patron sin sufijo"]).sum()),
        "por_ean": int(df["via"].isin(["ean", "patron+ean"]).sum()),
        "no_publicados": int((df["via"] == "no publicado").sum()),
        "ambiguos": int((df["via"] == "ambiguo").sum()),
        "duplicados": int((df["via"] == "duplicado").sum()),
        "sin_precio": int(ok["sugerido"].isna().sum()),
    }


# ------------------------------------------------------------------ guardado

def guardar(df):
    """
    Deja la lista en la Google Sheet. Se reemplaza entera: la ultima que sube
    el operador es la verdad, igual que con los costos.
    """
    import almacen

    sello = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    filas = [{"sku": f["sku"], "producto_id": f["producto_id"],
              "costo": f["costo"], "sugerido": f["sugerido"],
              "ean": f["ean"], "descripcion": f["descripcion"],
              "via": f["via"], "fecha": sello}
             for _, f in df.iterrows() if f["sku"]]
    ok, detalle = almacen.reescribir_hoja(HOJA, COLUMNAS, filas)
    return ok, (detalle or sello), len(filas)


def guardada():
    """
    (DataFrame, cuando). Vacio si todavia no se cargo ninguna lista.

    Nunca lanza: si la hoja no existe, las secciones siguen andando con el
    criterio de siempre y avisan que falta la lista.
    """
    import almacen

    try:
        filas = almacen.leer_hoja(HOJA, COLUMNAS)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=COLUMNAS), ""
    if not filas:
        return pd.DataFrame(columns=COLUMNAS), ""

    from actualizador import _a_numero
    df = pd.DataFrame(filas)
    df["sku"] = df["sku"].astype(str).str.strip().str.upper()
    for c in ("costo", "sugerido"):
        df[c] = df[c].map(_a_numero)
    cuando = str(df["fecha"].iloc[0]) if "fecha" in df else ""
    return df[df["sku"].ne("")], cuando


# --------------------------------------------------------------- consultas

def mapa_precios(df=None):
    """
    dict sku -> {'costo': x, 'sugerido': y}. Es lo que consumen las secciones.

    Si no se pasa `df` se lee de la Sheet. Devolver un dict y no un DataFrame
    es a proposito: las secciones lo consultan SKU por SKU dentro de un loop.
    """
    if df is None:
        df, _ = guardada()
    salida = {}
    for _, f in df.iterrows():
        sku = str(f["sku"]).strip().upper()
        if not sku:
            continue
        salida[sku] = {"costo": f.get("costo"), "sugerido": f.get("sugerido")}
    return salida


def _numero(x):
    """
    float valido, o None. Filtra los NaN ademas de los None.

    Hace falta de verdad: estos valores viajan dentro de columnas de pandas,
    y ahi un None se vuelve NaN. `NaN` es truthy y `NaN <= 0` es False, asi
    que sin este filtro las guardas de abajo lo dejan pasar y el resultado
    sale NaN — que despues se imprime como "haría falta nan% de descuento".
    """
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v      # v != v es True solo para NaN


def mapa_por_ean(df=None):
    """
    dict ean -> {'costo': x, 'sugerido': y}, para Competencia.

    Esa seccion trabaja por codigo de barras y no por SKU, asi que cruzar por
    EAN le ahorra el rodeo. Se incluyen tambien las filas que no se pudieron
    cruzar contra MercadoLibre: el precio de lista existe igual, y para
    comparar contra la competencia no hace falta tener publicacion propia.
    """
    if df is None:
        df, _ = guardada()
    salida = {}
    for _, f in df.iterrows():
        ean = str(f.get("ean") or "").strip()
        if not ean or ean.lower() == "nan":
            continue
        salida[ean] = {"costo": f.get("costo"), "sugerido": f.get("sugerido")}
    return salida


def precio_con_descuento(sugerido, descuento=DESCUENTO_PERMITIDO):
    """El piso al que se puede llegar usando el descuento permitido."""
    s = _numero(sugerido)
    if not s or s <= 0:
        return None
    return s * (1 - descuento)


def descuento_necesario(sugerido, precio_objetivo):
    """
    Que descuento sobre el sugerido hace falta para llegar a `precio_objetivo`.

    Devuelve None si no hace falta ninguno (el objetivo ya esta por encima) o
    si falta alguno de los dos datos. El numero puede pasarse de
    `DESCUENTO_PERMITIDO`: es justamente el caso que hay que mostrar como "no
    alcanza ni con el descuento maximo".
    """
    s, p = _numero(sugerido), _numero(precio_objetivo)
    if not s or not p or s <= 0:
        return None
    if p >= s:
        return None
    return (s - p) / s


def alcanza_con_descuento(sugerido, precio_objetivo,
                          permitido=DESCUENTO_PERMITIDO):
    """
    (alcanza, descuento_necesario). Para el mensaje "si usas el descuento
    permitido podes ganar el Buy Box".

    Sin precio de lista devuelve (None, None): no es que alcance, es que no se
    puede saber. Decir True ahi seria dar por buena una promocion que nadie
    calculo.

    Los tres resultados posibles, y conviene distinguirlos:

        (None, None)  -> no hay precio de lista, no se puede opinar
        (True, None)  -> alcanza SIN descuento, ya estas bien publicando
        (True, 0.11)  -> alcanza descontando 11%
        (False, 0.31) -> haria falta 31%, mas de lo permitido

    El segundo caso devolvia 0.0 en vez de None y eso hacia que las pantallas
    dijeran "con 0% de descuento ganás", que suena a que hay que hacer algo
    cuando en realidad no hay nada que hacer.
    """
    if _numero(sugerido) is None:
        return None, None
    d = descuento_necesario(sugerido, precio_objetivo)
    if d is None:
        return True, None
    return d <= permitido + 1e-9, d


# ------------------------------------------------------------------ cli

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip().split("\n\n")[1])
        return 1

    import json
    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    df = leer(args[0], pubs)
    r = resumen_cruce(df)

    print("=" * 68)
    print("LISTA DE PRECIOS")
    print("=" * 68)
    print(f"  Filas en la lista           {r['filas']:>6}")
    print(f"  Cruzadas con MercadoLibre   {r['resueltos']:>6}")
    print(f"    por codigo                {r['por_patron']:>6}")
    print(f"    por codigo de barras      {r['por_ean']:>6}")
    print(f"  No publicadas en ML         {r['no_publicados']:>6}")
    print(f"  Ambiguas (sin precio)       {r['ambiguos']:>6}")
    print(f"  Duplicadas (gano el codigo) {r['duplicados']:>6}")

    ok = df[df["sku"].ne("")]
    if len(ok):
        mult = (ok["sugerido"] / ok["costo"]).median()
        print(f"\n  Sugerido / costo (mediana)  {mult:>6.3f}")
        print(f"\n  Ejemplos:")
        for _, f in ok.head(5).iterrows():
            print(f"    {f['producto_id']:<18} -> {f['sku']:<22} "
                  f"costo ${f['costo']:>10,.0f}  publicar ${f['sugerido']:>10,.0f}")

    if "--guardar" in sys.argv:
        ok_g, cuando, n = guardar(df)
        print(f"\n  {'Guardada' if ok_g else 'ERROR'}: {n} SKU ({cuando})")
    else:
        print(f"\n  (no se guardo; agregar --guardar)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
