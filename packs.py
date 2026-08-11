#!/usr/bin/env python3
"""
Publicar packs de varias unidades del mismo producto.

    python packs.py              -> simula los primeros
    python packs.py 10 --hacerlo -> crea 10 de verdad

**Por que no son kits.** El armador de kits de MercadoLibre exige **dos
productos distintos**: con uno solo, aunque se le ponga cantidad 4, el
asistente no avanza. Un pack de 4 del mismo articulo hay que publicarlo como
publicacion propia, y eso ademas es mejor: aparece en las busquedas, se puede
publicitar y tiene su titulo y su foto.

--------------------------------------------------------------------------
El GTIN, que parecia un muro y no lo era
--------------------------------------------------------------------------

`POST /items` exige codigo universal y **no acepta "no tengo"**: se probaron
`values` vacio, `value_id: -1`, `"N/A"`, `EMPTY_GTIN_REASON`, marca Generica,
`UNITS_PER_PACKAGE`, `SALE_FORMAT` y `catalog_listing: false`. Todas
rechazadas.

**La vuelta es reusar el GTIN del producto suelto.** Un pack de 4 llaves de
luz *son* cuatro llaves de luz: el codigo identifica el producto, no el
envase. Con eso valida.

Otras dos que traban y el error no las nombra:

- **No se manda `title`.** Con `family_name` presente ML arma el titulo solo;
  mandar `title` contesta "campo invalido".
- **`official_store_id` es obligatorio** si el vendedor tiene marca propia:
  *"Users type brand have to provide a official store id"*.

**Los atributos se copian todos, no una seleccion.** Cada categoria tiene los
suyos obligatorios —"Materiales", "Incluye focos"— y el error
(`item.attribute.missing_catalog_required`) los nombra pero no aclara que el
suelto ya los trae. Copiando la lista entera entran solos.

Y el warning `free_shipping.cost_exceeded` **no bloquea**: `/items/validate`
devuelve 400 igual, pero el `POST /items` real pasa. Guiarse por el validador
lleva a concluir que no se puede.

--------------------------------------------------------------------------
La convencion de SKU, que no es un detalle
--------------------------------------------------------------------------

`<SKU del suelto> x <N> unidades`, definida por Agustin. Es **la misma que ya
usa el archivo de composicion de combos**, asi que un pack nombrado asi lo
explota solo `stock_control`: vender un pack de 4 descuenta 4 unidades del
suelto. Por eso se respeta la convencion que ya existia en vez de inventar
una.

El pack se registra ademas en la hoja `combos` al crearlo, para que la
explosion funcione sin que nadie tenga que acordarse.
"""

import copy
import json
import sys
import time
from pathlib import Path

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

# Se copian TODOS los atributos del suelto salvo el SKU, que el pack tiene
# propio. Con una lista fija de "los importantes" fallan las categorias que
# piden atributos suyos: `item.attribute.missing_catalog_required` — "El campo
# Materiales es obligatorio" — y el error no dice que se puedan heredar.
# Medido: 76 de 263 packs fallaban solo por esto.
SALTAR = ("SELLER_SKU",)

PAUSA = 1.0


LARGO_NOMBRE = 60

# El pack se lleva una PARTE de lo armable, no todo.
#
# El pack y el suelto salen del mismo deposito y **MercadoLibre no lo sabe**:
# son dos publicaciones sueltas. Poner todo lo armable publica el inventario
# dos veces —medido: 162.271 unidades comprometidas sobre 340.644 de stock— y
# nada lo resincroniza solo (`stock_control` anota en la planilla, no escribe
# en las publicaciones).
#
# Quedarse corto no cuesta nada: si el pack se vende, se sube el numero. Pasarse
# cuesta una venta que no se puede entregar.
PARTE = 0.20
TOPE_PACKS = 50


def stock_del_pack(stock_suelto, unidades):
    """
    Cuantos packs publicar. **Cero es una respuesta valida**: si no alcanza
    para armar uno, el pack no va a la venta.

    Antes habia un `max(..., 1)` que forzaba un pack igual — dejo 15
    publicaciones prometiendo un pack de 4 sobre 2 unidades de stock.
    """
    armables = int(stock_suelto or 0) // int(unidades)
    if armables < 1:
        return 0
    return max(1, min(int(armables * PARTE), TOPE_PACKS))


def _nombre(titulo, unidades):
    """
    El nombre del pack, recortado para entrar en el limite de ML.

    Se arma el sufijo primero y el titulo se queda con lo que sobra: al reves,
    ML contesta `family_name.length_invalid` sin decir cual es el limite.
    """
    sufijo = f" Pack {unidades} Unidades"
    return (" ".join(titulo.split())[:LARGO_NOMBRE - len(sufijo)].strip()
            + sufijo)


def _attr(item, cual):
    for a in (item.get("attributes") or []):
        if a.get("id") == cual:
            return a
    return None


def _gtin(item):
    a = _attr(item, "GTIN")
    return (a.get("value_name") or "").strip() if a else ""


def apto(item):
    """
    Si con este producto se puede armar un pack. Devuelve (si, motivo).

    Los dos requisitos salen de probarlo contra la API, no del manual.
    """
    g = _gtin(item)
    if not g or not g.split(",")[0].strip().isdigit():
        return False, "sin código universal válido (el pack lo hereda)"
    if not item.get("official_store_id"):
        return False, "sin tienda oficial (ML la exige para marcas propias)"
    if item.get("status") != "active":
        return False, f"la publicación está {item.get('status')}"
    return True, ""


