#!/usr/bin/env python3
"""
Gestion de Product Ads: campanas, anuncios y reglas automaticas.

    python publicidad.py            -> foto de las campanas y de las reglas
    python publicidad.py --detalle  -> ademas escupe publicidad.csv

CRAFTERS tiene **tres anunciantes**, uno por marca, con **una campana cada
uno**. O sea que la capa de campanas es diminuta: lo que mueve la aguja es la
capa de abajo, los ~2.600 anuncios por anunciante.

Medido el 2026-08-04, ultimos 30 dias del anunciante Bulit: $3.202.349 de
gasto, ACOS 25,99% contra un objetivo de 23%, ROAS 3,85 contra 4,35. O sea
que la campana esta corriendo peor que su propio objetivo, y nadie mira
anuncio por anuncio cual lo esta arrastrando.

**Es solo lectura hasta que se llama `aplicar()`.** `analizar()` propone y
explica; `aplicar()` escribe.

La ruta vieja `/advertising/...` esta deprecada y contesta 404, o un 500 con
"Type mismatch" que no dice nada. La que anda es
`/marketplace/advertising/{site}/advertisers/{id}/product_ads/...` y **exige
el header `Api-Version: 2`**.
"""

import json
import sys
from pathlib import Path

import pandas as pd

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError, SITE_ID

DIR = Path(__file__).resolve().parent

CABECERA = {"Api-Version": "2"}
BASE = "/marketplace/advertising/{site}/advertisers/{adv}/product_ads"

# Las metricas hay que pedirlas por nombre: sin el parametro `metrics` el
# campo viene {} y con `metrics_summary` sin `metrics` tira 400.
METRICAS = ("clicks,prints,cost,acos,ctr,cvr,roas,total_amount,"
            "direct_amount,indirect_amount,units_quantity")

# ------------------------------------------------------------------ config

HOJA_CONFIG = "publicidad_config"
COLUMNAS_CONFIG = ["clave", "valor"]
HOJA_ESTRATEGICOS = "publicidad_estrategicos"
COLUMNAS_ESTRATEGICOS = ["sku", "nota"]

# Los topes viven en la Sheet, no en un archivo: en Streamlit Cloud el disco
# es efimero y cualquier cambio se perderia en el proximo deploy.
POR_DEFECTO = {
    "acos_max": 35.0,        # arriba de esto el anuncio no se banca
    "roas_min": 2.5,         # abajo de esto tampoco
    "acos_bueno": 15.0,      # candidato a empujar
    "roas_bueno": 6.0,
    "clicks_minimos": 30,    # menos que esto es ruido, no una senal
    "gasto_minimo": 5000.0,  # idem, en pesos
}


def config():
    """Topes vigentes. Lo que falte en la Sheet cae al valor por defecto."""
    valores = dict(POR_DEFECTO)
    try:
        for fila in almacen.leer_hoja(HOJA_CONFIG, COLUMNAS_CONFIG):
            clave = str(fila.get("clave", "")).strip()
            if clave in valores:
                try:
                    valores[clave] = float(str(fila.get("valor")).replace(",", "."))
                except (TypeError, ValueError):
                    pass
    except Exception:
        # Sin Sheet configurada se trabaja con los defaults. No es motivo
        # para tumbar la pantalla.
        pass
    return valores


def guardar_config(valores):
    filas = [{"clave": k, "valor": v} for k, v in valores.items()]
    return almacen.reescribir_hoja(HOJA_CONFIG, COLUMNAS_CONFIG, filas)


def estrategicos():
    """
    SKU que las reglas **no** tocan nunca.

    Son los que se publicitan por decision comercial y no por rentabilidad:
    lanzamientos, productos que traen trafico, lo que se quiere defender de un
    competidor. Sin esta lista, la primera corrida de reglas los apaga a todos
    y nadie se entera hasta que caen las visitas.
    """
    try:
        filas = almacen.leer_hoja(HOJA_ESTRATEGICOS, COLUMNAS_ESTRATEGICOS)
    except Exception:
        return {}
    return {str(f.get("sku", "")).strip().upper(): str(f.get("nota", ""))
            for f in filas if str(f.get("sku", "")).strip()}


