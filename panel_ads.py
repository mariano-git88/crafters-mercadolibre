#!/usr/bin/env python3
"""
Aplica cambios de publicidad por el **endpoint interno del panel**, que es lo
unico que hoy acepta escritura.

    python panel_ads.py                      -> que haria, sin tocar nada
    python panel_ads.py --aplicar            -> pausa lo que marcan las reglas
    python panel_ads.py --activar ID [ID...] -> vuelve a encender ad_groups

**Por que existe esto.** La API publica de Product Ads rechaza toda escritura
para esta cuenta y esta app (`401 User does not have permission to write`, ver
`publicidad.py`). El panel de MercadoLibre si puede, porque **no usa la API
publica**: usa

    PUT https://pa.mercadolibre.com.ar/pa/api/admin-pads/ajax/ads/actions/status
    {"ids": ["<ad_group_id>", ...], "allSelected": false, "status": "paused"}

con **cookies de sesion** y el header `x-csrf-token`. No hay OAuth.

**Corre solo en una maquina con sesion.** No sirve desde Streamlit Cloud ni
desde GitHub Actions: no hay navegador ni cookies ahi. Es "abro la compu y
aplico", no automatico. Y se rompe cuando ML cambie el panel.

**La sesion se saca del navegador y vence.** En el panel de Publicidad: F12 ->
Network -> pausar cualquier anuncio -> click derecho en la llamada ->
Copy as cURL. De ahi salen `cookie` y `x-csrf-token`, que van a
`sesion_ads.json` (ignorado por git; **nunca commitear esto**).

    {"cookie": "orgnickp=...; ssid=...; _csrf=...",
     "csrf": "Io2QW8bu-..."}

Dos cosas que costaron encontrar:

  - **El lote falla si mezcla campanas o anunciantes.** Mandar 11 ids de dos
    anunciantes devuelve 400 para todos. Hay que agrupar y mandar el referer
    de la campana correspondiente.
  - **El anunciante viaja en una cookie**, `_ma_dsp_account-structure`. La
    sesion queda fijada al que estabas mirando; para tocar otro hay que
    reescribirla.
"""

import json
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd

import publicidad
from meli import Meli

DIR = Path(__file__).resolve().parent
SESION = DIR / "sesion_ads.json"

URL = ("https://pa.mercadolibre.com.ar/pa/api/admin-pads/ajax/ads/actions/"
       "status")
COOKIE_ADV = "_ma_dsp_account-structure"


def leer_sesion():
    if not SESION.exists():
        raise SystemExit(
            f"Falta {SESION.name}. Sacá `cookie` y `x-csrf-token` del panel "
            "(F12 → Network → pausar un anuncio → Copy as cURL) y guardalos "
            'así: {"cookie": "...", "csrf": "..."}')
    return json.loads(SESION.read_text(encoding="utf-8"))


def _cookie_para(cookie, advertiser_id):
    """Reescribe el anunciante de la sesion."""
    nuevo = urllib.parse.quote(
        json.dumps({"advertiserId": str(advertiser_id), "accountId": "645"},
                   separators=(",", ":")), safe="")
    partes = []
    for trozo in cookie.split("; "):
        if trozo.startswith(COOKIE_ADV + "="):
            partes.append(f"{COOKIE_ADV}={nuevo}")
        else:
            partes.append(trozo)
    if not any(p.startswith(COOKIE_ADV) for p in partes):
        partes.append(f"{COOKIE_ADV}={nuevo}")
    return "; ".join(partes)


