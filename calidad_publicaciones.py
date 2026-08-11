#!/usr/bin/env python3
"""Completar las publicaciones creadas hoy con lo que ya sabemos del producto.

    python mejorar_calidad.py             -> simula
    python mejorar_calidad.py --hacerlo   -> escribe

**Nada de cuotas ni de envio gratis**: se completa informacion del producto,
no condiciones comerciales.
"""
import csv
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.getcwd())
from catalogo import sku_del_atributo
from meli import Meli, MeliError

HACERLO = "--hacerlo" in sys.argv
DESDE = "2026-08-08"
PACK = re.compile(r"^(?P<b>.+?)\s+x\s+(?P<u>\d+)\s+unidades\s*$", re.I)

# Lo que NO se copia del producto suelto a un pack.
#
# **Las medidas del bulto son del bulto, no del producto.** Un pack de 4 pesa
# cuatro veces mas y ocupa otro volumen: copiarle el peso del suelto hace que
# ML cotice mal el envio y que el deposito arme mal el paquete. Es plata y es
# un problema operativo, no un dato cosmetico.
#
# Y las que describen el formato de venta (cuantas unidades trae, que tipo de
# envase) contradicen al pack por definicion.
PROHIBIDOS = {
    "PACKAGE_WEIGHT", "PACKAGE_HEIGHT", "PACKAGE_LENGTH", "PACKAGE_WIDTH",
    "UNITS_PER_PACK", "UNITS_PER_PACKAGE", "SALE_FORMAT",
    "SELLER_PACKAGE_TYPE", "SHIPMENT_PACKING", "SELLER_SKU", "GTIN",
    "EMPTY_GTIN_REASON",
}

# Solo se toca lo que se puede tocar: `under_review` rechaza los PUT.
EDITABLES = ("active", "paused")

# La garantia NO vive en `attributes` sino en `sale_terms`. Buscarla entre los
# atributos da "ninguna publicacion tiene garantia", que es falso: 2.474 del
# catalogo ya declaran "Garantia de fabrica / 6 meses".
#
# 6 meses para todo, confirmado por Mariano, y coincide con el estandar que ya
# usa el catalogo.
GARANTIA = [
    {"id": "WARRANTY_TYPE", "value_id": "2230279",
     "value_name": "Garantía de fábrica"},
    {"id": "WARRANTY_TIME", "value_name": "6 meses"},
]

ml = Meli(verbose=False)
uid = ml.get("/users/me")["id"]

ids, scroll = [], None
while True:
    p = {"search_type": "scan", "limit": 100}
    if scroll:
        p["scroll_id"] = scroll
    r = ml.get(f"/users/{uid}/items/search", **p)
    b = r.get("results") or []
    if not b:
        break
    ids += b
    scroll = r.get("scroll_id")
    if not scroll:
        break

todo = {}
for i in range(0, len(ids), 20):
    for w in ml.get("/items", ids=",".join(ids[i:i + 20])):
        d = w.get("body") or {}
        todo[d["id"]] = d

por_sku = {}
for i, d in todo.items():
    s = (sku_del_atributo(d) or d.get("seller_custom_field") or "").strip().lower()
    if s and not PACK.match(s) and (d.get("date_created") or "")[:10] < DESDE:
        por_sku.setdefault(s, []).append(d)

nuevas = {i: d for i, d in todo.items()
          if (d.get("date_created") or "")[:10] >= DESDE}
print(f"creadas desde {DESDE}: {len(nuevas)}"
      f" · editables: {sum(1 for d in nuevas.values() if d.get('status') in EDITABLES)}")
print("MODO:", "ESCRIBIENDO" if HACERLO else "simulacro")

n_attr = n_fotos = n_desc = n_gar = 0
tot_attr = tot_fotos = 0
errores, bloqueados = [], Counter()
hechas = []