def guardar_estrategicos(filas):
    return almacen.reescribir_hoja(HOJA_ESTRATEGICOS, COLUMNAS_ESTRATEGICOS,
                                   filas)


# ------------------------------------------------------------------ lectura

def anunciantes(ml):
    """Los anunciantes de la cuenta. En CRAFTERS son tres, uno por marca."""
    r = ml.get("/advertising/advertisers", product_id="PADS")
    return [a for a in (r.get("advertisers") or [])
            if a.get("site_id") == SITE_ID]


def campanas(ml, advertiser_id):
    base = BASE.format(site=SITE_ID, adv=advertiser_id)
    r = ml.get(f"{base}/campaigns/search", _headers=CABECERA,
               limit=50, offset=0)
    return r.get("results") or []


def anuncios(ml, advertiser_id, desde, hasta, callback=None, tope=None):
    """
    Todos los anuncios del anunciante con sus metricas del periodo.

    **Deduplica por item_id**: la API repite el mismo anuncio en mas de una
    fila (misma publicacion en varios ad_group), y contarlo dos veces inflaria
    el gasto y haria que una regla lo evalue dos veces con el mismo resultado.
    """
    base = BASE.format(site=SITE_ID, adv=advertiser_id)
    vistos, salida, offset = set(), [], 0

    while True:
        r = ml.get(f"{base}/ads/search", _headers=CABECERA,
                   limit=50, offset=offset,
                   date_from=desde, date_to=hasta, metrics=METRICAS)
        filas = r.get("results") or []
        if not filas:
            break

        for a in filas:
            item = a.get("item_id")
            if not item or item in vistos:
                continue
            vistos.add(item)
            m = a.get("metrics") or {}
            salida.append({
                "item_id": item,
                "advertiser_id": advertiser_id,
                "campaign_id": a.get("campaign_id"),
                "titulo": (a.get("title") or "")[:60],
                "marca": a.get("brand_value_name") or "",
                "estado_ad": a.get("status"),
                "precio": a.get("price"),
                "catalogo": bool(a.get("catalog_listing")),
                "gana_buybox": bool(a.get("buy_box_winner")),
                "clicks": m.get("clicks") or 0,
                "impresiones": m.get("prints") or 0,
                "gasto": float(m.get("cost") or 0),
                "facturado": float(m.get("total_amount") or 0),
                "unidades": m.get("units_quantity") or 0,
                "acos": float(m.get("acos") or 0),
                "roas": float(m.get("roas") or 0),
                "ctr": float(m.get("ctr") or 0),
                "cvr": float(m.get("cvr") or 0),
            })
            if tope and len(salida) >= tope:
                return salida

        if callback:
            callback(f"Anunciante {advertiser_id}: {len(salida)} anuncios...")

        total = (r.get("paging") or {}).get("total", 0)
        offset += 50
        if offset >= total:
            break

    return salida


def traer_todo(ml, desde, hasta, callback=None, tope=None):
    """Anuncios de los tres anunciantes, con el nombre de cada campana."""
    advs = anunciantes(ml)
    nombres, filas = {}, []
    for a in advs:
        aid = a["advertiser_id"]
        nombres[aid] = a.get("advertiser_name") or str(aid)
        for c in campanas(ml, aid):
            nombres[(aid, c["id"])] = c

        if callback:
            callback(f"Leyendo {nombres[aid]}...")
        filas.extend(anuncios(ml, aid, desde, hasta, callback=callback,
                              tope=tope))

    df = pd.DataFrame(filas)
    if len(df):
        df["anunciante"] = df["advertiser_id"].map(
            lambda i: nombres.get(i, str(i)))
    return df, advs, nombres