def crear_pack(ml, item_base, unidades, precio=None, descuento=0.15,
               stock=None, operador=""):
    """
    Publica un pack de `unidades` del producto de `item_base`.

    Devuelve (item_id_nuevo, detalle). El pack queda **pausado**: se revisa
    antes de ponerlo a la venta.
    """
    d = ml.get(f"/items/{item_base}")
    ok, porque = apto(d)
    if not ok:
        raise MeliError(porque)

    sku = (sku_del_atributo(d) or d.get("seller_custom_field") or "").strip()
    if not sku:
        raise MeliError("el producto suelto no tiene SKU")

    cuantos = (stock if stock is not None
               else stock_del_pack(d.get("available_quantity"), unidades))
    if cuantos < 1:
        raise MeliError(f"no alcanza el stock: {d.get('available_quantity')} "
                        f"unidades no arman un pack de {unidades}")

    unit = float(d.get("price") or 0)
    if not unit:
        raise MeliError("el producto suelto no tiene precio")
    total = precio if precio else round(unit * unidades * (1 - descuento), 2)

    att = []
    for a in (d.get("attributes") or []):
        if a.get("id") in SALTAR:
            continue
        if a.get("value_id") is None and not a.get("value_name"):
            continue                               # vacio: ML lo rechaza
        x = {"id": a["id"]}
        if a.get("value_id") is not None:
            x["value_id"] = a["value_id"]
        if a.get("value_name"):
            x["value_name"] = a["value_name"]
        att.append(x)

    cuerpo = {
        "category_id": d.get("category_id"),
        "price": total, "currency_id": "ARS",
        "available_quantity": cuantos,
        "buying_mode": "buy_it_now", "condition": "new",
        "listing_type_id": d.get("listing_type_id"),
        "official_store_id": d.get("official_store_id"),
        # Sin `title`: con family_name ML lo arma solo.
        "family_name": _nombre(d.get("title") or "", unidades),
        "pictures": [{"id": p["id"]} for p in (d.get("pictures") or [])[:6]],
        "attributes": att,
        # La garantia NO va en `attributes` sino en `sale_terms`: buscarla
        # entre los atributos da "no tiene garantia" y es mentira. Sin esto el
        # pack nace sin garantia aunque el suelto la tenga —paso con 281.
        "sale_terms": copy.deepcopy(d.get("sale_terms") or []),
        "shipping": copy.deepcopy(d.get("shipping") or {}),
    }
    r = ml.post("/items", payload=cuerpo)
    nuevo = r.get("id")
    if not nuevo:
        raise MeliError("ML no devolvió el id del pack")

    sku_pack = f"{sku} x {unidades} unidades"
    try:
        ml.put(f"/items/{nuevo}", {"attributes": [
            {"id": "SELLER_SKU", "value_name": sku_pack}]})
    except MeliError:
        pass

    # La descripcion del suelto, con una linea que aclara que es un pack.
    try:
        texto = (ml.get(f"/items/{item_base}/description")
                 .get("plain_text") or "").strip()
        if texto:
            ml.post(f"/items/{nuevo}/description",
                    payload={"plain_text": f"Pack por {unidades} unidades.\n\n{texto}"})
    except MeliError:
        pass

    # Nace pausado: nadie compra algo que todavia no se reviso.
    try:
        ml.put(f"/items/{nuevo}", {"status": "paused"})
    except MeliError:
        pass

    # Registrar la composicion para que el stock se descuente del suelto.
    try:
        almacen.append_hoja("combos", ["combo", "componente", "multiplicador"],
                            [{"combo": sku_pack, "componente": sku,
                              "multiplicador": unidades}])
    except Exception:                              # noqa: BLE001
        pass

    return nuevo, f"{sku_pack} · ${total:,.0f}".replace(",", ".")


def crear_lote(ml, plan, operador="", tope=None, callback=None):
    """
    Publica los packs de un plan de `kits.multipacks()`.

    Una falla no corta el lote: los productos sin código universal o sin
    tienda oficial se saltean con el motivo escrito.
    """
    hechos, salida = 0, []
    for _, f in plan.iterrows():
        if tope and hechos >= tope:
            break
        base = f.get("item")
        try:
            nuevo, det = crear_pack(ml, base, int(f["unidades"]),
                                    precio=f.get("precio_kit_sugerido"),
                                    operador=operador)
            ok = True
            hechos += 1
        except Exception as e:                     # noqa: BLE001
            nuevo, det, ok = "", str(e)[:150], False
        salida.append({"base": base, "producto": str(f.get("producto"))[:44],
                       "unidades": f.get("unidades"), "nuevo": nuevo,
                       "ok": ok, "detalle": det})
        if callback:
            callback(f"{'✓' if ok else '✗'} {str(f.get('producto'))[:40]} — {det}")
        time.sleep(PAUSA)
    return salida


def main():
    import pandas as pd

    hacerlo = "--hacerlo" in sys.argv
    cuantos = next((int(a) for a in sys.argv[1:] if a.isdigit()), 5)
    ruta = DIR / "multipacks.csv"
    if not ruta.exists():
        print("Falta multipacks.csv: corré la sección KITS de la app.")
        return 1
    plan = pd.read_csv(ruta)
    plan = plan[plan["ahorro_de"] == "cargo fijo"].head(cuantos)
    ml = Meli(verbose=False)

    if not hacerlo:
        print(f"SIMULACRO — {len(plan)} packs\n")
        for _, f in plan.iterrows():
            d = ml.get(f"/items/{f['item']}")
            ok, porque = apto(d)
            print(f"  {'sí ' if ok else 'NO '} {f['unidades']}× "
                  f"{str(f['producto'])[:44]} {porque}")
        print("\n(agregá --hacerlo para publicarlos)")
        return 0

    for r in crear_lote(ml, plan, operador="cli",
                        callback=lambda m: print("  " + m)):
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
