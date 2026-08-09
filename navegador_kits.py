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
ASISTENTE = PANEL + "/publicar/kit"

# Cuanto esperar cada pantalla. El asistente es lento y va contra la
# deteccion de bots: apurarlo con esperas cortas hace que falle por timeout
# en vez de por un problema real.
ESPERA = 30_000


def _sesion_en(contexto):
    """Le pone la cookie de sesion al navegador."""
    ssid = panel_ads.leer_sesion()["ssid"]
    contexto.add_cookies([{
        "name": "ssid", "value": ssid,
        "domain": ".mercadolibre.com.ar", "path": "/",
    }])


def crear_kit(pagina, productos, precio=None, titulo=None, callback=None):
    """
    Arma un kit en el asistente. `productos` es [(MLAU, cantidad), ...].

    Se apoya en **textos visibles**, no en clases ni ids: los textos de ML
    cambian menos que su HTML, y cuando cambian el error dice cual falto en
    vez de romperse en silencio.
    """
    def paso(m):
        if callback:
            callback(m)

    pagina.goto(f"{ASISTENTE}?pre_charged_ups={productos[0][0]}",
                wait_until="domcontentloaded", timeout=ESPERA)

    # --- Paso 1: los acompañantes.
    for up, _ in productos[1:]:
        paso(f"agregando {up}")
        pagina.fill("input[placeholder*='Buscar']", up)
        pagina.keyboard.press("Enter")
        pagina.wait_for_timeout(2500)
        pagina.click("text=Agregar al kit", timeout=ESPERA)
        pagina.wait_for_timeout(1500)

    # Cantidades: el multipack es el mismo producto con cantidad > 1.
    for _, cuantos in productos:
        for _ in range(int(cuantos) - 1):
            pagina.click("button[aria-label*='umentar'], "
                         "button:has-text('+')", timeout=ESPERA)
            pagina.wait_for_timeout(400)

    paso("pasando al paso 2")
    pagina.click("text=Ir al siguiente paso", timeout=ESPERA)
    pagina.wait_for_timeout(4000)

    # --- Paso 2: titulo, foto y descripcion los sugiere ML. Solo hay que
    #     dejarlo terminar y seguir.
    if titulo:
        try:
            pagina.fill("input[name*='title'], textarea[name*='title']",
                        titulo[:60], timeout=8000)
        except Exception:                          # noqa: BLE001
            paso("no pude escribir el título; queda el de ML")
    paso("esperando las sugerencias de MercadoLibre")
    pagina.wait_for_timeout(8000)
    pagina.click("text=Ir al siguiente paso", timeout=ESPERA)
    pagina.wait_for_timeout(4000)

    # --- Paso 3: precio y publicar.
    if precio:
        try:
            pagina.fill("input[name*='price'], input[inputmode='decimal']",
                        str(int(precio)), timeout=10000)
            pagina.wait_for_timeout(1500)
        except Exception:                          # noqa: BLE001
            paso("no pude fijar el precio; queda el que calculó ML")

    paso("publicando")
    pagina.click("text=Publicar, text=Crear kit", timeout=ESPERA)
    pagina.wait_for_timeout(6000)

    cuerpo = pagina.inner_text("body")[:4000].lower()
    if "listo" in cuerpo or "publicad" in cuerpo or "felicit" in cuerpo:
        return True, "creado"
    for pista in ("no pudimos", "error", "revisá", "código universal"):
        if pista in cuerpo:
            i = cuerpo.find(pista)
            return False, cuerpo[max(0, i - 60):i + 160].replace("\n", " ")
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
            locale="es-AR")
        _sesion_en(ctx)
        pagina = ctx.new_page()

        for _, f in filas.iterrows():
            nombre = str(f.get("producto") or f.get("detalle"))[:44]
            try:
                ups = ([(f["user_product"], int(f["unidades"]))]
                       if "unidades" in f else
                       [(u, 1) for u in str(f["user_product"]).split(",")])
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