# ------------------------------------------------------------------ reglas

# El orden importa: la primera regla que matchea es la que manda. Van de la
# mas dura (no se puede vender) a la mas discutible (rinde poco).
SIN_DATOS = "pocos datos todavía"


def _vendible(pub):
    """Si la publicacion se puede comprar hoy."""
    if pub is None:
        return False, "no está en el catálogo"
    if pub.get("status") != "active":
        return False, f"la publicación está {pub.get('status')}"
    if not (pub.get("available_quantity") or 0):
        return False, "sin stock"
    return True, ""


def analizar(df_ads, pubs, cfg=None, estrat=None, df_rent=None):
    """
    Marca que hacer con cada anuncio. Devuelve el mismo DataFrame con
    `accion` ('pausar' / 'activar' / 'revisar' / 'ninguna') y `motivo`.

    `df_rent` es la salida de rentabilidad, opcional: si viene, se usa para
    apagar lo que pierde plata de caja.
    """
    cfg = cfg or config()
    estrat = estrat if estrat is not None else estrategicos()
    df = df_ads.copy()
    if not len(df):
        return df

    por_id = {p["id"]: p for p in pubs}
    sku_de = {p["id"]: (sku_del_atributo(p) or
                        p.get("seller_custom_field") or "").strip().upper()
              for p in pubs}
    df["sku"] = df["item_id"].map(lambda i: sku_de.get(i, ""))

    pierde_plata = set()
    if df_rent is not None and len(df_rent) and "sku" in df_rent:
        col = ("gana_por_unidad" if "gana_por_unidad" in df_rent
               else "margen_unitario" if "margen_unitario" in df_rent else None)
        if col:
            pierde_plata = set(
                df_rent[df_rent[col] < 0]["sku"].astype(str).str.upper())

    acciones, motivos = [], []
    for _, a in df.iterrows():
        sku = a["sku"]
        activo = a["estado_ad"] == "active"

        # 1. Estrategico: no se toca, gane o pierda.
        if sku and sku in estrat:
            acciones.append("ninguna")
            motivos.append(f"SKU estratégico — {estrat[sku] or 'no se toca'}")
            continue

        # 2. `hold` es un anuncio que deshabilito ML. No gasta, no se puede
        #    encender y no se puede mover de campana. Proponer algo sobre el
        #    es prometer una accion que la API va a rechazar.
        if a["estado_ad"] == "hold":
            acciones.append("ninguna")
            motivos.append("deshabilitado por MercadoLibre")
            continue

        # 3. Lo que no se puede comprar no se publicita. Es la unica regla
        #    que no admite discusion: son clics pagos a una pagina sin stock.
        ok, porque = _vendible(por_id.get(a["item_id"]))
        if not ok:
            acciones.append("pausar" if activo else "ninguna")
            motivos.append(porque if activo else f"{porque} (ya pausado)")
            continue

        # 3. Sin datos suficientes no se juzga. Apagar por un ACOS calculado
        #    sobre 4 clics es apagar por ruido.
        flaco = (a["clicks"] < cfg["clicks_minimos"]
                 and a["gasto"] < cfg["gasto_minimo"])

        if activo and not flaco:
            if a["unidades"] == 0 and a["gasto"] >= cfg["gasto_minimo"]:
                acciones.append("pausar")
                motivos.append(f"gastó ${a['gasto']:,.0f} y no vendió nada"
                               .replace(",", "."))
                continue
            if a["acos"] > cfg["acos_max"]:
                acciones.append("pausar")
                motivos.append(f"ACOS {a['acos']:.0f}% supera el tope de "
                               f"{cfg['acos_max']:.0f}%")
                continue
            if 0 < a["roas"] < cfg["roas_min"]:
                acciones.append("pausar")
                motivos.append(f"ROAS {a['roas']:.1f} por debajo de "
                               f"{cfg['roas_min']:.1f}")
                continue
            if sku and sku in pierde_plata:
                acciones.append("pausar")
                motivos.append("el SKU pierde plata de caja")
                continue

        # 4. Apagado pero rinde: candidato a volver a encender.
        if not activo and not flaco:
            if a["acos"] and a["acos"] < cfg["acos_bueno"] and a["unidades"]:
                acciones.append("activar")
                motivos.append(f"ACOS {a['acos']:.0f}%, mejor que "
                               f"{cfg['acos_bueno']:.0f}% — está apagado")
                continue
            if a["roas"] >= cfg["roas_bueno"] and a["unidades"]:
                acciones.append("activar")
                motivos.append(f"ROAS {a['roas']:.1f} — está apagado")
                continue

        # 5. La marca del anuncio no coincide con su anunciante. No se
        #    corrige solo: mover un anuncio de campana cambia el presupuesto
        #    de las dos, y eso lo decide una persona.
        if a["marca"] and a["anunciante"] and \
                a["marca"].strip().lower() not in a["anunciante"].strip().lower():
            acciones.append("revisar")
            motivos.append(f"marca {a['marca']} en la campaña "
                           f"{a['anunciante']}")
            continue

        acciones.append("ninguna")
        motivos.append(SIN_DATOS if flaco else "dentro de los topes")

    df["accion"] = acciones
    df["motivo"] = motivos
    return df.sort_values("gasto", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------- escritura

# El alta/baja/cambio de un anuncio NO va por la ruta del anunciante: es un
# PUT a /marketplace/advertising/{site}/product_ads/ad con el item en el
# cuerpo. Las rutas con /advertisers/{id}/... adentro contestan 404.
RUTA_AD = f"/marketplace/advertising/{SITE_ID}/product_ads/ad"


def cambiar_estado(ml, item_id, estado, campaign_id=None):
    """
    Prende o apaga un anuncio. `estado` es 'active' o 'paused'.

    Devuelve (ok, detalle). No lanza: en un lote de cientos, una falla no
    puede llevarse la corrida.
    """
    cuerpo = {"item_id": item_id, "status": estado}
    if campaign_id:
        cuerpo["campaign_id"] = int(campaign_id)
    try:
        r = ml.put(RUTA_AD, cuerpo, _headers=CABECERA)
        return True, (r or {}).get("status", estado)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:180]}"


