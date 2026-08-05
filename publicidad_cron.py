#!/usr/bin/env python3
"""
Apaga solo los anuncios que no se bancan. Pensado para correr por GitHub
Actions.

    python publicidad_cron.py            -> dice que haria, no toca nada
    python publicidad_cron.py --aplicar  -> apaga

Hace dos cosas, las dos automaticas:

  - **apaga** lo que pasa el tope de ACOS o gasta sin vender;
  - **suma** a una campana las publicaciones que el analisis de Visitas vs
    ventas marca como `escalar` o `falta_exposicion`: ya convierten y les
    falta gente que las vea.

Las que le tocarian una campana **pausada** van a una campana propia
(`campana_nuevos` en la config). Sumarlas a una pausada no sirve —entran
activas pero la campana no corre— y prender la general de Crafters
encenderia sus 4.557 anuncios de una.

**Agregar prende y gasta desde el momento.** Lo aprendimos por las malas:
durante las pruebas, capturar esa accion reactivo un anuncio de $182.000 al
mes sin que nadie lo pidiera. Por eso hay tope por corrida y todo queda en la
auditoria.

**Escribe por el panel, no por la API.** MercadoLibre no habilito la
escritura de Product Ads para la aplicacion (ver `publicidad.py`), asi que
esto usa el endpoint interno con la cookie `ssid` de los secrets. En Actions
hay que tenerla en `CRAFTERS_SECRETS_TOML`, bajo `[ads]`.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import almacen
import panel_ads
import publicidad
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Cuantos anuncios como maximo apaga una corrida. No es por rendimiento: es
# para que un error de datos —un catalogo viejo, una metrica rara— no pueda
# apagar la cuenta entera de una. Si la lista da mas, apaga los de mayor
# gasto y el resto queda para la corrida siguiente, con el aviso en el log.
TOPE_POR_CORRIDA = 25

# Debajo de este gasto en el periodo no vale la pena tocar nada: son
# centavos y el anuncio puede estar recien arrancando.
GASTO_MINIMO = 3000.0

DIAS = 30


def correr(aplicar=False, verbose=True):
    ml = Meli(verbose=False)
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=DIAS - 1)

    def log(m):
        if verbose:
            print(m, flush=True)

    log(f"Publicidad del {desde} al {hasta}")
    df, advs, camps = publicidad.traer_todo(
        ml, desde.isoformat(), hasta.isoformat(),
        callback=lambda m: log(f"  {m}"))
    if not len(df):
        log("No hay anuncios.")
        return 0

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8")) \
        if (DIR / "catalogo.json").exists() else publicidad_catalogo(ml)
    plan = publicidad.analizar(df, pubs)

    pes = lambda v: f"${v:,.0f}".replace(",", ".")
    log(f"\n{len(plan)} anuncios · gasto {pes(plan['gasto'].sum())} · "
        f"facturado {pes(plan['facturado'].sum())}")

    apagar = plan[(plan["accion"] == "pausar")
                  & (plan["gasto"] >= GASTO_MINIMO)
                  & (plan["ad_group_id"].notna())].copy()
    apagar = apagar.sort_values("gasto", ascending=False)

    if not len(apagar):
        log("Nada para apagar: todo dentro de los topes.")
        return 0

    if len(apagar) > TOPE_POR_CORRIDA:
        log(f"\n*** {len(apagar)} superan el tope de {TOPE_POR_CORRIDA} por "
            f"corrida. Se apagan los {TOPE_POR_CORRIDA} de mayor gasto; el "
            "resto queda para la próxima. ***")
        apagar = apagar.head(TOPE_POR_CORRIDA)

    log(f"\nA apagar: {len(apagar)} · gasto {pes(apagar['gasto'].sum())}")
    for _, r in apagar.iterrows():
        log(f"  {r['item_id']:<15} {pes(r['gasto']):>12}  "
            f"ACOS {r['acos']:>3.0f}%  {r['motivo'][:52]}")

    # ------------------------------------------------ que sumar
    sumar = candidatos_a_sumar(ml, df, pubs, log)
    if len(sumar) > TOPE_POR_CORRIDA:
        log(f"\n*** {len(sumar)} candidatos superan el tope de "
            f"{TOPE_POR_CORRIDA}. Entran los {TOPE_POR_CORRIDA} de más "
            "visitas; el resto queda para la próxima. ***")
        sumar = sumar.head(TOPE_POR_CORRIDA)
    if len(sumar):
        log(f"\nA sumar: {len(sumar)} publicaciones que convierten y no se "
            "publicitan")
        for _, r in sumar.head(10).iterrows():
            log(f"  {r['item_id']:<15} campaña {int(r['campaign_id']):<11} "
                f"{r['motivo'][:55]}")

    if not aplicar:
        log("\n(simulación: corré con --aplicar para ejecutarlo)")
        return 0

    if not panel_ads.hay_sesion():
        log("\nERROR: no hay sesión del panel cargada. Falta [ads] ssid en "
            "los secrets — sin eso no se puede escribir.")
        return 1

    sesion = panel_ads.leer_sesion()
    auditoria = []

    res = panel_ads.aplicar(sesion, ml, apagar, accion="pausar",
                            callback=lambda i, t, d: log(f"  {i}/{t} {d}"))
    ok = int((res["resultado"] == "OK").sum())
    log(f"\nApagados {ok} de {len(res)}.")
    auditoria += _auditar(res, "active", "paused")

    # Los que estan fuera de campana se agregan; los que ya estan adentro
    # pero apagados solo se prenden, para no mudarlos de donde ML los puso.
    for acc, antes in (("agregar", "idle"), ("activar", "paused")):
        filas = sumar[sumar["accion"] == acc]
        if not len(filas):
            continue
        res2 = panel_ads.aplicar(sesion, ml, filas, accion=acc,
                                 callback=lambda i, t, d: log(f"  {i}/{t} {d}"))
        ok2 = int((res2["resultado"] == "OK").sum())
        log(f"{acc.capitalize()}: {ok2} de {len(res2)}.")
        auditoria += _auditar(res2, antes, "active")

    guardado, detalle = almacen.append_auditoria(auditoria)
    if not guardado:
        log(f"AVISO: no se pudo escribir la auditoría: {detalle}")

    if ok < len(res):
        log("\nLos que fallaron suelen ser benignos: anuncios en `hold` que "
            "ML deshabilitó, o que el listado trae desactualizados.")
    return 0


def _auditar(res, antes, despues):
    return [{
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": r["item_id"], "campo": "ad_status",
        "valor_anterior": antes, "valor_nuevo": despues,
        "resultado": r["resultado"], "operador": "cron",
        "nota": str(r.get("motivo", ""))[:180],
    } for _, r in res.iterrows()]


def candidatos_a_sumar(ml, df_ads, pubs, log):
    """
    Las publicaciones que convierten y no se publicitan.

    El analisis de visitas es **una llamada por publicacion** (ML no acepta
    mas de un id en `/items/visits`), o sea ~10 minutos para el catalogo. Es
    lo mas caro de la corrida y por eso va al final: si algo falla antes, al
    menos se apago lo que habia que apagar.
    """
    import conversion
    try:
        log("\nMidiendo visitas y ventas (tarda unos minutos)...")
        conv = conversion.analizar(ml, dias=DIAS,
                                   callback=lambda m: log(f"  {m}"))
    except Exception as e:
        log(f"AVISO: no pude medir conversión ({type(e).__name__}: "
            f"{str(e)[:120]}). Esta corrida solo apaga.")
        return pd.DataFrame()

    advs = publicidad.anunciantes(ml)
    camps = {a["advertiser_id"]: publicidad.campanas(ml, a["advertiser_id"])
             for a in advs}
    c = publicidad.candidatos(conv, pubs, df_ads, advs, camps)
    if not len(c):
        return c
    c = c[c["accion"] == "agregar"].sort_values("visitas", ascending=False)
    # Una llamada por candidato, asi que primero se recorta al tope.
    c = publicidad.resolver_candidatos(ml, c.head(TOPE_POR_CORRIDA * 2),
                                       callback=log)
    return c[c["accion"].isin(("agregar", "activar"))
             & c["ad_group_id"].notna()]


def publicidad_catalogo(ml):
    from catalogo import bajar_catalogo
    return bajar_catalogo(ml)


def main():
    return correr(aplicar="--aplicar" in sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