for i, d in sorted(nuevas.items()):
    if d.get("status") not in EDITABLES:
        continue
    sku = (sku_del_atributo(d) or d.get("seller_custom_field") or "").strip()
    m = PACK.match(sku)
    suelto = (por_sku.get(m.group("b").strip().lower()) or [None])[0] if m else None

    cambios = []
    # --- 0. Garantía, si le falta
    st_ahora = {x.get("id") for x in (d.get("sale_terms") or [])
                if x.get("value_name") or x.get("value_id")}
    if "WARRANTY_TYPE" not in st_ahora or "WARRANTY_TIME" not in st_ahora:
        if HACERLO:
            try:
                ml.put(f"/items/{i}", {"sale_terms": GARANTIA})
                n_gar += 1
                cambios.append("garantía")
            except MeliError as e:
                errores.append((i, "garantía", str(e)[:110]))
        else:
            n_gar += 1
            cambios.append("garantía")

    # --- 1. Atributos del producto que el suelto ya tiene
    if suelto:
        tengo = {a["id"] for a in (d.get("attributes") or [])
                 if a.get("value_name") or a.get("value_id")}
        nuevos = []
        for a in (suelto.get("attributes") or []):
            if a["id"] in tengo:
                continue
            if a["id"] in PROHIBIDOS or a["id"].startswith("PACKAGE_"):
                bloqueados[a["id"]] += 1
                continue
            if not (a.get("value_name") or a.get("value_id")):
                continue
            x = {"id": a["id"]}
            if a.get("value_id") is not None:
                x["value_id"] = a["value_id"]
            if a.get("value_name"):
                x["value_name"] = a["value_name"]
            nuevos.append(x)
        if nuevos:
            if HACERLO:
                try:
                    ml.put(f"/items/{i}", {"attributes": nuevos})
                    n_attr += 1
                    tot_attr += len(nuevos)
                    cambios.append(f"+{len(nuevos)} atributos")
                except MeliError as e:
                    errores.append((i, "atributos", str(e)[:110]))
            else:
                n_attr += 1
                tot_attr += len(nuevos)
                cambios.append(f"+{len(nuevos)} atributos")

    # --- 2. Fotos que el suelto tiene y esta no
    if suelto:
        mias = {p.get("id") for p in (d.get("pictures") or [])}
        faltan = [p for p in (suelto.get("pictures") or [])
                  if p.get("id") not in mias]
        if faltan and len(mias) < 10:
            cuantas = min(len(faltan), 10 - len(mias))
            todas = [{"id": p["id"]} for p in (d.get("pictures") or [])] + \
                    [{"id": p["id"]} for p in faltan[:cuantas]]
            if HACERLO:
                try:
                    ml.put(f"/items/{i}", {"pictures": todas})
                    n_fotos += 1
                    tot_fotos += cuantas
                    cambios.append(f"+{cuantas} fotos")
                except MeliError as e:
                    errores.append((i, "fotos", str(e)[:110]))
            else:
                n_fotos += 1
                tot_fotos += cuantas
                cambios.append(f"+{cuantas} fotos")

    # --- 3. Descripcion, si le falta
    try:
        actual = (ml.get(f"/items/{i}/description").get("plain_text") or "").strip()
    except MeliError:
        actual = ""
    if not actual and suelto:
        try:
            texto = (ml.get(f"/items/{suelto['id']}/description")
                     .get("plain_text") or "").strip()
        except MeliError:
            texto = ""
        if texto:
            cab = (f"Pack por {m.group('u')} unidades.\n\n") if m else ""
            if HACERLO:
                try:
                    ml.post(f"/items/{i}/description",
                            payload={"plain_text": cab + texto})
                    n_desc += 1
                    cambios.append("descripción")
                except MeliError as e:
                    errores.append((i, "descripción", str(e)[:110]))
            else:
                n_desc += 1
                cambios.append("descripción")

    if cambios:
        hechas.append({"id": i, "titulo": (d.get("title") or "")[:48],
                       "cambios": ", ".join(cambios)})
        print(f"  {i} {(d.get('title') or '')[:44]:<44} {', '.join(cambios)}",
              flush=True)
        if HACERLO:
            time.sleep(0.3)

print(f"\n{'HECHO' if HACERLO else 'SE HARÍA'}:")
print(f"  garantía:    {n_gar} publicaciones")
print(f"  atributos:   {n_attr} publicaciones, {tot_attr} atributos")
print(f"  fotos:       {n_fotos} publicaciones, {tot_fotos} fotos")
print(f"  descripción: {n_desc} publicaciones")
print(f"  errores:     {len(errores)}")
for e in errores[:10]:
    print("   ", e[0], e[1], "—", e[2])

print("\n  atributos NO copiados a propósito (son del bulto, no del producto):")
for a, n in bloqueados.most_common(8):
    print(f"    {a:<26} {n}")

if hechas:
    with open("calidad_mejorada.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "titulo", "cambios"])
        w.writeheader()
        w.writerows(hechas)
    print("\n→ calidad_mejorada.csv")
print("TERMINÓ")