def sacar_de_campana(ml, item_id):
    """
    Saca el anuncio de su campana. Queda en `idle`: sigue disponible para
    publicitar pero no gasta.

    **No se puede mandar `status` en la misma llamada** — al salir de la
    campana el anuncio queda en idle solo, y mandar los dos campos falla.
    """
    try:
        r = ml.put(RUTA_AD, {"item_id": item_id, "campaign_id": 0},
                   _headers=CABECERA)
        return True, (r or {}).get("status", "idle")
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:180]}"


def aplicar(ml, plan, operador="", callback=None, acciones=("pausar",)):
    """
    Ejecuta el plan. Por defecto **solo pausa**: encender un anuncio gasta
    plata y es una decision distinta de dejar de gastarla, asi que 'activar'
    hay que pedirlo explicitamente.

    Cada anuncio va en su propio try. Todo queda en la auditoria.

    **OJO — hoy esto no funciona y la pantalla no lo ofrece.** Al 2026-08-04
    no hay endpoint de escritura accesible: la ruta documentada
    (`PUT /marketplace/advertising/{site}/product_ads/ad`) contesta 404 en
    todas sus variantes probadas —con y sin advertiser en el path, PUT y
    POST, Api-Version 1, 2 y sin header— y la unica que existe,
    `/marketplace/advertising/{site}/product_ads/items/{item_id}`, devuelve
    **503 de forma constante** (nueve intentos, tres cuerpos distintos). La
    lectura y el analisis andan perfecto; queda pendiente resolver esto con
    el soporte de ML o encontrar la ruta vigente.
    """
    if plan is None or not len(plan):
        return pd.DataFrame()

    pendientes = plan[plan["accion"].isin(acciones)]
    nota = f"publicidad {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    salida, total = [], len(pendientes)

    for i, (_, a) in enumerate(pendientes.iterrows(), start=1):
        if callback:
            callback(i, total, a)

        nuevo = "paused" if a["accion"] == "pausar" else "active"
        fila = {"item_id": a["item_id"], "sku": a.get("sku", ""),
                "titulo": a.get("titulo", ""),
                "anunciante": a.get("anunciante", ""),
                "estado_antes": a["estado_ad"], "estado_nuevo": nuevo,
                "gasto": a.get("gasto", 0), "acos": a.get("acos", 0),
                "motivo": a.get("motivo", "")}

        ok, detalle = cambiar_estado(ml, a["item_id"], nuevo,
                                     a.get("campaign_id"))
        # El anuncio no es una publicacion, pero la auditoria es el unico
        # lugar donde queda como estaba antes.
        from meli import registrar_auditoria
        registrar_auditoria(a["item_id"], {"ad_status": nuevo},
                            {"ad_status": a["estado_ad"]},
                            {"ad_status": detalle if ok else ""},
                            "OK" if ok else f"ERROR: {detalle}",
                            operador, f"{nota} - {a.get('motivo','')}"[:200])

        salida.append({**fila, "resultado": "OK" if ok else "ERROR",
                       "detalle": "" if ok else str(detalle)[:200]})

    return pd.DataFrame(salida)


