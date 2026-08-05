#!/usr/bin/env python3
"""
Lleva las reseñas de MercadoLibre a la tienda Shopify, via Parlata.

    python reviews_shopify.py            -> dice que traeria, no manda nada
    python reviews_shopify.py --aplicar  -> las importa

Suprabond vende el mismo catalogo en MercadoLibre y en Shopify. En ML hay
años de reseñas acumuladas; la tienda Shopify arranca de cero.

**QUE SON ESTAS RESEÑAS Y QUE NO SON.** MercadoLibre agrupa las reseñas **por
producto de catalogo, no por publicacion**, y una pagina de catalogo la
comparten todos los vendedores de ese producto. Medido sobre un SKU: la
publicacion de catalogo tenia 8.920 reseñas y una hermana 2.891, con 50% de
solapamiento. O sea que buena parte de lo que entra lo escribio gente que le
compro el mismo producto a un competidor.

Es una decision tomada a conciencia por Mariano, no un descuido. De ahi salen
dos cosas que no hay que aflojar:

  - cada reseña entra con `source: "mercadolibre"`, para que el widget pueda
    decir de donde vino;
  - no llevan el badge de "voz verificada" — Parlata las marca
    `NOT_APPLICABLE` porque no hay audio ni una orden nuestra que las respalde.

Solo se importan las de **3 estrellas o mas**.

**Por que se pregunta primero que SKU tiene la tienda.** El endpoint de
reseñas de ML **ignora `sort` y `filter`** (probado con cinco variantes: los
acepta y devuelve lo mismo), asi que no hay forma de pedir "las nuevas" y hay
que paginar todo. Traer solo los SKU que existen en Shopify convierte horas de
paginado en minutos.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import almacen
from catalogo import sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

ESTRELLAS_MINIMAS = 3
POR_PAGINA = 50
# El endpoint de import de Parlata corta arriba de esto.
LOTE_ENVIO = 2000
# Tope de paginas por publicacion. Las mas vendidas tienen miles de reseñas y
# las de mas abajo aportan cada vez menos: sin un tope, una sola publicacion
# se come la corrida entera.
PAGINAS_MAX = 40


def config():
    """API key y URL de Parlata, desde los secrets."""
    cfg = almacen._seccion("parlata")
    faltan = [k for k in ("api_key", "base_url") if not cfg.get(k)]
    if faltan:
        raise SystemExit(
            "Faltan en los secrets, bajo [parlata]: " + ", ".join(faltan) +
            '\n\n[parlata]\napi_key = "parlata_..."\n'
            'base_url = "https://parlata.fly.dev"')
    return cfg


def skus_de_la_tienda(cfg):
    """Los SKU que tiene el catalogo de Shopify."""
    r = requests.get(f"{cfg['base_url'].rstrip('/')}/api/v1/skus",
                     headers={"Authorization": f"Bearer {cfg['api_key']}"},
                     timeout=120)
    if r.status_code == 403:
        raise SystemExit("La API de Parlata pide plan Growth para esta tienda.")
    if r.status_code >= 400:
        raise SystemExit(f"No pude leer los SKU: HTTP {r.status_code} "
                         f"{r.text[:200]}")
    return {s.strip() for s in (r.json().get("skus") or []) if s and s.strip()}


def publicaciones_por_sku(pubs, skus):
    """Solo las publicaciones activas cuyo SKU esta en la tienda."""
    salida = {}
    for p in pubs:
        if p.get("status") != "active":
            continue
        s = (sku_del_atributo(p) or p.get("seller_custom_field") or "").strip()
        if s and s in skus:
            salida.setdefault(s, []).append(p["id"])
    return salida


def traer_resenas(ml, item_id, callback=None):
    """
    Todas las reseñas de una publicacion. Devuelve dict id -> reseña.

    Se devuelve indexado por id porque **las publicaciones espejo comparten
    el pozo de reseñas del producto**: sin deduplicar, el mismo texto entra
    tantas veces como publicaciones tenga el SKU.
    """
    salida, offset = {}, 0
    for pagina in range(PAGINAS_MAX):
        try:
            r = ml.get(f"/reviews/item/{item_id}", limit=POR_PAGINA,
                       offset=offset)
        except MeliError:
            break
        filas = r.get("reviews") or []
        if not filas:
            break
        for x in filas:
            salida[x["id"]] = x
        total = (r.get("paging") or {}).get("total", 0)
        offset += POR_PAGINA
        if offset >= total:
            break
        if callback and pagina and pagina % 10 == 0:
            callback(f"    {item_id}: {len(salida)} de {total}...")
    return salida


def juntar(ml, por_sku, callback=None):
    """Reseñas utiles por SKU, ya deduplicadas y filtradas por estrellas."""
    filas, vistas = [], set()
    for i, (sku, items) in enumerate(por_sku.items(), start=1):
        if callback:
            callback(f"  {i}/{len(por_sku)} {sku}")
        for item_id in items:
            for rid, x in traer_resenas(ml, item_id, callback).items():
                if rid in vistas:
                    continue
                vistas.add(rid)
                if (x.get("rate") or 0) < ESTRELLAS_MINIMAS:
                    continue
                texto = (x.get("content") or "").strip()
                if not texto:
                    continue
                filas.append({
                    "externalId": rid,
                    "sku": sku,
                    "stars": x.get("rate"),
                    "title": (x.get("title") or "").strip() or None,
                    "text": texto,
                    "createdAt": x.get("date_created"),
                })
    return filas


def enviar(cfg, filas, publicar=True, callback=None):
    """Manda a Parlata en lotes. Devuelve el acumulado de resultados."""
    url = f"{cfg['base_url'].rstrip('/')}/api/v1/reviews/import"
    total = {"imported": 0, "skipped": 0, "skusMissing": set(), "errors": []}
    for i in range(0, len(filas), LOTE_ENVIO):
        lote = filas[i:i + LOTE_ENVIO]
        if callback:
            callback(f"  enviando {i + len(lote)}/{len(filas)}...")
        try:
            r = requests.post(
                url, headers={"Authorization": f"Bearer {cfg['api_key']}",
                              "Content-Type": "application/json"},
                json={"source": "mercadolibre", "publish": publicar,
                      "reviews": lote}, timeout=300)
            j = r.json() if r.content else {}
        except Exception as e:
            total["errors"].append(f"{type(e).__name__}: {str(e)[:150]}")
            continue
        if r.status_code >= 400:
            total["errors"].append(f"HTTP {r.status_code}: {r.text[:200]}")
            continue
        total["imported"] += j.get("imported", 0)
        total["skipped"] += j.get("skipped", 0)
        total["skusMissing"] |= set(j.get("skusMissing") or [])
        total["errors"] += j.get("errors") or []
        time.sleep(1)
    total["skusMissing"] = sorted(total["skusMissing"])
    return total


def correr(aplicar=False, log=None, ml=None):
    log = log or (lambda m: print(m, flush=True))
    cfg = config()
    ml = ml or Meli(verbose=False)

    log("Preguntando a la tienda qué SKU tiene...")
    skus = skus_de_la_tienda(cfg)
    log(f"  {len(skus)} SKU en Shopify")

    pubs = json.loads((DIR / "catalogo.json").read_text(encoding="utf-8"))
    por_sku = publicaciones_por_sku(pubs, skus)
    publicaciones = sum(len(v) for v in por_sku.values())
    log(f"  {len(por_sku)} de esos SKU están publicados en MercadoLibre "
        f"({publicaciones} publicaciones)")
    if not por_sku:
        log("Nada para traer: ningún SKU coincide entre las dos tiendas.")
        return 0

    log("\nTrayendo reseñas (se paginan todas: ML no deja pedir las nuevas)...")
    filas = juntar(ml, por_sku, callback=log)
    log(f"\n{len(filas)} reseñas de {ESTRELLAS_MINIMAS} estrellas o más, "
        "ya sin repetidas entre publicaciones espejo")
    if filas:
        from collections import Counter
        c = Counter(f["stars"] for f in filas)
        log("  por estrellas: " + ", ".join(
            f"{k}★ {c[k]}" for k in sorted(c, reverse=True)))

    if not aplicar:
        log("\n(simulación: corré con --aplicar para importarlas)")
        return 0

    log("\nEnviando a Parlata...")
    res = enviar(cfg, filas, publicar=True, callback=log)
    log(f"\nImportadas {res['imported']} · salteadas {res['skipped']}")
    if res["skusMissing"]:
        log(f"SKU que Shopify no pudo resolver: {len(res['skusMissing'])} "
            f"(ej: {', '.join(res['skusMissing'][:5])})")
    for e in res["errors"][:5]:
        log(f"  ERROR: {e}")
    return 0


def main():
    return correr(aplicar="--aplicar" in sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
