#!/usr/bin/env python3
"""
Crear kits en MercadoLibre, por el panel de vendedores.

    python panel_kits.py                 -> simula los primeros multipacks
    python panel_kits.py --hacerlo 5     -> crea 5 de verdad

**La API no crea kits.** El panel los arma con un asistente de tres pasos que
va todo por un mismo endpoint, `PUT /publicar/kit/api/event-request`, mandando
un evento por paso.

Autentica con la cookie `ssid` (la misma de `panel_ads`) mas un
`x-csrf-token`, y ademas hace falta un **`session_id`** —del estilo
`422682314-list_kit-aeeb81f6762b`— que se crea al abrir el asistente. Los tres
salen del mismo GET inicial.

--------------------------------------------------------------------------
Lo que se aprendio de las capturas
--------------------------------------------------------------------------

**Los multipacks son nativos.** Cada producto lleva `stock.quantity`, con
`maxLimit: 10`. Un pack de 4 del mismo producto es el mismo flujo con la
cantidad cambiada: no hay que inventar nada.

**Todo va por `MLAU` (user product)**, no por el `MLA` de la publicacion.

**ML valida la categoria del kit.** Un intento manual de Mariano fallo con
**422**: *"El codigo universal 0300701403 pertenece a un producto de otra
categoria"*. Medido sobre las propuestas: 94 de 285 kits cruzan categorias y
tienen ese riesgo; los multipacks, ninguno.

**No se construye el listado de productos a mano.** El paso de agregar
devuelve los bricks con los productos ya armados (titulo, foto, limites de
stock); se toman de ahi y se devuelven con la cantidad puesta. Inventar ese
JSON es adivinar campos que el servidor ya nos da.
"""

import json
import re
import sys
import time
from datetime import datetime

import requests

import panel_ads
from meli import MeliError

PANEL = "https://vendedores.mercadolibre.com.ar"
ASISTENTE = PANEL + "/publicar/kit?pre_charged_ups={up}"
EVENTO = PANEL + "/publicar/kit/api/event-request"

NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# ML no deja mas de esto por producto dentro de un kit.
MAX_UNIDADES = 10

PAUSA = 1.0


def contexto(mlau, sesion=None):
    """
    Abre el asistente y devuelve lo necesario para operar:
    `csrf`, `session_id` y las cookies.

    **Cada kit necesita su propia sesion**: el `session_id` identifica un
    armado en curso, no al usuario.
    """
    s = sesion or panel_ads.leer_sesion()
    url = ASISTENTE.format(up=mlau) if mlau else PANEL + "/publicar/kit"
    r = requests.get(url,
                     headers={"User-Agent": NAVEGADOR,
                              "Accept": "text/html,application/xhtml+xml",
                              "Accept-Language": "es-AR,es;q=0.9"},
                     cookies={"ssid": s["ssid"]}, timeout=90)
    if r.status_code != 200:
        raise MeliError(f"el asistente contestó {r.status_code}")
    if "/login" in r.url:
        raise MeliError("la sesión venció: volvé a copiar el ssid")

    tok = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', r.text)
    ses = re.findall(r"\d+-list_kit-[0-9a-f]+", r.text)
    if not tok or not ses:
        raise MeliError("no encontré csrf o session_id en el asistente")
    galletas = {"ssid": s["ssid"]}
    galletas.update(dict(r.cookies))
    return {"csrf": tok.group(1), "session": ses[0], "cookies": galletas,
            "mlau": mlau, "productos": _productos_de_la_pagina(r.text)}


def _productos_de_la_pagina(html):
    """
    Los productos que el asistente ya trae cargados.

    El principal viene **precargado** por `pre_charged_ups`: volver a
    agregarlo lo duplica (se ve en que el paso devuelve dos). El estado
    inicial esta embebido en la pagina, asi que se lee de ahi.
    """
    i = html.find('"products":[')
    if i < 0:
        return []
    j = i + len('"products":')
    prof, fin = 0, None
    for k in range(j, min(len(html), j + 200000)):
        if html[k] == "[":
            prof += 1
        elif html[k] == "]":
            prof -= 1
            if prof == 0:
                fin = k + 1
                break
    if not fin:
        return []
    try:
        return json.loads(html[j:fin])
    except json.JSONDecodeError:
        return []