def cambiar(sesion, ad_group_ids, advertiser_id, campaign_id, estado):
    """
    Cambia el estado de uno o varios ad_groups **de la misma campana**.

    Devuelve (ok_ids, fallidos). No lanza.
    """
    import requests
    ref = ("https://ads.mercadolibre.com.ar/product-ads/admin/campaigns/"
           f"{campaign_id}/dashboard")
    try:
        r = requests.put(
            URL,
            headers={"accept": "application/json",
                     "content-type": "application/json",
                     "cookie": _cookie_para(sesion["cookie"], advertiser_id),
                     "x-csrf-token": sesion["csrf"],
                     "x-requested-with": "XMLHttpRequest",
                     "origin": "https://ads.mercadolibre.com.ar",
                     "referer": ref, "x-pads-page-href": ref,
                     "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; "
                                    "x64) AppleWebKit/537.36 Chrome/150.0 "
                                    "Safari/537.36")},
            json={"ids": [str(i) for i in ad_group_ids],
                  "allSelected": False, "status": estado},
            timeout=90)
    except Exception as e:
        return [], [{"error": f"{type(e).__name__}: {e}"}]

    if r.status_code == 403:
        return [], [{"error": "la sesión venció: volvé a copiarla del panel"}]
    try:
        j = r.json()
    except ValueError:
        return [], [{"error": f"HTTP {r.status_code}: {r.text[:150]}"}]
    return j.get("succeededIds") or [], j.get("failed") or []


def estado_real(ml, ad_group_id):
    """
    El estado que vale. **`ads/{item_id}` viene atrasado**: despues de pausar
    sigue diciendo `active` un buen rato, y hace creer que la escritura no
    entro. `ad_groups/{id}` contesta la verdad (en mayusculas: PAUSED).
    """
    try:
        g = ml.get(publicidad._ruta_ad_group(ad_group_id),
                   _headers=publicidad.CABECERA)
        return (g or {}).get("status")
    except Exception:
        return None


def aplicar(sesion, ml, plan, estado="paused", callback=None):
    """
    Agrupa por (anunciante, campana) y manda un lote por grupo — mezclarlos
    devuelve 400 para todos — y despues verifica contra `ad_groups`.
    """
    salida = []
    faltan = plan[plan["ad_group_id"].notna()]
    for (adv, camp), g in faltan.groupby(["advertiser_id", "campaign_id"]):
        ids = [int(x) for x in g["ad_group_id"]]
        if callback:
            callback(f"Anunciante {adv}, campaña {camp}: {len(ids)}...")
        ok, fallidos = cambiar(sesion, ids, adv, camp, estado)
        for _, r in g.iterrows():
            ag = int(r["ad_group_id"])
            salida.append({
                "item_id": r.get("item_id"), "ad_group_id": ag,
                "titulo": r.get("titulo", ""), "gasto": r.get("gasto", 0),
                "motivo": r.get("motivo", ""),
                "enviado": "OK" if str(ag) in ok else "ERROR",
                "estado_real": estado_real(ml, ag),
            })
        time.sleep(1)
    return pd.DataFrame(salida)


def main():
    ml = Meli(verbose=False)
    if "--activar" in sys.argv:
        sesion = leer_sesion()
        ids = [a for a in sys.argv[sys.argv.index("--activar") + 1:]
               if a.isdigit()]
        for ag in ids:
            g = ml.get(publicidad._ruta_ad_group(ag),
                       _headers=publicidad.CABECERA)
            ok, fall = cambiar(sesion, [ag], g.get("advertiser_id"),
                               g.get("campaign_id"), "active")
            print(f"  {ag}: {'OK' if ok else fall}")
        return 0

    ruta = DIR / "publicidad_a_pausar.csv"
    if not ruta.exists():
        print("Falta publicidad_a_pausar.csv: generalo desde la app "
              "(Publicidad → Qué haría con los anuncios).")
        return 1
    plan = pd.read_csv(ruta)
    plan = plan[plan.get("gasto", 0) > 0]
    print(f"{len(plan)} anuncios con gasto para pausar.")
    if "--aplicar" not in sys.argv:
        print("Corré con --aplicar para hacerlo.")
        return 0

    res = aplicar(leer_sesion(), ml, plan,
                  callback=lambda m: print(f"  {m}"))
    print(res.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
