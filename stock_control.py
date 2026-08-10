#!/usr/bin/env python3
"""
Control de stock propio, con historico de movimientos.

Es un control PARALELO al stock de MercadoLibre: lee las ventas y las va
descontando de un stock inicial que carga el operador. **No toca el stock de
ML** — solo registra.

    python stock_control.py                 -> sincroniza las ventas nuevas
    python stock_control.py --dias 30       -> revisa 30 dias hacia atras

Reglas acordadas:

  - La unidad se descuenta **cuando la orden se paga**. Es lo mas parecido a
    lo que hace ML y evita vender lo que ya esta comprometido.
  - Si despues se cancela, se devuelve al stock automaticamente.
  - Las **devoluciones no vuelven solas**: quedan en una bandeja aparte para
    que alguien confirme si la unidad esta apta para volver a venderse.
  - Las compras y los ajustes los carga el operador a mano.

Lo importante del diseño: **es idempotente**. Cada movimiento tiene una clave
unica derivada de la orden, asi que correrlo cada 15 minutos (o dos veces
seguidas) no puede duplicar nada.
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

import pathlib

import almacen
from meli import Meli, MeliError
from ventas import traer_ordenes

HOJA_INICIAL = "stock_inicial"
HOJA_MOV = "movimientos"
HOJA_DEV = "devoluciones"

COLS_INICIAL = ["sku", "descripcion", "cantidad", "fecha", "operador"]
COLS_MOV = ["id_mov", "fecha", "sku", "tipo", "cantidad", "referencia",
            "detalle", "operador"]
COLS_DEV = ["id_dev", "fecha", "sku", "cantidad", "order_id", "motivo",
            "estado_ml", "resolucion", "operador", "fecha_resolucion"]

# Estados de orden que cuentan como venta efectiva.
VENDIDAS = ("paid", "partially_refunded")

# Cuantos dias hacia atras revisamos por defecto. Tiene que ser mayor al
# tiempo que puede tardar una orden en cancelarse, si no las cancelaciones
# tardias no se enteran.
DIAS_VENTANA = 7


# ------------------------------------------------------------------ claves

# ---------------------------------------------------------------- combos

HOJA_COMBOS = "combos"
COLS_COMBOS = ["combo", "componente", "multiplicador"]
ARCHIVO_COMBOS = (pathlib.Path(__file__).resolve().parent
                  / "composicion_combos.csv")


def composicion():
    """
    SKU de combo -> [(componente, multiplicador), ...].

    **Vender un combo descuenta stock de sus componentes, no del combo.** El
    combo no existe en el depósito: es una forma de vender. Sin esto, el
    candado que se fue dentro de 11 combos figura con 10 ventas en vez de 21,
    y el stock queda alto — se sigue ofreciendo lo que ya no está.

    Sale de la hoja `combos` si existe, y si no del CSV. **Ojo con el Excel
    original**: las celdas de la columna combo vienen combinadas, o sea vacías
    en las filas 2ª en adelante de cada grupo. Sin rellenar hacia abajo, los
    149 kits de varios componentes parecen packs de uno solo.
    """
    filas = []
    try:
        filas = almacen.leer_hoja(HOJA_COMBOS, COLS_COMBOS)
    except Exception:                              # noqa: BLE001
        filas = []
    if not filas and ARCHIVO_COMBOS.exists():
        import csv
        with open(ARCHIVO_COMBOS, encoding="utf-8") as f:
            filas = list(csv.DictReader(f))

    salida = {}
    ultimo = ""
    for f in filas:
        combo = str(f.get("combo") or f.get("SKU Combo") or "").strip().upper()
        combo = combo or ultimo          # celdas combinadas
        ultimo = combo
        comp = str(f.get("componente") or f.get("Composición") or "").strip().upper()
        if not combo or not comp:
            continue
        try:
            mult = int(float(f.get("multiplicador") or f.get("Multiplicador") or 1))
        except (TypeError, ValueError):
            mult = 1
        salida.setdefault(combo, []).append((comp, max(mult, 1)))
    return salida


def explotar(sku, cantidad, comp=None):
    """
    Un SKU vendido -> lo que hay que descontar del depósito.

    Devuelve [(sku_real, unidades), ...]. Si no es un combo, se devuelve tal
    cual. **No es recursivo a propósito**: un combo de combos no existe hoy y
    soportarlo invita a un bucle infinito si alguien carga mal la tabla.
    """
    comp = composicion() if comp is None else comp
    partes = comp.get(str(sku).strip().upper())
    if not partes:
        return [(sku, cantidad)]
    return [(c, cantidad * m) for c, m in partes]


def clave_venta(order_id, item_id):
    return f"v:{order_id}:{item_id}"


def clave_cancelacion(order_id, item_id):
    return f"c:{order_id}:{item_id}"


# ------------------------------------------------------------------ lectura

def movimientos():
    return almacen.leer_hoja(HOJA_MOV, COLS_MOV)


def claves_registradas():
    """Solo la columna de claves: es lo unico que hace falta para no duplicar."""
    return set(almacen.columna_hoja(HOJA_MOV, COLS_MOV, "id_mov"))


def stock_inicial():
    return almacen.leer_hoja(HOJA_INICIAL, COLS_INICIAL)


def devoluciones():
    return almacen.leer_hoja(HOJA_DEV, COLS_DEV)


def _num(v):
    try:
        return float(str(v).replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


def stock_actual():
    """
    Arma la tabla de stock: parte del inicial y le aplica todos los
    movimientos registrados.
    """
    inicial = {}
    desc = {}
    for f in stock_inicial():
        sku = str(f.get("sku", "")).strip().upper()
        if sku:
            inicial[sku] = inicial.get(sku, 0) + _num(f.get("cantidad"))
            if f.get("descripcion"):
                desc[sku] = f["descripcion"]

    movs = defaultdict(lambda: defaultdict(float))
    for m in movimientos():
        sku = str(m.get("sku", "")).strip().upper()
        if sku:
            movs[sku][m.get("tipo", "?")] += _num(m.get("cantidad"))

    filas = []
    for sku in sorted(set(inicial) | set(movs)):
        detalle = movs.get(sku, {})
        total_mov = sum(detalle.values())
        filas.append({
            "sku": sku,
            "descripcion": desc.get(sku, ""),
            "inicial": inicial.get(sku, 0),
            "vendido": -detalle.get("venta", 0),
            "cancelado": detalle.get("cancelacion", 0),
            "comprado": detalle.get("compra", 0),
            "devuelto_ok": detalle.get("devolucion_apta", 0),
            "ajustes": detalle.get("ajuste", 0),
            "stock_actual": inicial.get(sku, 0) + total_mov,
        })

    df = pd.DataFrame(filas)
    if len(df):
        df = df.sort_values("stock_actual")
    return df


# ------------------------------------------------------------------ sync

def sincronizar(ml, dias=DIAS_VENTANA, operador="automatico", callback=None):
    """
    Lee las ventas del periodo y registra los movimientos que falten.

    Devuelve un resumen de lo que hizo. Correrlo dos veces seguidas no
    agrega nada la segunda vez.
    """
    hasta = datetime.now()
    desde = hasta - timedelta(days=dias)

    if callback:
        callback("Trayendo órdenes de MercadoLibre...")
    ordenes = traer_ordenes(ml, desde, hasta)

    if callback:
        callback(f"{len(ordenes)} órdenes. Revisando cuáles son nuevas...")
    ya_estan = claves_registradas()

    nuevos, devoluciones_nuevas = [], []
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    combos = composicion()

    for o in ordenes:
        order_id = o.get("id")
        estado = o.get("status")
        fecha = (o.get("date_created") or "")[:19].replace("T", " ")

        for it in o.get("order_items") or []:
            item_id = it["item"].get("id")
            sku = (it["item"].get("seller_sku")
                   or it["item"].get("seller_custom_field") or "").strip().upper()
            if not sku:
                continue
            cant = it.get("quantity") or 0
            k_venta = clave_venta(order_id, item_id)
            k_cancel = clave_cancelacion(order_id, item_id)

            if estado in VENDIDAS and k_venta not in ya_estan:
                # Un combo se descuenta de sus COMPONENTES. La clave del
                # movimiento lleva el componente para que dos partes de la
                # misma orden no se pisen entre si.
                partes = explotar(sku, cant, combos)
                for n, (real, unidades) in enumerate(partes):
                    nuevos.append({
                        "id_mov": k_venta if len(partes) == 1
                                  else f"{k_venta}-{n}",
                        "fecha": fecha or ahora, "sku": real,
                        "tipo": "venta", "cantidad": -unidades,
                        "referencia": f"orden {order_id}",
                        "detalle": (it["item"].get("title", "")[:60]
                                    if len(partes) == 1
                                    else f"combo {sku}: {it['item'].get('title','')[:40]}"),
                        "operador": operador})
                ya_estan.add(k_venta)

            elif estado == "cancelled":
                # Solo devolvemos al stock si la venta habia sido registrada.
                if k_venta in ya_estan and k_cancel not in ya_estan:
                    partes = explotar(sku, cant, combos)
                    for n, (real, unidades) in enumerate(partes):
                        nuevos.append({
                            "id_mov": k_cancel if len(partes) == 1
                                      else f"{k_cancel}-{n}",
                            "fecha": ahora, "sku": real,
                            "tipo": "cancelacion", "cantidad": unidades,
                            "referencia": f"orden {order_id}",
                            "detalle": "orden cancelada, vuelve al stock",
                            "operador": operador})
                    ya_estan.add(k_cancel)

    ok, detalle = almacen.append_hoja(HOJA_MOV, COLS_MOV, nuevos)

    return {
        "ordenes_revisadas": len(ordenes),
        "movimientos_nuevos": len(nuevos),
        "ventas": sum(1 for m in nuevos if m["tipo"] == "venta"),
        "cancelaciones": sum(1 for m in nuevos if m["tipo"] == "cancelacion"),
        "unidades": sum(abs(m["cantidad"]) for m in nuevos),
        "ok": ok, "detalle": detalle,
        "desde": desde.strftime("%Y-%m-%d"), "hasta": hasta.strftime("%Y-%m-%d"),
    }


def sincronizar_devoluciones(ml, operador="automatico"):
    """
    Trae los reclamos con devolucion y los deja en la bandeja para que el
    operador decida. NO suma nada al stock: eso pasa recien cuando alguien
    confirma que la unidad esta apta.
    """
    ya_estan = {str(d.get("id_dev")) for d in devoluciones()}
    nuevas = []
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        r = ml.get("/post-purchase/v1/claims/search", limit=50)
    except Exception as e:  # noqa: BLE001
        return {"nuevas": 0, "ok": False, "detalle": str(e)[:200]}

    for c in r.get("data", []):
        cid = str(c.get("id"))
        if cid in ya_estan:
            continue
        nuevas.append({
            "id_dev": cid, "fecha": ahora, "sku": "",
            "cantidad": "", "order_id": c.get("resource_id"),
            "motivo": c.get("reason_id", ""),
            "estado_ml": f"{c.get('status')}/{c.get('stage')}",
            "resolucion": "pendiente", "operador": operador,
            "fecha_resolucion": ""})

    ok, detalle = almacen.append_hoja(HOJA_DEV, COLS_DEV, nuevas)
    return {"nuevas": len(nuevas), "ok": ok, "detalle": detalle}


# ------------------------------------------------------------------ carga manual

def registrar(tipo, sku, cantidad, referencia="", detalle="", operador=""):
    """
    Registra un movimiento manual: compra, ajuste o devolucion aprobada.
    La clave lleva la hora para que no choque con otra carga del mismo SKU.
    """
    sku = str(sku).strip().upper()
    cantidad = float(cantidad)
    if tipo not in ("compra", "ajuste", "devolucion_apta"):
        raise ValueError(f"Tipo de movimiento no valido: {tipo}")

    ahora = datetime.now()
    fila = {
        "id_mov": f"{tipo[0]}:{sku}:{ahora.strftime('%Y%m%d%H%M%S%f')}",
        "fecha": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "sku": sku, "tipo": tipo, "cantidad": cantidad,
        "referencia": referencia, "detalle": detalle, "operador": operador,
    }
    return almacen.append_hoja(HOJA_MOV, COLS_MOV, [fila])


def cargar_stock_inicial(filas, operador=""):
    """
    Carga o corrige el stock de arranque desde una planilla.
    `filas` es una lista de dicts con sku, cantidad y (opcional) descripcion.
    """
    hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    a_guardar = [{"sku": str(f["sku"]).strip().upper(),
                  "descripcion": f.get("descripcion", ""),
                  "cantidad": f["cantidad"], "fecha": hoy,
                  "operador": operador} for f in filas]
    return almacen.append_hoja(HOJA_INICIAL, COLS_INICIAL, a_guardar)


def resolver_devolucion(id_dev, resolucion, sku="", cantidad=0, operador=""):
    """
    Cierra una devolucion de la bandeja. Si se marca 'apta', recien ahi la
    unidad vuelve al stock.
    """
    filas = devoluciones()
    encontrada = False
    for f in filas:
        if str(f.get("id_dev")) == str(id_dev):
            f["resolucion"] = resolucion
            f["operador"] = operador
            f["fecha_resolucion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if sku:
                f["sku"] = str(sku).strip().upper()
            if cantidad:
                f["cantidad"] = cantidad
            encontrada = True
            break

    if not encontrada:
        return False, f"No encontre la devolucion {id_dev}"

    almacen.reescribir_hoja(HOJA_DEV, COLS_DEV, filas)

    if resolucion == "apta" and sku and cantidad:
        return registrar("devolucion_apta", sku, cantidad,
                         referencia=f"devolucion {id_dev}",
                         detalle="unidad revisada y apta para la venta",
                         operador=operador)
    return True, ""


def main():
    dias = DIAS_VENTANA
    if "--dias" in sys.argv:
        dias = int(sys.argv[sys.argv.index("--dias") + 1])

    ml = Meli(verbose=False)
    print(f"Sincronizando ventas de los ultimos {dias} dias...")
    r = sincronizar(ml, dias=dias, callback=lambda m: print(f"  {m}"))

    print(f"\n  ordenes revisadas   {r['ordenes_revisadas']:>6}")
    print(f"  movimientos nuevos  {r['movimientos_nuevos']:>6}")
    print(f"    ventas            {r['ventas']:>6}")
    print(f"    cancelaciones     {r['cancelaciones']:>6}")
    print(f"  unidades movidas    {r['unidades']:>6.0f}")
    if not r["ok"]:
        print(f"\n  ERROR al guardar: {r['detalle']}")
        return 1

    df = stock_actual()
    print(f"\n  SKU con movimiento: {len(df)}")
    if len(df):
        print("\n  Los 10 con menos stock:")
        print(df.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