# ------------------------------------------------------------------ campanas

def cambiar_campana(ml, advertiser_id, campaign_id, cambios):
    """
    Modifica una campana: `status` ('active'/'paused'), `budget`,
    `acos_target`. Son tres campanas en total, asi que esto se usa poco y a
    mano — pero el presupuesto es lo unico que topea el gasto de todo lo
    demas, y tenerlo aca evita entrar al panel.
    """
    base = BASE.format(site=SITE_ID, adv=advertiser_id)
    try:
        r = ml.put(f"{base}/campaigns/{campaign_id}", cambios,
                   _headers=CABECERA)
        return True, r
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def main():
    from datetime import date, timedelta
    ml = Meli(verbose=False)
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=29)

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    print(f"Publicidad del {desde} al {hasta}\n")

    for a in anunciantes(ml):
        print(f"  {a['advertiser_name']} (id {a['advertiser_id']})")
        for c in campanas(ml, a["advertiser_id"]):
            print(f"     campaña «{c['name']}» — {c['status']} · "
                  f"presupuesto {pes(c.get('budget') or 0)} · "
                  f"ACOS objetivo {c.get('acos_target')}%")

    df, _, _ = traer_todo(ml, desde.isoformat(), hasta.isoformat(),
                          callback=lambda m: print(f"   {m}"))
    if not len(df):
        print("\nNo hay anuncios.")
        return 0

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    plan = analizar(df, pubs)

    print(f"\n{len(plan)} anuncios · gasto {pes(plan['gasto'].sum())} · "
          f"facturado {pes(plan['facturado'].sum())}")
    print("\nQué haría:")
    for acc, g in plan.groupby("accion"):
        print(f"  {acc:<10} {len(g):>5}   (gasto {pes(g['gasto'].sum())})")

    print("\nLos 10 de mayor gasto a pausar:")
    for _, a in plan[plan["accion"] == "pausar"].head(10).iterrows():
        print(f"  {a['sku'] or a['item_id']:<22} {pes(a['gasto']):>12}  "
              f"ACOS {a['acos']:>5.0f}%  {a['motivo']}")

    if "--detalle" in sys.argv:
        plan.to_csv(DIR / "publicidad.csv", index=False)
        print(f"\nGuardado en publicidad.csv ({len(plan)} filas)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
