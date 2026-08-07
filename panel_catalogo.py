#!/usr/bin/env python3
"""
Sacar publicaciones de una ficha de catalogo, por el panel de vendedores.

La API publica **no deja**: `PUT /items/{id}` con `catalog_listing: false`
contesta 400 `field_not_updatable`, y no hay ruta alternativa (`/moderations`,
`/catalog_listings/{id}` dan 404). El panel si puede, en dos llamadas.

**Sacar del catalogo CIERRA la publicacion.** No la convierte en tradicional:
lo que sigue vendiendo es el *gemelo tradicional* que ML crea al anotarse al
catalogo, con el mismo precio y otro ID. Medido el 7/8/2026 sobre
MLA1635845677, que quedo `closed` mientras MLA2747742606 siguio activa. Por
eso, antes de tocar nada, se verifica **en vivo** que quede algo del producto.

Es irreversible: volver a entrar al catalogo crea una publicacion nueva, sin
historia. No existe "probar y ver".

Autenticacion: alcanza la cookie `ssid` (la misma de `panel_ads`), pero ademas
hace falta un **`x-csrf-token` por sesion**, que sale de la propia pagina del
panel. Por eso cada operacion arranca con un GET.
"""

import json
import re
import sys
import time
from datetime import datetime

import requests

import almacen
import panel_ads
from meli import Meli, MeliError

PANEL = "https://www.mercadolibre.com.ar"
PAGINA = PANEL + "/productizar/catalogo/{mla}"
OPTOUT = PANEL + "/productizar/catalogo/api/optin-up/{up}/product-suggestions/optout"
CONFIRM = PANEL + "/productizar/catalogo/api/competition-report/{up}/confirm"

NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

HOJA = "catalogo_sacadas"
COLUMNAS = ["fecha", "item_id", "user_product_id", "ficha", "sku", "titulo",
            "precio", "motivo", "operador", "resultado", "json"]

# `paused` se reactiva cuando uno quiere; `closed` no vuelve.
VIVA = ("active", "paused")

# El panel pide un motivo de texto libre. Va uno honesto: no estamos
# corrigiendo la ficha, estamos sacando una publicacion que compite con otra
# nuestra.
MOTIVO = "Tengo otra publicación mía compitiendo en la misma ficha"


def _sesion():
    return panel_ads.leer_sesion()


def contexto(mla, sesion=None):
    """
    Abre la pagina del panel y saca lo que hace falta para escribir:
    el `x-csrf-token`, el `MLAU` (user product) y las cookies de la respuesta.

    **El MLA no sirve para escribir**: los endpoints van por user product.
    """
    s = sesion or _sesion()
    r = requests.get(PAGINA.format(mla=mla),
                     headers={"User-Agent": NAVEGADOR,
                              "Accept": "text/html,application/xhtml+xml",
                              "Accept-Language": "es-AR,es;q=0.9"},
                     cookies={"ssid": s["ssid"]}, timeout=60)
    if r.status_code != 200:
        raise MeliError(f"el panel contestó {r.status_code} para {mla}")
    if "/login" in r.url or "iniciá sesión" in r.text[:4000].lower():
        raise MeliError("la sesión venció: volvé a copiar el ssid")

    tok = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', r.text)
    ups = re.findall(r"MLAU\d+", r.text)
    if not tok:
        raise MeliError(f"no encontré el csrf-token en la página de {mla}")
    if not ups:
        raise MeliError(f"no encontré el user product (MLAU) de {mla}")

    galletas = {"ssid": s["ssid"]}
    galletas.update({k: v for k, v in r.cookies.items()})
    return {"csrf": tok.group(1), "user_product": ups[0], "cookies": galletas}


def _headers(ctx, mla):
    return {
        "User-Agent": NAVEGADOR,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": PANEL,
        "Referer": PAGINA.format(mla=mla),
        "x-csrf-token": ctx["csrf"],
    }


def _sigue_viva(ml, item, gemelos_de):
    """
    Que quede algo del producto despues de cerrar `item`: el gemelo
    tradicional, o alguna hermana viva en la misma ficha.

    **Se relee en vivo, no del cache.** El analisis puede tener dias y la
    publicacion que lo respaldaba pudo cerrarse en el medio.
    """
    d = ml.get(f"/items/{item}")
    ficha = d.get("catalog_product_id")
    if not d.get("catalog_listing"):
        return False, f"{item} ya no está en catálogo"
    if d.get("status") not in VIVA:
        return False, f"{item} está {d.get('status')}: no hay nada que sacar"

    sku = ""
    for a in (d.get("attributes") or []):
        if a.get("id") == "SELLER_SKU":
            sku = (a.get("value_name") or "").strip().upper()
    sku = sku or (d.get("seller_custom_field") or "").strip().upper()

    for otro in gemelos_de.get(sku, []):
        if otro == item:
            continue
        x = ml.get(f"/items/{otro}")
        if x.get("status") in VIVA and not x.get("catalog_listing"):
            return True, f"queda el gemelo tradicional {otro}"
        if x.get("status") in VIVA and x.get("catalog_product_id") == ficha:
            return True, f"queda {otro} viva en la misma ficha"
    return False, (f"{item} es la única viva del producto: sacarla lo deja "
                   f"sin vidriera")