# **ML mira las pistas del cliente.** Con DevTools acoplado el navegador queda
# angosto, ML lo lee como pantalla chica y corta el asistente — Mariano lo
# sufrio al capturar. Un cliente que no manda `viewport-width` cae en el mismo
# lugar. Y el navegador manda `x-requested-with` en TODOS los eventos, no solo
# al crear.
PISTAS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9",
    "Content-Type": "application/json",
    "device-memory": "8",
    "downlink": "10",
    "dpr": "1",
    "ect": "4g",
    "rtt": "50",
    "viewport-width": "1920",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="140", '
                 '"Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
    "x-requested-with": "XMLHttpRequest",
}


def _cabeceras(ctx):
    h = dict(PISTAS)
    h.update({"User-Agent": NAVEGADOR, "Origin": PANEL,
              "Referer": PANEL + "/publicar/kit",
              "x-csrf-token": ctx["csrf"]})
    return h


def abrir_paso(ctx, paso):
    """
    Carga la **pagina** de un paso del asistente.

    Hace falta de verdad: el asistente es con estado del lado del servidor y
    los bricks de un paso no existen hasta que su pagina se pidio. Sin esto,
    pedir la foto sugerida contesta **500 `No value present`** (una
    `NoSuchElementException` de Java, o sea que ML busca algo que todavia no
    creo).
    """
    r = requests.get(
        f"https://www.mercadolibre.com.ar/publicar/kit/{ctx['session']}/{paso}",
        headers={"User-Agent": NAVEGADOR, "Accept": "text/html"},
        cookies=ctx["cookies"], timeout=90)
    return r.status_code, r.text


def _evento(ctx, metodo, paso, cuerpo=None, extra=None, confirmar=False):
    """Un paso del asistente. Devuelve la respuesta ya parseada."""
    cuerpo_ev = ({"output": {"value": cuerpo}} if cuerpo is not None
                 else {"output": {}})
    if confirmar:
        cuerpo_ev["confirmed"] = True
        cuerpo_ev["applyToAll"] = False
        cuerpo_ev["output"]["confirmed"] = True
        cuerpo_ev["output"]["applyToAll"] = False
    payload = {
        "method": metodo,
        "path": f"list-kits/{ctx['session']}/{paso}",
        "loadingEvents": [], "errorEvents": [], "queryParams": {},
        "pathParams": [], "bodyParams": [],
        "headers": dict(extra or {}),
        "body": cuerpo_ev,
    }
    r = requests.put(EVENTO, headers=_cabeceras(ctx),
                     cookies=ctx["cookies"], json=payload, timeout=120)
    try:
        datos = r.json()
    except ValueError:
        datos = {}
    return r.status_code, datos


def _mensaje_de_error(datos):
    """El texto que ML muestra en el cartel rojo, si lo hay."""
    for ev in (datos.get("events") or []):
        d = (ev.get("data") or {}).get("data") or {}
        if d.get("type") == "ERROR" and d.get("message"):
            return d["message"]
    return ""


