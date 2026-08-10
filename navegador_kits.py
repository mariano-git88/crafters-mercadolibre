#!/usr/bin/env python3
"""
Crear kits manejando un navegador de verdad.

    python navegador_kits.py            -> simula (no crea nada)
    python navegador_kits.py --hacerlo 5 -> crea 5

**Por que no se puede por pedidos HTTP.** MercadoLibre corre deteccion de
bots en el asistente de kits: en una sesion real hay ~20 llamadas a
`/browser-assessment/s/r` y `/v1/device_sessions/web_device` intercaladas
entre los pasos. Un cliente que manda los mismos cuerpos y las mismas
cabeceras igual recibe `CONTENT` donde el navegador recibe `REDIRECT`: el
asistente no avanza y las sugerencias de IA contestan `REJECT`. No es un
parametro que falte — es a proposito.

Con Playwright el navegador es real, corre ese JavaScript y la sesion es
legitima. Mas lento (20-30 s por kit contra 3) y mas fragil (depende de la
pantalla, no de endpoints), pero es lo unico que funciona.

**La sesion entra por la cookie `ssid`**, la misma de `panel_ads`.

Requisitos, una sola vez:

    pip install playwright
    python3 -m playwright install chromium
    sudo python3 -m playwright install-deps chromium
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import panel_ads

DIR = Path(__file__).resolve().parent
PANEL = "https://vendedores.mercadolibre.com.ar"
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
ASISTENTE = PANEL + "/publicar/kit"

# Cuanto esperar cada pantalla. El asistente es lento y va contra la
# deteccion de bots: apurarlo con esperas cortas hace que falle por timeout
# en vez de por un problema real.
ESPERA = 30_000


SESION = DIR / "sesion_kits.json"


def _sesion_en(contexto):
    """
    Le pasa la sesion al navegador.

    **Con `ssid` sola no alcanza.** Por HTTP si —el asistente responde con su
    token—, pero en el navegador el JavaScript de ML revalida y redirige al
    login. Hacen falta las cookies completas de una sesion real: se sacan del
    navegador con *Copy as cURL* y se guardan en `sesion_kits.json`
    (ignorado por git).

    Es la sesion entera, mas amplia que el `ssid`: si se filtra, se invalida
    cambiando la contraseña de MercadoLibre.
    """
    if not SESION.exists():
        raise RuntimeError(
            f"Falta {SESION.name}. Sacá las cookies del navegador con "
            f"F12 → Network → click derecho en un pedido → Copy as cURL.")
    contexto.add_cookies(
        json.loads(SESION.read_text(encoding="utf-8"))["cookies"])


# El control de cantidad de ML es un `andes-input-stepper`: dentro del
# contenedor, el primer boton resta y el segundo suma.
MAS = ".andes-input-stepper__container button:nth-of-type(2)"


BUSCADOR = "input[placeholder*='Buscar por título']"

# Cada fila del buscador trae los IDs de publicacion como `#1139087851`. Es
# lo unico con lo que se distingue el producto correcto entre los resultados.
FILAS_JS = """() => {
  const bs=[...document.querySelectorAll('button')].filter(
      b=>b.innerText.includes('Agregar al kit'));
  return bs.map(b=>{ let e=b;
    for(let i=0;i<8;i++){ e=e.parentElement; if(!e) break;
      if(e.innerText.length>60) return e.innerText; }
    return ''; });
}"""


def _busquedas(titulo):
    """
    Con que buscar un producto, de lo mas especifico a lo mas general.

    **El buscador del asistente no encuentra por MLAU ni por SKU**: los dos
    dan cero resultados, probado. Solo va por texto del titulo. Y una sola
    palabra no siempre alcanza —la lista viene acotada— asi que se prueban
    varias combinaciones antes de darse por vencido.
    """
    limpio = " ".join((titulo or "").split())
    palabras = [w.strip(".,-()") for w in limpio.split()
                if len(w) > 3 and not w.isdigit()]
    intentos = []
    if len(palabras) >= 3:
        intentos.append(" ".join(palabras[:3]))
    if len(palabras) >= 2:
        intentos.append(" ".join(palabras[:2]))
    intentos += sorted(palabras, key=len, reverse=True)[:3]
    vistos, salida = set(), []
    for x in intentos:
        if x and x.lower() not in vistos:
            vistos.add(x.lower())
            salida.append(x)
    return salida[:5]


def crear_kit(pagina, productos, precio=None, callback=None):
    """
    Arma un kit en el asistente y lo publica. Devuelve (ok, detalle).

    `productos` es [(MLAU, item_id, titulo, cantidad), ...]; el primero es el
    principal y va precargado por URL.
    """
    def paso(m):
        if callback:
            callback(m)

    principal = productos[0]
    pagina.goto(f"{ASISTENTE}?pre_charged_ups={principal[0]}",
                wait_until="networkidle", timeout=ESPERA * 4)
    pagina.wait_for_timeout(5000)

    # --- Paso 1: los acompañantes, buscando por titulo y eligiendo por ID.
    for up, item, titulo, _ in productos[1:]:
        marca = "#" + str(item).replace("MLA", "")
        clave = " ".join((titulo or "").split()[:5]).lower()
        cual, usada = None, ""
        for q in _busquedas(titulo):
            paso(f"buscando «{q}»")
            pagina.fill(BUSCADOR, q)
            pagina.keyboard.press("Enter")
            pagina.wait_for_timeout(5000)
            filas = pagina.evaluate(FILAS_JS)
            # Primero por ID de publicacion, que es exacto; si no aparece, por
            # titulo, que sirve cuando el producto tiene otras publicaciones.
            cual = next((n for n, t in enumerate(filas) if marca in t), None)
            if cual is None:
                cual = next((n for n, t in enumerate(filas)
                             if clave and clave in " ".join(t.split()).lower()),
                            None)
            if cual is not None:
                usada = q
                break
        if cual is None:
            return False, (f"no encontré {item} en el buscador del asistente "
                           f"(probé: {', '.join(_busquedas(titulo))})")
        paso(f"agregando {item} (encontrado con «{usada}»)")
        pagina.locator("button:has-text('Agregar al kit')").nth(cual).click(
            timeout=ESPERA)
        pagina.wait_for_timeout(2500)

    # Cantidades: el multipack es el mismo producto con cantidad > 1.
    for n, (_, _, _, cuantos) in enumerate(productos):
        for _ in range(int(cuantos) - 1):
            pagina.locator(MAS).nth(n).click(timeout=ESPERA)
            pagina.wait_for_timeout(600)

    paso("pasando al paso 2")
    pagina.click("text=Ir al siguiente paso", timeout=ESPERA)
    pagina.wait_for_timeout(10000)
    if "kit_detail" not in pagina.url and "sales_" not in pagina.url:
        return False, "no salió del paso 1 (¿el kit ya existe?)"

    # --- Paso 2: titulo, foto y descripcion los genera ML solo.
    paso("esperando las sugerencias de MercadoLibre")
    pagina.wait_for_timeout(9000)
    pagina.click("text=Ir al siguiente paso", timeout=ESPERA)
    pagina.wait_for_timeout(8000)

    # --- Paso 3: precio y publicar.
    if precio:
        try:
            pagina.fill("input[inputmode='decimal'], input[name*='price']",
                        str(int(precio)), timeout=12000)
            pagina.wait_for_timeout(2000)
        except Exception:                          # noqa: BLE001
            paso("no pude fijar el precio; queda el que calculó ML")

    paso("publicando")
    for texto in ("Publicar", "Crear kit", "Finalizar"):
        try:
            pagina.click(f"button:has-text('{texto}')", timeout=8000)
            break
        except Exception:                          # noqa: BLE001
            continue
    pagina.wait_for_timeout(8000)

    cuerpo = pagina.inner_text("body")[:4000].lower()
    if any(k in cuerpo for k in ("listo", "publicad", "felicit", "tu kit ya")):
        return True, "creado"
    for pista in ("no pudimos", "código universal", "revisá", "error"):
        if pista in cuerpo:
            i = cuerpo.find(pista)
            return False, cuerpo[max(0, i - 50):i + 150].replace("\n", " ")
    return False, "no pude confirmar que se publicara"


def crear_lote(plan, operador="", hacerlo=False, tope=None, callback=None):
    """
    Arma los kits de un plan. Una falla no corta el lote y cada resultado
    queda registrado, asi que se puede retomar sin repetir.
    """
    from playwright.sync_api import sync_playwright

    import kits as kits_mod

    filas = plan.head(tope) if tope else plan
    salida = []
    if not hacerlo:
        return [{"detalle": "simulacro", "producto": f.get("producto")
                 or f.get("detalle")} for _, f in filas.iterrows()]

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        ctx = navegador.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="es-AR", user_agent=NAVEGADOR)
        _sesion_en(ctx)
        pagina = ctx.new_page()

        for _, f in filas.iterrows():
            nombre = str(f.get("producto") or f.get("detalle"))[:44]
            try:
                if "unidades" in f:      # multipack: un producto, N unidades
                    ups = [(f["user_product"], f["item"], f["producto"],
                            int(f["unidades"]))]
                else:
                    ups = [(u.strip(), i.strip(), t.strip(), 1) for u, i, t in
                           zip(str(f["user_product"]).split(","),
                               str(f["items"]).split(","),
                               str(f["detalle"]).split(" + "))]
                ok, det = crear_kit(pagina, ups,
                                    precio=f.get("precio_kit_sugerido"),
                                    callback=callback)
            except Exception as e:                 # noqa: BLE001
                ok, det = False, f"{type(e).__name__}: {str(e)[:160]}"
            salida.append({"producto": nombre, "ok": ok, "detalle": det})
            if callback:
                callback(f"{'✓' if ok else '✗'} {nombre} — {det}")
            kits_mod.registrar(
                [{**f.to_dict(), "veredicto": "creado" if ok else "falló",
                  "motivo": det}],
                operador=operador, estado="armado" if ok else "error")
            time.sleep(2)
        navegador.close()
    return salida


def main():
    import pandas as pd

    hacerlo = "--hacerlo" in sys.argv
    cuantos = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3)
    ruta = DIR / "multipacks.csv"
    if not ruta.exists():
        print("Falta multipacks.csv: corré primero la sección KITS.")
        return 1
    plan = pd.read_csv(ruta)
    plan = plan[plan["ahorro_de"] == "cargo fijo"].head(cuantos)

    if not hacerlo:
        print(f"SIMULACRO — {len(plan)} multipacks\n")
        for _, f in plan.iterrows():
            print(f"  {f['unidades']}× {str(f['producto'])[:52]} → "
                  f"${f['precio_kit_sugerido']:,.0f}".replace(",", "."))
        print("\n(agregá --hacerlo para crearlos)")
        return 0

    for r in crear_lote(plan, operador="cli", hacerlo=True,
                        callback=lambda m: print("  " + m)):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