def sacar(mla, sesion=None, motivo=MOTIVO, ctx=None):
    """
    Las dos llamadas del panel. Devuelve (ok, detalle).

    No verifica nada: para eso esta `sacar_lote`, que es lo que hay que usar.
    """
    ctx = ctx or contexto(mla, sesion)
    up = ctx["user_product"]
    h = _headers(ctx, mla)

    r1 = requests.patch(OPTOUT.format(up=up), headers=h, cookies=ctx["cookies"],
                        json={"flow": "REPRODUCTIZE", "catalogProductId": None,
                              "variationsIds": [up]}, timeout=90)
    if r1.status_code >= 400:
        return False, f"optout {r1.status_code}: {r1.text[:200]}"

    r2 = requests.post(CONFIRM.format(up=up), headers=h, cookies=ctx["cookies"],
                       json={"flow": "REPRODUCTIZE", "reason": motivo,
                             "entityId": up, "type": "CLASSIC"}, timeout=90)
    if r2.status_code >= 400:
        return False, f"confirm {r2.status_code}: {r2.text[:200]}"

    paso = (r2.json() or {}).get("step", "")
    return True, paso or "confirmado"


def _respaldar(ml, item, ctx, motivo, operador, resultado):
    """
    Guarda la publicacion entera antes de cerrarla. **Si el respaldo falla,
    revienta y no se sigue**: cerrar sin respaldo es perder el producto.

    `append_hoja` recibe **diccionarios** y devuelve `(ok, detalle)`; no mira
    el valor de retorno deja el respaldo en silencio (paso el 7/8/2026 con
    MLA2704683956, que se cerro sin respaldo).
    """
    d = ml.get(f"/items/{item}")
    sku = next((a.get("value_name") for a in (d.get("attributes") or [])
                if a.get("id") == "SELLER_SKU"), "") or ""
    fila = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "item_id": item,
        "user_product_id": (ctx or {}).get("user_product", ""),
        "ficha": d.get("catalog_product_id") or "",
        "sku": sku,
        "titulo": (d.get("title") or "")[:120],
        "precio": d.get("price"),
        "motivo": motivo,
        "operador": operador,
        "resultado": resultado,
        "json": json.dumps(d, ensure_ascii=False),
    }
    ok, detalle = almacen.append_hoja(HOJA, COLUMNAS, [fila])
    if not ok:
        raise MeliError(f"no pude respaldar {item}, no la saco: {detalle}")


def sacar_lote(ml, items, gemelos_de, operador="", callback=None,
               pausa=1.5, hasta=None):
    """
    Saca varias, verificando cada una antes.

    **Una que falla no corta el lote** y cada resultado queda registrado, asi
    que se puede retomar sin repetir: las que salieron bien ya no estan en
    catalogo y la verificacion las saltea sola.

    `hasta` corta despues de N exitosas — para arrancar de a poco.
    """
    hechas, salida = 0, []
    sesion = _sesion()
    for item in items:
        if hasta and hechas >= hasta:
            salida.append({"item_id": item, "ok": None,
                           "detalle": f"no se intentó (tope de {hasta})"})
            continue
        try:
            puede, porque = _sigue_viva(ml, item, gemelos_de)
            if not puede:
                salida.append({"item_id": item, "ok": False,
                               "detalle": f"salteada: {porque}"})
                if callback:
                    callback(f"{item} salteada — {porque}")
                continue

            ctx = contexto(item, sesion)
            _respaldar(ml, item, ctx, MOTIVO, operador, "por intentar")
            ok, detalle = sacar(item, sesion, ctx=ctx)
            if ok:
                hechas += 1
            salida.append({"item_id": item, "ok": ok,
                           "detalle": f"{detalle} ({porque})"})
            if callback:
                callback(f"{item} {'✓' if ok else '✗'} {detalle}")
        except Exception as e:
            salida.append({"item_id": item, "ok": False,
                           "detalle": f"error: {str(e)[:160]}"})
            if callback:
                callback(f"{item} ✗ {str(e)[:80]}")
        time.sleep(pausa)
    return salida


def gemelos_desde_catalogo(pubs):
    """SKU -> publicaciones del mismo SKU, para poder verificar en vivo."""
    from catalogo import sku_del_atributo
    from collections import defaultdict
    d = defaultdict(list)
    for p in pubs:
        sku = (sku_del_atributo(p) or p.get("seller_custom_field")
               or "").strip().upper()
        if sku:
            d[sku].append(p["id"])
    return d


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("uso: python panel_catalogo.py MLA123 [MLA456 ...] [--hacerlo]")
        return 1
    items = [a for a in sys.argv[1:] if a.startswith("MLA")]
    de_verdad = "--hacerlo" in sys.argv

    ml = Meli(verbose=False)
    pubs = json.loads((__import__("pathlib").Path(__file__).resolve().parent
                       / "catalogo.json").read_text(encoding="utf-8"))
    gem = gemelos_desde_catalogo(pubs)

    if not de_verdad:
        print("SIMULACRO (agregá --hacerlo para ejecutar)\n")
        for it in items:
            puede, porque = _sigue_viva(ml, it, gem)
            print(f"  {it}  {'se puede' if puede else 'NO'} — {porque}")
        return 0

    print("SACANDO DEL CATÁLOGO — es irreversible\n")
    for r in sacar_lote(ml, items, gem, operador="cli",
                        callback=lambda m: print("  " + m)):
        print(f"  {r['item_id']}: {r['detalle']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