def _foto_sugerida(datos):
    """
    La foto de portada que ML propone con IA para el kit.

    **No hay que generar ni subir nada**: el asistente la arma solo a partir
    de los productos. Se pide con valor vacio, viene en el brick del
    `picture_uploader`, y despues se confirma.
    """
    def buscar(o):
        if isinstance(o, dict):
            if o.get("secureUrl") or (o.get("url") and o.get("id")
                                      and "mlstatic" in str(o.get("url"))):
                return o
            for v in o.values():
                r = buscar(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = buscar(v)
                if r:
                    return r
        return None
    return buscar(datos)


def _productos_de(datos):
    """Los productos como los devuelve el servidor tras agregarlos."""
    for ev in (datos.get("events") or []):
        for b in ((ev.get("data") or {}).get("bricks") or []):
            if b.get("id") == "products_manager":
                return ((b.get("data") or {}).get("products")
                        or (b.get("data") or {}).get("items") or [])
    return []


def crear_kit(productos, precio=None, tienda=None, tipo=None, sesion=None,
              callback=None, titulo=None):
    """
    Arma un kit y lo publica. Devuelve (ok, detalle).

    `productos` es una lista de (MLAU, cantidad). El primero queda como
    principal.

    El flujo sale de una captura completa de punta a punta (kits 10), que es
    la unica que sirvio: las anteriores eran pedazos de sesiones distintas
    porque **con DevTools acoplado el navegador queda angosto, ML lo lee como
    mobile y corta el asistente**. Undocked se puede capturar entero.

    Tres cosas que no se adivinan:

    - **El asistente se abre SIN producto precargado** y se agregan todos por
      evento. Abrirlo con `pre_charged_ups` deja el principal cargado y
      agregarlo otra vez lo duplica.
    - **Confirmar los productos va dos veces**, con el mismo cuerpo.
    - **El paso 2 dispara TRES sugerencias de IA** —titulo, foto y
      descripcion— con valor vacio. No hay que confirmarlas ni devolver nada:
      alcanza con pedirlas. Sin las tres, `next_form` no avanza.

    `precio`, `tienda` y `tipo` son opcionales: si no se pasan, quedan los que
    ML calcula a partir de los componentes.
    """
    ctx = contexto(None, sesion)
    if callback:
        callback(f"sesión {ctx['session']}")

    # 1) Agregar todos los productos.
    d = {}
    for up, _ in productos:
        cod, d = _evento(ctx, "PATCH", "search_form/search-add-product",
                         {"product_added": up, "offset": 0,
                          "filters": ["ONLY_ELIGIBLE"]})
        if cod >= 400:
            return False, f"al agregar {up}: {_mensaje_de_error(d) or cod}"

    lista = _productos_de(d)
    if not lista:
        return False, "el asistente no devolvió la lista de productos"

    # Las cantidades: el multipack es el mismo producto con quantity > 1.
    cantidades = {up: n for up, n in productos}
    for x in lista:
        n = cantidades.get(x.get("id"))
        if n:
            x.setdefault("stock", {})
            x["stock"]["quantity"] = min(int(n), MAX_UNIDADES)

    # 2) Confirmar, dos veces con el mismo cuerpo.
    for _ in range(2):
        cod, d = _evento(ctx, "PATCH", "search_form/products-manager-default",
                         {"products": lista})
        if cod >= 400:
            return False, f"al confirmar: {_mensaje_de_error(d) or cod}"
        time.sleep(0.4)

    # 3) Paso 2. El titulo se puede mandar directo —`title-default` con un
    #    string contesta 200 y el foco pasa de `title_task` a
    #    `picture_uploader_task`, o sea que lo toma—. La **portada** no: solo
    #    se puede poner una imagen que ML haya generado para esa sesion, y la
    #    sugerencia de IA contesta REJECT desde este cliente.
    abrir_paso(ctx, "kit_detail_form")
    if titulo:
        _evento(ctx, "PATCH", "kit_detail_form/title-default", titulo)
    for cual in ("title", "picture-uploader", "description"):
        cod, d = _evento(
            ctx, "PATCH", f"kit_detail_form/ai-suggestions-{cual}-default", "")
        if cod >= 400:
            return False, f"al pedir la sugerencia de {cual}: {cod}"
        time.sleep(0.6)

    cod, d = _evento(ctx, "GET", "kit_detail_form/next_form")
    if cod >= 400:
        return False, f"no avanzó del paso 2: {_mensaje_de_error(d) or cod}"

    # 4) Condiciones de venta. Solo se toca lo que se quiera cambiar.
    abrir_paso(ctx, "sales_condition_form")
    pasos = []
    if tienda:
        pasos.append(("sales_condition_form/official-store-default",
                      str(tienda)))
    if tipo:
        pasos.append(("sales_condition_form/listing-fees-default",
                      {"listingType": {"selected": tipo,
                                       "channels": [{"channel": "ml",
                                                     "listingType": tipo,
                                                     "campaign": "no-campaign"}]}}))
    if precio:
        pasos.append(("sales_condition_form/price-default",
                      {"marketplace": {"currency": "ARS",
                                       "price": round(float(precio), 2),
                                       "syncronized": True}}))
    for paso, valor in pasos:
        cod, d = _evento(ctx, "PATCH", paso, valor)
        if d.get("result_type") == "CONFIRMATION_REQUIRED":
            cod, d = _evento(ctx, "PATCH", paso, valor, confirmar=True)
        if cod >= 400:
            return False, f"en {paso.split('/')[-1]}: {_mensaje_de_error(d) or cod}"
        time.sleep(0.4)

    # 5) Crear.
    cod, d = _evento(ctx, "POST", "create-item", {},
                     extra={"X-Requested-With": "XMLHttpRequest"})
    if cod >= 400:
        return False, (_mensaje_de_error(d) or f"create-item devolvió {cod}")
    return True, (_mensaje_de_error(d) or "creado")


def crear_multipacks(plan, operador="", callback=None, tope=None,
                     tiendas=None):
    """
    Crea los multipacks de un plan de `kits.multipacks()`.

    **Una falla no corta el lote** y cada resultado se registra, asi que se
    puede retomar sin repetir. Empezar por multipacks es a proposito: son los
    de mayor ahorro medido y **no cruzan categorias**, que es lo unico que ML
    rechaza con 422.
    """
    import kits as kits_mod

    sesion = panel_ads.leer_sesion()
    hechos, salida = 0, []
    for _, f in plan.iterrows():
        if tope and hechos >= tope:
            break
        up = f.get("user_product")
        if not up:
            salida.append({"producto": f.get("producto"), "ok": False,
                           "detalle": "sin user product"})
            continue
        try:
            # **La tienda oficial la decide la publicacion**, no un default:
            # mandar una a la que el producto no pertenece contesta 500.
            tienda = (tiendas or {}).get(f.get("item"))
            ok, det = crear_kit(
                [(up, int(f["unidades"]))],
                precio=f["precio_kit_sugerido"],
                tienda=tienda,
                tipo=f.get("tipo_publicacion", "gold_special"),
                sesion=sesion)
        except Exception as e:                     # noqa: BLE001
            ok, det = False, f"{type(e).__name__}: {str(e)[:180]}"
        hechos += 1 if ok else 0
        salida.append({"producto": f.get("producto"), "unidades": f.get("unidades"),
                       "precio": f.get("precio_kit_sugerido"),
                       "ok": ok, "detalle": det})
        if callback:
            callback(f"{'✓' if ok else '✗'} {str(f.get('producto'))[:44]} — {det}")
        kits_mod.registrar(
            [{**f.to_dict(), "veredicto": "creado" if ok else "falló",
              "motivo": det}],
            operador=operador, estado="armado" if ok else "error")
        time.sleep(PAUSA)
    return salida


def main():
    import kits as kits_mod
    import rentabilidad as rent

    de_verdad = "--hacerlo" in sys.argv
    cuantos = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3)

    hist = json.loads((__import__("pathlib").Path(__file__).resolve().parent
                       / "historico_ventas.json").read_text(encoding="utf-8"))
    envios = json.loads((__import__("pathlib").Path(__file__).resolve().parent
                         / "costos_envio.json").read_text(encoding="utf-8"))
    cargos = rent.cargos_por_sku(hist["ordenes"], envios)
    plan = kits_mod.multipacks(cargos=cargos)
    plan = plan[plan["ahorro_de"] == "cargo fijo"].head(cuantos)
    pubs = json.loads((__import__("pathlib").Path(__file__).resolve().parent
                       / "catalogo.json").read_text(encoding="utf-8"))
    tiendas = {x["id"]: x.get("official_store_id") for x in pubs}

    if not de_verdad:
        print(f"SIMULACRO — los primeros {len(plan)} multipacks\n")
        for _, f in plan.iterrows():
            print(f"  {f['unidades']}× {f['producto'][:52]}")
            print(f"     ${f['precio_kit_sugerido']:,.0f} ({f['descuento']:.0%} off) "
                  f"· {f['tipo_publicacion']} · {f['marca']}".replace(",", "."))
        print("\n(agregá --hacerlo para crearlos de verdad)")
        return 0

    print(f"CREANDO {len(plan)} multipacks — es de verdad\n")
    for r in crear_multipacks(plan, operador="cli", tiendas=tiendas,
                              callback=lambda m: print("  " + m)):
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeliError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
