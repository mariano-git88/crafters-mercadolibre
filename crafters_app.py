#!/usr/bin/env python3
"""
Herramientas de MercadoLibre para CRAFTERS.

    streamlit run crafters_app.py

Seis secciones: precios, precios mayoristas por reglas, stock de ML,
control de stock propio, rentabilidad por SKU y precios de la competencia.

Las que escriben en la cuenta real siguen siempre el mismo flujo:
simular -> revisar -> confirmar -> aplicar. Nunca se aplica nada sin pasar
por la simulacion.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import actualizador as act
import alertas_stock
import almacen
import buybox
import cambios
import plata as plata_mod
import competencia
import conciliacion
import conversion
import duplicados
import envios
import panel_ads
import publicidad
import publicidad_cron
import full
import salud
import espejos
import financiacion
import reclamos as rec
import rentabilidad as rent
import lista_precios as LP
import mayoristas
import preguntas as preg
import kits as kits_mod
import promociones
import promos_campanas
import promos_planilla
import reporte
import stock_control
import tramos
import tutorial_crafters
import ventana
from catalogo import (CACHE as CACHE_CATALOGO, actualizado_en as catalogo_al,
                      bajar_catalogo)
from meli import Meli, MeliError, es_error_de_api

_ASSETS = Path(__file__).resolve().parent / "_assets"
LOGO = _ASSETS / "logo_crafters.png"          # horizontal, para el encabezado
ICONO = _ASSETS / "icono_crafters.png"        # cuadrado, para la pestaña

st.set_page_config(page_title="MercadoLibre — CRAFTERS",
                   page_icon=str(ICONO) if ICONO.exists() else "🛒",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    [data-testid="stMain"] .stButton > button,
    [data-testid="stMain"] .stDownloadButton > button,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button {
        background-color: #C8552F !important;
        color: #FFFFFF !important;
        border-color: #C8552F !important;
        padding: 0.2rem 0.7rem !important;
        font-size: 0.72rem !important;
        letter-spacing: 0.03em;
    }
    [data-testid="stMain"] .stButton > button:hover,
    [data-testid="stMain"] .stDownloadButton > button:hover,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button:hover {
        background-color: #A8451F !important;
        border-color: #A8451F !important;
        color: #FFFFFF !important;
    }
    [data-testid="stMain"] [data-testid="stMetricValue"] {
        font-size: 1.5rem !important; line-height: 1.1 !important;
    }
    [data-testid="stMain"] [data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
    }

    /* Preguntas va destacada en naranja, como el boton de Tutorial. Es la
       segunda opcion del selector de seccion: si se reordena la lista de
       arriba, hay que mover el nth-of-type junto con ella. */
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3),
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3):hover,
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3):focus,
    [data-testid="stMain"] [data-testid="stButtonGroup"] > div > button:nth-of-type(3) {
        background-color: #C8552F !important;
        color: #FFFFFF !important;
        border-color: #C8552F !important;
    }
    [data-testid="stMain"] [data-testid="stButtonGroup"] button:nth-of-type(3) * {
        color: #FFFFFF !important;
    }
    </style>
""", unsafe_allow_html=True)


# ===================================================================== login

def autenticado():
    # Sin secrets.toml (uso local) st.secrets revienta, no devuelve vacio.
    try:
        clave = st.secrets.get("crafters_password")
    except Exception:
        clave = None
    if not clave:
        return True          # sin clave configurada, uso local

    if st.session_state.get("auth_crafters"):
        return True

    izq, centro, der = st.columns([1, 2, 1])
    with centro:
        if LOGO.exists():
            st.image(str(LOGO), width=280)
        st.markdown("<h3 style='margin:0.5rem 0 0.25rem 0;'>Herramientas de "
                    "MercadoLibre</h3>", unsafe_allow_html=True)
        st.caption("Precios, stock y rentabilidad. Acceso restringido.")
        with st.form("login"):
            ingresada = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar", use_container_width=True):
                if ingresada == clave:
                    st.session_state["auth_crafters"] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
    return False


if not autenticado():
    st.stop()


# ===================================================================== datos

@st.cache_resource(show_spinner=False)
def conectar():
    return Meli(verbose=False)


@st.cache_data(ttl=1800, show_spinner="Cargando catálogo de MercadoLibre...")
def cargar_catalogo_cacheado(_ml, sello):
    """`sello` fuerza el refresco cuando el operador aprieta el botón."""
    if CACHE_CATALOGO.exists() and sello == 0:
        return json.loads(CACHE_CATALOGO.read_text(encoding="utf-8"))
    return bajar_catalogo(_ml)


try:
    ml = conectar()
except MeliError as e:
    st.error(f"No hay conexión con MercadoLibre: {e}")
    st.info("Corré `python autorizar.py` en la carpeta del proyecto.")
    st.stop()

if "sello_catalogo" not in st.session_state:
    st.session_state["sello_catalogo"] = 0

pubs = cargar_catalogo_cacheado(ml, st.session_state["sello_catalogo"])
activas = [p for p in pubs if p.get("status") == "active"]


# ===================================================================== header

@st.dialog("Tutorial — Herramientas de MercadoLibre", width="large")
def _tutorial_dialog():
    tutorial_crafters.render()


@st.dialog("Novedades — qué cambió en la app", width="large")
def _cambios_dialog():
    cambios.render()


enc_logo, enc_info, enc_btn = st.columns([1.1, 2, 1.3])
with enc_logo:
    if LOGO.exists():
        st.image(str(LOGO), width=190)
    else:
        st.markdown("### CRAFTERS")
with enc_info:
    st.markdown("##### Herramientas de MercadoLibre")
    st.caption(f"{len(pubs):,} publicaciones · {len(activas):,} activas"
               .replace(",", "."))
    # Dos fechas distintas: cuando se bajo el catalogo (lo que cambia el boton
    # de al lado) y cuando se actualizo la app (lo que cuenta Novedades). El
    # encabezado mostraba la segunda con el rotulo de la primera, asi que
    # apretar "Actualizar catalogo" bajaba todo de nuevo y la fecha no se movia.
    st.caption(f"Catálogo bajado: **{catalogo_al() or 'todavía no'}**")
    st.caption(f"Versión de la app: {cambios.ultima_actualizacion()}")
with enc_btn:
    bt1, bt2 = st.columns(2)
    if bt1.button("📖 Tutorial", use_container_width=True):
        _tutorial_dialog()
    if bt2.button("🆕 Novedades", use_container_width=True):
        _cambios_dialog()
    if st.button("↻ Actualizar catálogo", use_container_width=True):
        st.session_state["sello_catalogo"] += 1
        st.cache_data.clear()
        st.rerun()

seccion = st.segmented_control(
    "Sección", ["Plata sobre la mesa", "Reporte semanal", "Preguntas",
                "Alertas", "Ganar la venta",
                "Precios", "Mayoristas", "PROMOS", "KITS",
                "Stock ML", "Control de stock",
                "Rentabilidad", "Precio óptimo", "Competencia",
                "Publicidad", "Oportunidades"],
    default="Plata sobre la mesa", label_visibility="collapsed",
    # La key ademas de fijar la seccion entre reruns permite recorrerla desde
    # los tests: sin key, AppTest no puede cambiar de seccion.
    key="seccion_activa")

# En la nube el disco se borra en cada reinicio: si no hay Sheet configurada,
# se perderia el refresh_token (habria que reautorizar a mano) y la auditoria.
if not almacen.hay_sheet():
    st.warning(
        "**Sin Google Sheet configurada.** El token y el registro de auditoría "
        "se guardan en archivos locales. Está bien para uso desde tu máquina, "
        "pero en Streamlit Cloud se borran en cada reinicio: habría que volver "
        "a autorizar a mano y se perdería el historial de cambios.", icon="⚠️")

st.divider()


# ===================================================================== helpers

def pesos(v):
    try:
        return f"${float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


@st.cache_data(ttl=300, show_spinner=False)
def _sesion_panel_viva(sello):
    """Si la cookie del panel entra. Se cachea: es una llamada a ML."""
    return panel_ads.sesion_viva()


def _sesion_del_panel():
    """
    El cartel de la cookie del panel, arriba de todo en Publicidad.

    **Existe porque no tenerla no se notaba.** La escritura de publicidad no
    va por la API pública —ML no se la habilitó a la app— sino por el panel,
    que necesita la cookie `ssid`. Cuando vence, los pedidos no fallan con un
    error de permisos: contestan como si el anuncio no existiera. El cron del
    martes terminaba en verde diciendo "Apagados 0 de 35" y nadie se enteraba
    de que había $1.261.770 de gasto que se tenía que haber cortado.
    """
    viva, motivo = _sesion_panel_viva(st.session_state.get("sesion_sello", 0))

    if viva:
        st.success("Sesión del panel activa: se puede aplicar.", icon="🔓")
        with st.expander("Cambiar la cookie"):
            _pegar_cookie()
        return True

    st.error(
        f"**No se puede aplicar nada: {motivo}.**\n\n"
        "Leer anda igual —los números de abajo son de verdad— pero *apagar, "
        "encender, agregar a campaña y crear campañas* necesita la cookie del "
        "panel. Sin ella el proceso corre, dice que hizo todo y no hace nada.",
        icon="🔒")
    _pegar_cookie()
    return False


def _pegar_cookie():
    st.markdown(
        "**Cómo sacarla:** entrá al [panel de "
        "Publicidad](https://ads.mercadolibre.com.ar) → `F12` → pestaña "
        "**Network** → pausá y despausá cualquier anuncio → click derecho en "
        "el pedido que aparece → **Copy as cURL**. Pegalo acá: de todo eso "
        "se guarda **solo el `ssid`**, que es lo único que hace falta.")
    # El `key` lleva el sello adentro a propósito. Hay que vaciar el cuadro
    # después de guardar —es una credencial, no tiene por qué quedar a la
    # vista— y Streamlit **no deja** escribir `session_state["ck_txt"] = ""`
    # una vez que el widget se dibujó en este run: tira StreamlitAPIException.
    # Al mover el sello, el `key` cambia, el widget es otro y nace vacío solo.
    sello = st.session_state.get("sesion_sello", 0)
    txt = st.text_area(
        "Pegá el cURL (o el ssid pelado)", height=110, key=f"ck_txt_{sello}",
        placeholder="curl 'https://pa.mercadolibre.com.ar/...' -H 'cookie: ...ssid=...'")
    c1, c2 = st.columns([1, 3])
    if c1.button("Guardar cookie", key=f"ck_go_{sello}", disabled=not txt.strip()):
        ok, det = panel_ads.guardar_sesion(txt)
        if ok:
            st.session_state["sesion_sello"] = sello + 1
            st.session_state.pop(f"ck_txt_{sello}", None)
            st.success(det)
            st.rerun()
        else:
            st.error(det)
    c2.caption(
        "La cookie es tu sesión de MercadoLibre: sirve para todo lo que vos "
        "podés hacer. Se guarda en este servidor y se pierde al reiniciar la "
        "app. Para invalidarla, cambiá la contraseña de MercadoLibre.")


def pesos_md(v):
    """
    Igual que `pesos()` pero con el `$` escapado, para textos en markdown.

    Streamlit interpreta lo que va **entre dos `$`** como fórmula LaTeX y lo
    renderiza como matemática. Un texto tan común como "de $29.615 a $33.000"
    se convierte en un engendro ilegible. Con un solo importe no pasa nada;
    con dos o más en el mismo texto, sí. Usar esta en `st.markdown`,
    `st.error`, `st.warning`, `st.info`, `st.caption` y `st.success`.

    En `st.metric` y en las tablas NO hace falta: ahí no se interpreta
    markdown y se vería el backslash.
    """
    return pesos(v).replace("$", "\\$")


def cumplen(n):
    """'1 publicación cumple' / '3 publicaciones cumplen'."""
    return (f"**1 publicación cumple el criterio.**" if n == 1
            else f"**{n} publicaciones cumplen el criterio.**")


@st.cache_data(ttl=300, show_spinner=False)
def _costos_guardados_cache(sello):
    """`sello` fuerza la relectura cuando el operador sube una planilla nueva."""
    return rent.costos_guardados()


def bloque_costos(clave):
    """
    Planilla de costos: se guarda una vez y la usan Rentabilidad y Buy Box.

    Antes había que subirla en cada sección y en cada visita, porque el
    `file_uploader` no sobrevive al rerun. Ahora se guarda en la planilla
    (mismo motivo que los tokens: en Streamlit Cloud el disco es efímero) y
    solo hace falta volver a subirla cuando cambian los costos.

    Devuelve el DataFrame de costos, o None si todavía no hay ninguno.
    """
    sello = st.session_state.get("sello_costos", 0)
    guardados, cuando = _costos_guardados_cache(sello)

    if len(guardados):
        c1, c2 = st.columns([3, 1.4])
        c1.success(
            f"Usando la planilla de costos guardada: **{len(guardados)} SKU**"
            + (f", actualizada el {cuando}." if cuando else "."), icon="📄")
        with c2:
            st.write("")
            reemplazar = st.toggle("Subir otra", key=f"rep_{clave}")
    else:
        st.info("Todavía no hay una planilla de costos guardada. Subí una y "
                "queda disponible para Rentabilidad y para Buy Box.", icon="📄")
        reemplazar = True

    if reemplazar:
        archivo = st.file_uploader(
            "Planilla de costos (.xlsx o .csv) — una columna de SKU y una de costo",
            type=["xlsx", "xls", "csv"], key=f"up_{clave}")
        quien = st.text_input("Tu nombre (queda en el registro)",
                              key=f"opc_{clave}")
        if archivo and st.button("Guardar la planilla", key=f"save_{clave}",
                                 disabled=not quien.strip()):
            try:
                nuevos = rent.leer_costos(archivo)
            except Exception as e:
                st.error(f"No pude leer la planilla: {e}")
                return guardados if len(guardados) else None
            ok, detalle = rent.guardar_costos(nuevos, operador=quien.strip())
            if ok:
                st.session_state["sello_costos"] = sello + 1
                st.success(f"Guardados {len(nuevos)} costos. Ya los usan "
                           "Rentabilidad y Buy Box.")
                st.rerun()
            else:
                st.error(f"No pude guardar: {detalle}")
                return nuevos      # al menos sirve para esta corrida

    return guardados if len(guardados) else None


@st.cache_data(ttl=300, show_spinner=False)
def _lista_precios_cache(sello):
    """`sello` fuerza la relectura cuando se sube una lista nueva."""
    import lista_precios as lp
    df, cuando = lp.guardada()
    return lp.mapa_precios(df), lp.mapa_por_ean(df), cuando, len(df)


def precios_de_lista():
    """
    (mapa por SKU, mapa por EAN, cuándo, cuántos). Vacíos si no hay lista.

    Se lee una vez y la usan Precio óptimo, Ganar la venta, Competencia y
    Rentabilidad. Nunca falla: sin lista, cada sección sigue con el criterio
    de siempre y lo dice.
    """
    sello = st.session_state.get("sello_lista", 0)
    try:
        return _lista_precios_cache(sello)
    except Exception:  # noqa: BLE001
        return {}, {}, "", 0


def aviso_lista(n, cuando):
    """La línea que explica de dónde sale el precio de publicación."""
    if n:
        st.caption(
            f"Precio **mínimo** de publicación: lista de Suprabond, {n} SKU"
            + (f" (actualizada el {cuando})." if cuando else ".")
            + " Por encima se puede cobrar lo que se quiera; por debajo solo "
            f"con el descuento permitido, hasta **{LP.DESCUENTO_PERMITIDO:.0%}**.")
    else:
        st.caption(
            "Sin lista de precios cargada: manda el precio mínimo despejado "
            "del costo. Se carga desde **Rentabilidad**.")


def aviso_sin_lista(n_sin):
    """Los SKU de otros proveedores no tienen mínimo ni descuento."""
    if n_sin:
        st.caption(
            f"{n_sin} SKU no están en la lista (otros proveedores): **no "
            "tienen mínimo sugerido ni el descuento del 20%**, y se guían "
            "solo por el precio que despeja el costo.")


def bloque_lista_precios(clave):
    """
    La lista de precios del proveedor: qué cuesta y a qué precio publicar.

    Va junto a la planilla de costos porque son la misma decisión vista de dos
    lados, pero se guardan aparte: los costos cubren todo el catálogo y la
    lista cubre solo lo de Suprabond.
    """
    import lista_precios as lp

    sello = st.session_state.get("sello_lista", 0)
    mapa, _, cuando, n = precios_de_lista()

    if n:
        c1, c2 = st.columns([3, 1.4])
        c1.success(
            f"Lista de precios cargada: **{n} SKU**"
            + (f", actualizada el {cuando}." if cuando else "."), icon="🏷️")
        with c2:
            st.write("")
            reemplazar = st.toggle("Subir otra", key=f"replp_{clave}")
    else:
        st.info(
            "Todavía no hay lista de precios. Es la que trae el **costo** y el "
            "**precio al que hay que publicar** (`PRECIO_SUGERIDO_ONLINE`).",
            icon="🏷️")
        reemplazar = True

    if not reemplazar:
        return

    archivo = st.file_uploader(
        "Lista de precios de Suprabond (.xlsx)", type=["xlsx", "xls"],
        key=f"uplp_{clave}")
    if not archivo:
        return

    try:
        df = lp.leer(archivo, pubs)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude leer la lista: {e}")
        return

    r = lp.resumen_cruce(df)
    m1, m2, m3 = st.columns(3)
    m1.metric("Filas en la lista", f"{r['filas']:,}".replace(",", "."))
    m2.metric("Cruzadas con ML", f"{r['resueltos']:,}".replace(",", "."))
    m3.metric("Sin publicar", f"{r['no_publicados']:,}".replace(",", "."))

    if r["no_publicados"]:
        st.caption(
            f"Las {r['no_publicados']} sin publicar son combos, exhibidores y "
            "sets del canal comercio: no tienen publicación en MercadoLibre, "
            "así que no hay precio que guiar.")
    if r["ambiguos"] or r["duplicados"]:
        st.caption(
            f"{r['ambiguos'] + r['duplicados']} quedaron sin asignar porque "
            "dos productos distintos caían en el mismo código. Se dejan sin "
            "precio a propósito: uno equivocado viajaría callado hasta la "
            "publicación.")

    with st.expander("Ver el cruce"):
        st.dataframe(
            df[["producto_id", "sku", "via", "costo", "sugerido",
                "descripcion"]],
            hide_index=True, use_container_width=True,
            column_config={
                "producto_id": "Código Suprabond", "sku": "SKU CRAFTERS",
                "via": "Cruzado por",
                "costo": st.column_config.NumberColumn("Costo", format="$%.0f"),
                "sugerido": st.column_config.NumberColumn(
                    "Publicar a", format="$%.0f"),
                "descripcion": "Descripción"})

    if st.button("Guardar la lista", key=f"savelp_{clave}", type="primary"):
        ok, detalle, cuantos = lp.guardar(df)
        if ok:
            st.session_state["sello_lista"] = sello + 1
            st.success(f"Guardados {cuantos} precios. Ya los usan Precio "
                       "óptimo, Ganar la venta, Competencia y Rentabilidad.")
            st.rerun()
        else:
            st.error(f"No pude guardar: {detalle}")


def controles_otros_conceptos(clave):
    """
    Los tres costos de estructura que no cobra ML pero hay que cargarle igual
    a cada venta. Se usan los mismos en Rentabilidad y en Buy Box a propósito:
    si no coincidieran, Buy Box aprobaría bajas de precio que Rentabilidad
    marca como pérdida.
    """
    st.caption(
        "**Otros conceptos** — costos que no cobra MercadoLibre pero igual "
        "hay que cargarle a cada venta. Se aplican como porcentaje del "
        f"**ingreso sin IVA**. El logístico es el porcentaje **o "
        f"{pesos_md(rent.TOPE_LOGISTICO)}, lo que sea menor**.")
    # En puntos porcentuales enteros: mostrar "0.10" se lee como 0,1%.
    o1, o2, o3 = st.columns(3)
    return {
        "impuestos": o1.number_input(
            "Impuestos %", 0, 100,
            int(rent.OTROS_CONCEPTOS["impuestos"] * 100), 1,
            key=f"imp_{clave}") / 100,
        "logistico": o2.number_input(
            "Logístico %", 0, 100,
            int(rent.OTROS_CONCEPTOS["logistico"] * 100), 1,
            key=f"log_{clave}") / 100,
        "general": o3.number_input(
            "General %", 0, 100,
            int(rent.OTROS_CONCEPTOS["general"] * 100), 1,
            key=f"gen_{clave}") / 100,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def cargos_cacheados(_ml, dias=90):
    """
    Cargos reales por SKU (comisión y envío medidos de las ventas).

    Lo usan Buy Box y Promociones para saber qué queda por unidad a un precio
    más bajo. Se cachea una hora porque trae el histórico y muestrea envíos.
    """
    ordenes = rent.traer_historico(_ml, dias)
    envios = rent.traer_costos_envio(_ml, ordenes, muestra_por_sku=5)
    return rent.cargos_por_sku(ordenes, envios)


def bloque_carga(operacion):
    """
    UI comun de precios y stock: subir planilla, simular, revisar, aplicar.
    `operacion` es 'precio' o 'stock'.
    """
    etiqueta = "precios" if operacion == "precio" else "stock"
    k = f"sim_{operacion}"          # la simulacion vive en session_state para
    kr = f"res_{operacion}"         # que no se pierda al tocar otro widget

    st.markdown(f"#### Actualización masiva de {etiqueta}")
    st.caption(
        "Subí una planilla con una columna de **SKU** (o el código **MLA**) y otra "
        f"con el **{'precio' if operacion == 'precio' else 'stock'}** nuevo. "
        "Los SKU que no estén en la planilla no se tocan.")

    archivo = st.file_uploader("Planilla (.xlsx o .csv)", type=["xlsx", "xls", "csv"],
                               key=f"up_{operacion}")
    if not archivo:
        st.session_state.pop(k, None)
        st.session_state.pop(kr, None)
        return

    try:
        df = act.leer_planilla(archivo)
    except Exception as e:
        st.error(f"No pude leer la planilla: {e}")
        return

    col_clave_auto, col_valor_auto = act.detectar_columnas(df, operacion)
    cols = list(df.columns)

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        col_clave = st.selectbox(
            "Columna de SKU / MLA", cols,
            index=cols.index(col_clave_auto) if col_clave_auto in cols else 0,
            key=f"ck_{operacion}")
    with c2:
        col_valor = st.selectbox(
            f"Columna de {'precio' if operacion == 'precio' else 'stock'}", cols,
            index=cols.index(col_valor_auto) if col_valor_auto in cols else 0,
            key=f"cv_{operacion}")
    with c3:
        st.metric("Filas", f"{len(df):,}".replace(",", "."))

    with st.expander("Ver la planilla como la leí"):
        st.dataframe(df.head(50), use_container_width=True)

    if st.button(f"Simular cambios de {etiqueta}", key=f"sim_btn_{operacion}"):
        try:
            st.session_state[k] = act.simular(df, pubs, operacion,
                                              col_clave, col_valor)
            st.session_state.pop(kr, None)
        except Exception as e:
            st.error(f"Error al simular: {e}")

    sim = st.session_state.get(k)
    if sim is None:
        return

    if sim.empty:
        st.warning("La simulación no encontró ninguna fila utilizable.")
        return

    # ------------------------------------------------ resultado de la simulacion
    res = act.resumen(sim)
    st.markdown("##### Qué va a pasar")

    m1, m2, m3 = st.columns(3)
    m1.metric("Se actualizan", res.get("actualizar", 0))
    m2.metric("Para revisar", res.get("revisar", 0))
    m3.metric("Sin cambio", res.get("sin_cambio", 0))

    problemas = {kk: v for kk, v in res.items()
                 if kk not in ("actualizar", "revisar", "sin_cambio")}
    if problemas:
        p1, p2, p3 = st.columns(3)
        for col, (nombre, cant) in zip([p1, p2, p3] * 3, problemas.items()):
            col.metric(nombre.replace("_", " ").capitalize(), cant)

    if res.get("revisar"):
        st.warning(
            f"**{res['revisar']} publicaciones tienen un cambio grande** "
            f"(más de {act.UMBRAL_ALERTA_PRECIO:.0%} de variación). "
            "Revisalas antes de incluirlas: suele ser un error de carga.")

    ambiguos = sim[sim["accion"] == "ambiguo"]
    if len(ambiguos):
        st.error(
            f"**{len(ambiguos)} SKU tienen el stock repartido en varios productos "
            "de MercadoLibre.** Poner el mismo número en cada uno duplicaría el "
            "stock, así que quedan sin tocar. Hay que definir a cuál corresponde.")

    filtro = st.multiselect("Filtrar por acción", sorted(sim["accion"].unique()),
                            default=sorted(sim["accion"].unique()),
                            key=f"f_{operacion}")
    vista = sim[sim["accion"].isin(filtro)]

    st.dataframe(
        vista, use_container_width=True, height=340,
        column_config={
            "clave": "SKU / MLA",
            "item_id": "Publicación",
            "titulo": "Título",
            "tipo": "Tipo",
            "logistica": "Logística",
            "valor_actual": st.column_config.NumberColumn(
                "Actual", format="%.0f"),
            "valor_nuevo": st.column_config.NumberColumn(
                "Nuevo", format="%.0f"),
            "variacion": st.column_config.NumberColumn(
                "Variación", format="percent"),
            "accion": "Acción",
            "motivo": "Motivo",
        })

    st.download_button(
        "Descargar la simulación", vista.to_csv(index=False).encode("utf-8"),
        f"simulacion_{operacion}_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
        key=f"dl_{operacion}")

    # ------------------------------------------------ aplicar
    st.divider()
    st.markdown("##### Aplicar en MercadoLibre")

    a1, a2 = st.columns([2, 3])
    with a1:
        operador = st.text_input("Tu nombre o iniciales (queda en el registro)",
                                 key=f"op_{operacion}")
    with a2:
        incluir = st.checkbox(
            f"Incluir también las {res.get('revisar', 0)} marcadas para revisar",
            key=f"inc_{operacion}", disabled=not res.get("revisar"))

    a_aplicar = res.get("actualizar", 0) + (res.get("revisar", 0) if incluir else 0)

    if a_aplicar == 0:
        st.info("No hay nada para aplicar.")
        return

    st.warning(f"Se van a modificar **{a_aplicar} publicaciones** en la cuenta real.")
    confirmo = st.checkbox(f"Confirmo que quiero cambiar el {etiqueta} de esas "
                           f"{a_aplicar} publicaciones", key=f"conf_{operacion}")

    if st.button(f"Aplicar {etiqueta}", key=f"go_{operacion}",
                 disabled=not (confirmo and operador.strip())):
        barra = st.progress(0.0, text="Aplicando...")
        def avance(i, total, fila):
            barra.progress(i / total, text=f"Aplicando {i} de {total}...")

        with st.spinner("Escribiendo en MercadoLibre..."):
            st.session_state[kr] = act.aplicar(
                ml, sim, operacion, operador=operador.strip(),
                incluir_revisar=incluir, callback=avance)
        barra.empty()

    resultados = st.session_state.get(kr)
    if resultados is not None and len(resultados):
        ok = (resultados["resultado"] == "OK").sum()
        err = len(resultados) - ok
        if err == 0:
            st.success(f"Listo: {ok} publicaciones actualizadas.")
        else:
            st.error(f"{ok} actualizadas, {err} con error. El detalle está abajo.")
        st.dataframe(resultados, use_container_width=True, height=280)
        st.download_button(
            "Descargar el resultado",
            resultados.to_csv(index=False).encode("utf-8"),
            f"resultado_{operacion}_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv",
            key=f"dlr_{operacion}")
        st.caption(f"Todos los cambios quedaron registrados en {almacen.describir()}.")


# ===================================================================== secciones

if seccion == "Plata sobre la mesa":
    st.markdown("#### Todo lo accionable, ordenado por plata")
    st.caption(
        "La información ya estaba repartida en seis secciones. Acá está junta: "
        "cada fila dice cuánta plata es, qué hay que hacer y dónde se hace.")

    costos_pl = bloque_costos("pl")
    otros_pl = controles_otros_conceptos("pl")

    g1, g2 = st.columns([1.2, 3])
    con_promos = g1.checkbox("Incluir promociones", value=False,
                             help="Suma unos minutos: consulta las ofertas "
                                  "publicación por publicación.")
    g2.write("")
    if costos_pl is not None and g2.button("Buscar la plata",
                                           use_container_width=True):
        estado = st.empty()
        with st.spinner("Cruzando stock, márgenes y Buy Box..."):
            cargos_pl = cargos_cacheados(ml)
            ordenes_pl = rent.traer_historico(ml, 90)

            estado.caption("Revisando el stock...")
            stock_pl = alertas_stock.analizar(ml, dias=90, pubs=pubs,
                                              ordenes=ordenes_pl)
            estado.caption("Calculando márgenes...")
            rent_pl = rent.calcular(costos_pl, cargos_pl, pubs, iva=0.21,
                                    otros_conceptos=otros_pl)
            estado.caption("Consultando el Buy Box...")
            cat_pl = [p["id"] for p in pubs if p.get("status") == "active"
                      and p.get("catalog_listing")]
            ptw_pl = buybox.traer_price_to_win(
                ml, cat_pl, callback=lambda m: estado.caption(str(m)))
            ven_pl = ventana.analizar(costos_pl, cargos_pl, pubs, iva=0.21,
                                      otros_conceptos=otros_pl, objetivo=0.0,
                                      ptw_por_item=ptw_pl,
                                      precios_lista=precios_de_lista()[0])
            promos_pl = None
            if con_promos:
                estado.caption("Buscando promociones...")
                unid_pl = dict(zip(cargos_pl["sku"],
                                   cargos_pl["unidades_vendidas"]))
                promos_pl, _ = promociones.analizar(
                    ml, pubs=pubs, tope=150, cargos=cargos_pl,
                    unidades=unid_pl,
                    callback=lambda m: estado.caption(str(m)))

            st.session_state["plata"] = plata_mod.juntar(
                stock=stock_pl, ventana=ven_pl, rentabilidad=rent_pl,
                promos=promos_pl)
        estado.empty()

    dpl = st.session_state.get("plata")
    if dpl is not None and len(dpl):
        rpl = plata_mod.resumen(dpl)

        h1, h2 = st.columns(2)
        h1.metric("Facturación parada", pesos(rpl["facturacion_parada"]) + "/mes",
                  help="Lo que hoy NO entra porque el producto no se puede "
                       "vender")
        h2.metric("Margen en juego", pesos(rpl["margen_en_juego"]) + "/mes",
                  help="Lo que se pierde o se deja de ganar vendiendo")
        st.caption(
            "**Los dos números no se suman**: uno es facturación que no entra "
            "y el otro es margen que se pierde. Sumarlos daría un número "
            "grande y sin sentido.")

        if rpl["conflictos"]:
            n_conf = rpl["conflictos"]
            st.error(
                (f"**{n_conf} producto está para reponer pero pierde plata "
                 "en cada unidad.**" if n_conf == 1 else
                 f"**{n_conf} productos están para reponer pero pierden "
                 "plata en cada unidad.**")
                + " Reponerlos aumenta la pérdida: primero hay que arreglar "
                  "el precio o el costo. Están marcados en la lista.",
                icon="⚠️")

        st.markdown("##### Por acción")
        st.dataframe(
            pd.DataFrame([
                {"Acción": k, "Casos": v["count"],
                 "Plata por mes": v["sum"], "Mide": v["unidad"]}
                for k, v in sorted(rpl["por_accion"].items(),
                                   key=lambda x: -x[1]["sum"])]),
            use_container_width=True, hide_index=True,
            column_config={"Plata por mes": st.column_config.NumberColumn(
                "Plata por mes", format="%.0f")})

        acciones = sorted(dpl["accion_nombre"].unique())
        filtro_pl = st.multiselect("Filtrar por acción", acciones,
                                   default=acciones, key="f_pl")
        vpl = dpl[dpl["accion_nombre"].isin(filtro_pl)] if filtro_pl else dpl

        st.dataframe(
            vpl[["accion_nombre", "sku", "titulo", "detalle", "plata_mes",
                 "unidad", "seccion", "base"]],
            use_container_width=True, height=440, hide_index=True,
            column_config={
                "accion_nombre": "Qué hacer", "sku": "SKU",
                "titulo": "Título", "detalle": "Detalle",
                "plata_mes": st.column_config.NumberColumn(
                    "Plata/mes", format="%.0f"),
                "unidad": "Mide", "seccion": "Dónde se hace",
                "base": st.column_config.TextColumn(
                    "Sobre qué base", help="De dónde sale el número")})

        st.download_button(
            "Descargar la lista",
            vpl.to_csv(index=False).encode("utf-8"),
            f"plata_{datetime.now():%Y%m%d}.csv", "text/csv")

        st.caption(
            "Las estimaciones asumen **el mismo volumen** que el período "
            "medido. Cambiar un precio cambia el volumen, así que son "
            "referencias de tamaño para priorizar, no proyecciones.")

        # ------------------------------------------------ ejecutar en lote
        st.divider()
        st.markdown("##### Aplicar los cambios de precio")
        st.caption(
            "De las cuatro acciones, **dos se resuelven cambiando un "
            "precio** y se pueden aplicar desde acá: *pierde plata* y *correr "
            "al escalón*. Reponer stock se hace comprando mercadería y las "
            "promociones se toman desde el panel de MercadoLibre, así que "
            "ésas quedan afuera.")

        ej1, ej2 = st.columns([1.4, 2.6])
        cambio_pl = ej1.slider(
            "Cambio máximo", 1, int(plata_mod.TECHO_DE_CAMBIO * 100), 15, 1,
            format="%d%%", key="cb_pl",
            help=f"Tope duro de esta pantalla: "
                 f"{plata_mod.TECHO_DE_CAMBIO:.0%}. Para subas más grandes "
                 "está Precio óptimo, que obliga a mirarlas de a una.") / 100
        acc_pl = ej2.multiselect(
            "Qué aplicar",
            [plata_mod.ACCIONES[a] for a in plata_mod.ACCIONES_EJECUTABLES],
            default=[plata_mod.ACCIONES[a]
                     for a in plata_mod.ACCIONES_EJECUTABLES],
            key="ac_pl")
        claves_acc = [a for a in plata_mod.ACCIONES_EJECUTABLES
                      if plata_mod.ACCIONES[a] in acc_pl]

        ejec = plata_mod.ejecutables(dpl, cambio_maximo=cambio_pl,
                                     acciones=claves_acc or None)
        st.markdown(cumplen(len(ejec)))

        if len(ejec):
            st.caption("**Tildá filas para elegir a mano.** Si no seleccionás "
                       "ninguna van todas las que cumplen.")
            ev_pl = st.dataframe(
                ejec[["accion_nombre", "sku", "precio_actual",
                      "precio_sugerido", "cambio_pct", "plata_mes",
                      "detalle"]],
                use_container_width=True, height=320, hide_index=True,
                key="tabla_ejec", on_select="rerun",
                selection_mode="multi-row",
                column_config={
                    "accion_nombre": "Qué hacer", "sku": "SKU",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Precio nuevo", format="%.0f"),
                    "cambio_pct": st.column_config.NumberColumn(
                        "Cambio", format="percent"),
                    "plata_mes": st.column_config.NumberColumn(
                        "Plata/mes", format="%.0f"),
                    "detalle": "Detalle"})

            elegidas_pl = list(getattr(ev_pl.selection, "rows", []) or [])
            aplicar_pl = ejec.iloc[elegidas_pl] if elegidas_pl else ejec
            if elegidas_pl:
                st.info(f"Vas a aplicar solo las **{len(aplicar_pl)}** que "
                        "tildaste.", icon="👉")

            st.warning(
                "**Esto cambia precios en MercadoLibre de verdad.** El "
                "cálculo dice qué precio necesitás según tus costos, **no si "
                "el mercado lo va a pagar**. Todo queda en la auditoría con "
                "el precio anterior.", icon="⚠️")

            if st.button("Simular los cambios", key="sim_pl"):
                st.session_state["plata_sim"] = act.simular(
                    plata_mod.planilla_de_precios(aplicar_pl), pubs, "precio",
                    col_clave="sku", col_valor="precio")

            sim_pl = st.session_state.get("plata_sim")
            if sim_pl is not None and len(sim_pl):
                # Se pasa por el motor de Precios a propósito: trae el
                # resolver de SKU, el aviso de >50% y la auditoría.
                rev_pl = int((sim_pl["accion"] == "revisar").sum())
                q1, q2 = st.columns(2)
                q1.metric("Listas para aplicar",
                          int((sim_pl["accion"] == "actualizar").sum()))
                q2.metric("Marcadas para revisar", rev_pl)
                if rev_pl:
                    st.error(
                        f"**{rev_pl} superan el "
                        f"{act.UMBRAL_ALERTA_PRECIO:.0%} de variación** y no "
                        "se aplican salvo que lo pidas aparte.", icon="🛑")
                st.dataframe(sim_pl, use_container_width=True, height=280,
                             hide_index=True)

                op_pl = st.text_input("Tu nombre (queda en el registro)",
                                      key="op_pl")
                inc_pl = st.checkbox("Incluir también las marcadas para "
                                     "revisar", key="rev_pl")
                conf_pl = st.checkbox(
                    "Confirmo que quiero cambiar estos precios en "
                    "MercadoLibre", key="conf_pl")
                if st.button("Aplicar en MercadoLibre", key="go_pl",
                             disabled=not (conf_pl and op_pl.strip())):
                    barra = st.progress(0.0, text="Aplicando...")
                    res_pl = act.aplicar(
                        ml, sim_pl, "precio", operador=op_pl.strip(),
                        incluir_revisar=inc_pl,
                        callback=lambda i, t, f: barra.progress(
                            i / t, text=f"Aplicando {i} de {t}..."))
                    barra.empty()
                    ok = int((res_pl["resultado"] == "OK").sum())
                    if ok == len(res_pl):
                        st.success(f"{ok} precios actualizados.")
                    else:
                        st.error(f"{ok} aplicados, {len(res_pl) - ok} "
                                 "con error.")
                    st.dataframe(res_pl, use_container_width=True,
                                 hide_index=True)
                    # Los precios cambiaron: la lista quedó vieja.
                    st.session_state.pop("plata", None)
                    st.session_state.pop("plata_sim", None)
                    st.caption("Volvé a buscar la plata para ver el estado "
                               "nuevo.")
        else:
            st.info("Ninguna fila cumple el criterio de ejecución. Probá "
                    "subiendo el cambio máximo.")

elif seccion == "Reporte semanal":
    st.markdown("#### Cómo vino la semana")
    st.caption(
        "Una pantalla para el lunes: qué pasó, contra qué se compara y qué hay "
        "que resolver. El resto de las secciones hay que acordarse de abrirlas; "
        "esto se lee en dos minutos.")

    r1, r2, r3 = st.columns([1.6, 1.4, 1.4])
    periodo = r1.selectbox(
        "Período", ["Semana cerrada (lunes a domingo)", "Últimos 14 días",
                    "Últimos 30 días"],
        help="La semana cerrada se compara contra la anterior completa. "
             "Comparar una semana a medias contra una entera siempre da que "
             "las ventas se derrumbaron.")
    dias_rep = {"Semana cerrada (lunes a domingo)": None,
                "Últimos 14 días": 14, "Últimos 30 días": 30}[periodo]
    con_rec = r2.checkbox("Incluir reclamos", value=True,
                          help="Identificar el producto de cada reclamo cuesta "
                               "una llamada por envío: suma unos segundos.")
    r3.write("")
    if r3.button("Generar reporte", use_container_width=True):
        estado = st.empty()
        with st.spinner("Armando el reporte..."):
            st.session_state["reporte"] = reporte.generar(
                ml, dias_rep, pubs=pubs, con_reclamos=con_rec,
                callback=lambda m: estado.caption(str(m)))
        estado.empty()

    rep = st.session_state.get("reporte")
    if rep is not None:
        v = rep["ventas"]
        st.caption(
            f"**{v['desde']:%d/%m/%Y}** a **{v['hasta']:%d/%m/%Y}** · "
            f"comparado contra {v['desde_previa']:%d/%m} a "
            f"{v['hasta_previa']:%d/%m}")

        def delta(campo):
            x = v.get(f"var_{campo}")
            return None if x is None else f"{x:+.1%}"

        a1, a2, a3 = st.columns(3)
        a1.metric("Facturación", pesos(v["bruto"]), delta("bruto"))
        a2.metric("Neto post-comisión", pesos(v["neto"]), delta("neto"))
        a3.metric("Comisiones ML", pesos(v["comisiones"]), delta("comisiones"),
                  delta_color="inverse")

        b1, b2, b3 = st.columns(3)
        b1.metric("Órdenes", f"{v['ordenes']:,}".replace(",", "."),
                  delta("ordenes"))
        b2.metric("Unidades", f"{v['unidades']:,}".replace(",", "."),
                  delta("unidades"))
        b3.metric("Ticket promedio", pesos(v["ticket"]), delta("ticket"))
        st.caption(f"La comisión se llevó el **{v['comision_pct']:.1%}** de la "
                   "facturación del período.")

        # ------------------------------------------------------ a resolver
        st.divider()
        st.markdown("##### Para resolver esta semana")

        sr = rep["stock_resumen"]
        urg = rep["stock_urgentes"]
        if sr and (sr["sin_publicacion"] or sr["criticos"] or sr["sin_stock"]):
            st.error(
                f"**{sr['sin_publicacion'] + sr['sin_stock'] + sr['criticos']} "
                f"productos con problema de stock** — "
                f"{pesos_md(sr['plata_en_riesgo'])} de facturación semanal en "
                f"riesgo. {sr['sin_publicacion']} vendieron y hoy no tienen "
                f"ninguna publicación activa.", icon="📦")
            st.dataframe(
                urg[["sku", "titulo", "diagnostico", "stock", "dias_cobertura",
                     "plata_semanal_en_riesgo"]].head(15),
                use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "diagnostico": "Problema", "stock": "Stock",
                    "dias_cobertura": st.column_config.NumberColumn(
                        "Días", format="%.0f"),
                    "plata_semanal_en_riesgo": st.column_config.NumberColumn(
                        "Facturación/semana", format="%.0f")})
        elif sr:
            st.success("Ningún producto en riesgo de quedarse sin stock. 👌")

        c1, c2 = st.columns(2)
        if rep["preguntas_sin_responder"] is not None:
            n = rep["preguntas_sin_responder"]
            c1.metric("Preguntas sin responder", n)
        rr = rep["reclamos_resumen"]
        if rr:
            c2.metric("Reclamos del período", rr["reclamos"],
                      f"{rr['abiertos']} abiertos", delta_color="off")
            rd = rep["reclamos"]
            graves = (rd[rd["diagnostico"] == "tasa alta"]
                      if len(rd) else pd.DataFrame())
            if len(graves):
                st.warning(
                    f"**{len(graves)} productos con tasa de reclamo alta** "
                    f"(la tasa de la cuenta es {rr['tasa_cuenta']:.2%}).",
                    icon="⚠️")
                st.dataframe(
                    graves[["sku", "titulo", "reclamos", "unidades_vendidas",
                            "tasa", "motivo_principal"]].head(10),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "sku": "SKU", "titulo": "Título",
                        "reclamos": "Reclamos",
                        "unidades_vendidas": "Unidades",
                        "tasa": st.column_config.NumberColumn(
                            "Tasa", format="percent"),
                        "motivo_principal": "Motivo más frecuente"})

        # ------------------------------------------------------ que se movio
        st.divider()
        st.markdown("##### Lo que más facturó")
        top = rep["top"]
        if len(top):
            st.dataframe(
                top, use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título", "unidades": "Unidades",
                    "facturacion": st.column_config.NumberColumn(
                        "Facturación", format="%.0f"),
                    "var": st.column_config.NumberColumn(
                        "vs período anterior", format="percent",
                        help="Vacío = no vendió en el período anterior")})

        caidas = rep["caidas"]
        if len(caidas):
            st.markdown("##### Vendían y este período no vendieron nada")
            st.caption(
                f"Productos con {reporte.MINIMO_CAIDA} o más unidades en el "
                "período anterior y cero en este. Puede ser estacionalidad, "
                "pero también una publicación pausada o sin stock.")
            st.dataframe(
                caidas, use_container_width=True, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "unidades_previas": "Unidades antes",
                    "facturacion_previa": st.column_config.NumberColumn(
                        "Facturaba", format="%.0f")})

        st.download_button(
            "Descargar el detalle de stock",
            rep["stock"].to_csv(index=False).encode("utf-8"),
            f"reporte_stock_{datetime.now():%Y%m%d}.csv", "text/csv")

elif seccion == "Alertas":
    st.markdown("#### Lo que necesita atención")
    al = st.radio("Vista", ["Stock crítico", "Reclamos"],
                  horizontal=True, label_visibility="collapsed")

    if al == "Stock crítico":
        st.caption(
            "La pregunta no es cuánto stock hay sino **cuántos días queda**. "
            "40 unidades de algo que vende 1 por semana están bien; 40 de algo "
            "que vende 10 por día se agotan el jueves.")
        st.caption(
            "Ordenado por **plata en riesgo**: lo que ese producto deja de "
            "facturar por cada semana sin stock.")

        s1, s2 = st.columns([1.2, 3])
        # 90 dias por defecto igual que el resto de la app: el historico de
        # ordenes se cachea por ventana, asi que elegir otra obliga a bajarlo
        # entero de nuevo (son varios minutos).
        dias_st = s1.selectbox("Velocidad medida sobre", [30, 60, 90], index=2,
                               format_func=lambda d: f"{d} días", key="d_stk")
        if s2.button("Revisar el stock", use_container_width=True):
            estado = st.empty()
            with st.spinner("Calculando cobertura..."):
                st.session_state["alertas_stock"] = alertas_stock.analizar(
                    ml, dias_st, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfs = st.session_state.get("alertas_stock")
        if dfs is not None and len(dfs):
            res = alertas_stock.resumen(dfs)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Sin publicación activa", res["sin_publicacion"])
            k2.metric("Sin stock", res["sin_stock"])
            k3.metric("Críticos", res["criticos"])
            k4.metric("Bajos", res["bajos"])
            st.metric("Facturación semanal en riesgo",
                      pesos(res["plata_en_riesgo"]))

            with st.expander("Cómo se calcula y qué significa cada estado"):
                st.markdown(
                    f"- **Sin publicación activa**: el SKU vendió en el "
                    f"período pero hoy no tiene ninguna publicación activa. "
                    f"MercadoLibre **pausa sola** la publicación al llegar a "
                    f"cero, así que este es el caso típico del producto que se "
                    f"agotó y nadie repuso.\n"
                    f"- **Sin stock**: tiene publicación activa pero cero "
                    f"unidades.\n"
                    f"- **Crítico**: menos de {alertas_stock.DIAS_CRITICO} "
                    f"días de cobertura ({alertas_stock.DIAS_CRITICO_FULL} si "
                    f"está en Full, porque reponer allá tarda más).\n"
                    f"- **Bajo**: menos de {alertas_stock.DIAS_BAJO} días "
                    f"({alertas_stock.DIAS_BAJO_FULL} en Full).\n"
                    f"- **Sobrestock**: más de "
                    f"{alertas_stock.DIAS_SOBRESTOCK} días. No es urgente, "
                    f"es plata dormida.\n"
                    f"- **Pocas ventas / sin ventas**: menos de "
                    f"{alertas_stock.MINIMO_UNIDADES} unidades en el período. "
                    f"La velocidad no alcanza para proyectar nada.\n\n"
                    "El stock se agrupa por `user_product_id`: las "
                    "publicaciones espejo comparten unidades y sumarlas todas "
                    "contaría lo mismo varias veces.")

            estados = sorted(dfs["diagnostico"].unique())
            por_defecto = [e for e in estados if e in alertas_stock.URGENTES
                           or e == "bajo"]
            filtro_st = st.multiselect("Filtrar por estado", estados,
                                       default=por_defecto or estados)
            vst = dfs[dfs["diagnostico"].isin(filtro_st)] if filtro_st else dfs

            st.dataframe(
                vst, use_container_width=True, height=420, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título", "stock": "Stock",
                    "stock_propio": "Propio", "stock_full": "Full",
                    "unidades_periodo": "Vendidas",
                    "por_dia": st.column_config.NumberColumn(
                        "Por día", format="%.2f"),
                    "dias_cobertura": st.column_config.NumberColumn(
                        "Días de stock", format="%.0f"),
                    "precio": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "plata_semanal_en_riesgo": st.column_config.NumberColumn(
                        "Facturación/semana", format="%.0f"),
                    "publicaciones": "Pub.", "en_full": "En Full",
                    "diagnostico": "Estado"})
            st.download_button("Descargar el análisis",
                               vst.to_csv(index=False).encode("utf-8"),
                               f"stock_critico_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    else:
        st.caption(
            "Qué productos concentran los reclamos. Lo que importa no es el "
            "total sino la **tasa**: un SKU que reclama el 8% de sus ventas "
            "cuando la cuenta promedia 2,8% tiene un problema de producto, de "
            "ficha o de embalaje.")

        q1, q2 = st.columns([1.2, 3])
        dias_rec = q1.selectbox("Período", [30, 60, 90], index=2,
                                format_func=lambda d: f"{d} días", key="d_rec")
        if q2.button("Analizar reclamos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo reclamos e identificando productos..."):
                st.session_state["reclamos"] = rec.analizar(
                    ml, dias_rec, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado = st.session_state.get("reclamos")
        if guardado is not None:
            dfr, resr = guardado
            n1, n2, n3, n4 = st.columns(4)
            n1.metric("Reclamos", resr["reclamos"])
            n2.metric("Abiertos hoy", resr["abiertos"])
            n3.metric("Tasa de la cuenta", f"{resr['tasa_cuenta']:.2%}")
            n4.metric("Sin producto identificado", resr["sin_producto"])

            if resr["sin_producto"]:
                st.caption(
                    "Los reclamos que apuntan a un pago (no a un pedido ni a "
                    "un envío) no se pueden asociar al producto: la API no "
                    "expone ese camino. Quedan contados aparte.")

            m1, m2 = st.columns(2)
            with m1:
                st.markdown("**Por tipo**")
                st.dataframe(
                    pd.DataFrame(resr["por_tipo"].most_common(),
                                 columns=["Tipo", "Reclamos"]),
                    use_container_width=True, hide_index=True, height=200)
            with m2:
                st.markdown("**Motivos más frecuentes**")
                st.dataframe(
                    pd.DataFrame(resr["por_motivo"].most_common(8),
                                 columns=["Motivo", "Reclamos"]),
                    use_container_width=True, hide_index=True, height=200)

            if len(dfr):
                graves = dfr[dfr["diagnostico"] == "tasa alta"]
                if len(graves):
                    st.warning(
                        f"**{len(graves)} productos con tasa de reclamo por "
                        f"encima del {rec.TASA_ALTA:.0%}** sobre "
                        f"{rec.MINIMO_UNIDADES}+ ventas.", icon="⚠️")

                solo_conf = st.checkbox(
                    "Ver solo los que tienen ventas suficientes", value=True,
                    help=f"Con menos de {rec.MINIMO_UNIDADES} unidades "
                         "vendidas la tasa no significa nada: un reclamo "
                         "sobre 3 ventas da 33%.")
                vr = dfr[dfr["confiable"]] if solo_conf else dfr

                st.dataframe(
                    vr, use_container_width=True, height=420, hide_index=True,
                    column_config={
                        "sku": "SKU", "titulo": "Título",
                        "reclamos": "Reclamos", "abiertos": "Abiertos",
                        "unidades_vendidas": "Unidades",
                        "tasa": st.column_config.NumberColumn(
                            "Tasa", format="percent"),
                        "tipo_principal": "Tipo",
                        "motivo_principal": "Motivo más frecuente",
                        "ultimo_reclamo": "Último",
                        "diagnostico": "Diagnóstico", "confiable": None})
                st.download_button("Descargar el análisis",
                                   vr.to_csv(index=False).encode("utf-8"),
                                   f"reclamos_{datetime.now():%Y%m%d}.csv",
                                   "text/csv")

elif seccion == "Ganar la venta":
    _, _, _cuando_g, _n_g = precios_de_lista()
    aviso_lista(_n_g, _cuando_g)
    st.markdown("#### Ganar la venta")
    gv = st.radio("Vista", ["Buy Box", "Promociones"],
                  horizontal=True, label_visibility="collapsed")

    if gv == "Buy Box":
        st.caption(
            "**1.009 de tus publicaciones activas compiten en una página de "
            "catálogo.** En esas páginas todos los vendedores comparten la "
            "misma publicación y MercadoLibre muestra a uno solo: el que gana "
            "se lleva casi todas las ventas y el resto queda escondido detrás "
            "de *otras opciones de compra*. No es una diferencia de posición, "
            "es vender o no vender.")

        with st.expander("Por qué el precio para ganar no es el del ganador"):
            st.markdown(
                "El **precio para ganar** casi nunca coincide con lo que "
                "cobra el que gana hoy, y suele ser bastante más bajo. No es "
                "un error.\n\n"
                "MercadoLibre pondera el precio junto con los beneficios de "
                "la publicación: Full, envío gratis y cuotas. Si el ganador "
                "los tiene y vos no, para empatarle tenés que compensar con "
                "precio. **La diferencia entre lo que cobra el ganador y lo "
                "que tendrías que cobrar vos es, en pesos, lo que te cuesta "
                "no tener esas palancas.**\n\n"
                "De ahí salen dos diagnósticos que piden cosas opuestas:\n\n"
                "- **Perdés por precio**: el ganador está más barato. Se "
                "arregla con precio.\n"
                "- **Perdés estando más barato**: ya cobrás menos y perdés "
                "igual. Bajar más es tirar plata — lo que falta son las "
                "palancas. Acá es donde Full deja de ser una idea y se vuelve "
                "una cuenta concreta.")

        b1, b2 = st.columns([1.4, 3])
        tope_bb = b1.selectbox(
            "Alcance", [150, 400, 0],
            format_func=lambda t: (f"Las {t} que más venden" if t
                                   else "Todas (~5 min)"),
            key="tope_bb")
        b2.write("")
        if b2.button("Revisar el Buy Box", use_container_width=True):
            estado = st.empty()
            with st.spinner("Consultando el Buy Box publicación por publicación..."):
                cargos_bb = cargos_cacheados(ml)
                unidades_bb = dict(zip(cargos_bb["sku"],
                                       cargos_bb["unidades_vendidas"]))
                st.session_state["buybox"] = buybox.analizar(
                    ml, pubs=pubs, tope=tope_bb or None, cargos=cargos_bb,
                    unidades=unidades_bb,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dbb = st.session_state.get("buybox")
        if dbb is not None and len(dbb):
            rb = buybox.resumen(dbb)
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Publicaciones", rb["publicaciones"])
            g2.metric("Ganando", rb["ganando"])
            g3.metric("Compartiendo", rb["compartiendo"])
            g4.metric("Perdiendo", rb["perdiendo"])

            if rb["mas_barato_y_perdiendo"]:
                extra = (f" La penalización mediana por no tener las palancas "
                         f"es {pesos_md(rb['penalizacion_mediana'])}."
                         if rb["penalizacion_mediana"] else "")
                st.error(
                    f"**En {rb['mas_barato_y_perdiendo']} publicaciones ya "
                    f"estás más barato que el ganador y perdés igual.** Ahí "
                    f"bajar el precio no sirve: lo que falta son Full, envío "
                    f"gratis o cuotas.{extra}", icon="🎯")

            if rb["baja_chica"]:
                st.success(
                    f"**{rb['baja_chica']} publicaciones se ganan bajando "
                    f"menos del {buybox.BAJA_CHICA:.0%}.** Es lo más barato "
                    f"que podés hacer hoy.", icon="✅")

            st.caption(
                f"Las publicaciones que están perdiendo venden "
                f"**{rb['unidades_perdiendo']:,}** unidades en el período "
                "medido.".replace(",", "."))

            estados_bb = sorted(dbb["diagnostico"].unique())
            por_defecto_bb = [e for e in estados_bb
                              if e not in ("ganando", "no compite", "sin dato")]
            filtro_bb = st.multiselect("Filtrar por diagnóstico", estados_bb,
                                       default=por_defecto_bb or estados_bb)
            vbb = dbb[dbb["diagnostico"].isin(filtro_bb)] if filtro_bb else dbb

            st.dataframe(
                vbb, use_container_width=True, height=440, hide_index=True,
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "diagnostico": "Diagnóstico",
                    "precio_actual": st.column_config.NumberColumn(
                        "Tu precio", format="%.0f"),
                    "precio_para_ganar": st.column_config.NumberColumn(
                        "Para ganar", format="%.0f"),
                    "precio_ganador": st.column_config.NumberColumn(
                        "Precio del ganador", format="%.0f"),
                    "bajar": st.column_config.NumberColumn(
                        "Hay que bajar", format="%.0f"),
                    "bajar_pct": st.column_config.NumberColumn(
                        "Bajar %", format="percent"),
                    "penalizacion_palancas": st.column_config.NumberColumn(
                        "Costo de no tener palancas", format="%.0f",
                        help="Lo que el ganador puede cobrar de más que vos "
                             "gracias a Full, envío gratis o cuotas"),
                    "queda_al_precio_para_ganar": st.column_config.NumberColumn(
                        "Te quedaría", format="%.0f",
                        help="Por unidad, antes del costo de la mercadería"),
                    "palancas_sin_usar": "Te falta",
                    "palancas_activas": "Ya usás",
                    "competidores_primeros": "Rivales 1°",
                    "share_de_visitas": "Share visitas",
                    "unidades": "Unidades (período)",
                    "vendidas_historico": "Vendidas (histórico)",
                    "producto_catalogo": None})
            st.download_button("Descargar el análisis",
                               vbb.to_csv(index=False).encode("utf-8"),
                               f"buybox_{datetime.now():%Y%m%d}.csv",
                               "text/csv")
            st.caption(
                f"Los precios de los competidores se cachean "
                f"{buybox.VIGENCIA_HORAS} horas. Para forzar la relectura, "
                "volvé a apretar el botón después de ese plazo.")

            # ------------------------------------------- bajar precios solo
            st.divider()
            st.markdown("##### Bajar precios y seguir ganando plata")
            st.caption(
                "Con la planilla de costos, la herramienta calcula qué te "
                "quedaría vendiendo al precio del Buy Box y te deja aplicar "
                "la baja en lote, solo en las publicaciones donde el margen "
                "aguanta.")

            costos_bb = bloque_costos("bb")

            i1, i2 = st.columns([1.2, 3])
            iva_bb = i1.selectbox(
                "IVA a descontar", [0.21, 0.105, 0.0],
                format_func=lambda x: f"{x:.1%}" if x else "Sin descontar",
                key="iva_bb",
                help="La planilla de costos está SIN IVA y los precios de ML "
                     "lo incluyen, así que corresponde descontarlo.")

            otros_bb = controles_otros_conceptos("bb")

            if costos_bb is not None and i2.button("Calcular márgenes",
                                                   use_container_width=True):
                with st.spinner("Cruzando con los cargos reales..."):
                    st.session_state["buybox_costos"] = buybox.con_costos(
                        dbb, costos_bb, cargos_cacheados(ml), iva=iva_bb,
                        otros_conceptos=otros_bb,
                        precios_lista=precios_de_lista()[0])

            dcb = st.session_state.get("buybox_costos")
            if dcb is not None and len(dcb):
                rc = buybox.resumen_costos(dcb)
                w1, w2, w3, w4 = st.columns(4)
                w1.metric("Podés ganar y seguir ganando", rc["ganables"])
                w2.metric("Margen flaco", rc["flacas"])
                w3.metric("Ganar daría pérdida", rc["perdida"])
                w4.metric("Sin costo cargado", rc["sin_costo"])

                if rc.get("con_descuento_permitido"):
                    st.success(
                        f"**{rc['con_descuento_permitido']} publicaciones se "
                        f"ganan con el descuento permitido** (hasta "
                        f"{LP.DESCUENTO_PERMITIDO:.0%} sobre el precio de "
                        "lista). No hace falta republicar más barato: alcanza "
                        "con una promoción puntual. Mirá la columna *Cómo "
                        "ganarla*.", icon="🏷️")
                if rc.get("necesitan_mas_descuento"):
                    st.caption(
                        f"Otras {rc['necesitan_mas_descuento']} necesitarían "
                        f"más del {LP.DESCUENTO_PERMITIDO:.0%} permitido: ahí "
                        "el competidor está a un precio al que no se llega "
                        "sin romper la política de precios.")

                if rc["cruzan_escalon"]:
                    st.warning(
                        f"**{rc['cruzan_escalon']} publicaciones cruzarían un "
                        "escalón de cargo fijo al bajar.** MercadoLibre cobra "
                        "un porcentaje más un cargo fijo por unidad, y ese "
                        "cargo salta en escalones, y en \\$33.000 también "
                        "cambia quién paga el envío. Bajar de \\$34.000 a "
                        "\\$32.000 te suma \\$3.005 de cargo fijo pero te saca "
                        "~\\$7.641 de envío de encima: **suele convenir**. El "
                        "margen ya lo tiene en cuenta.", icon="🪜")

                st.markdown("###### Criterio para bajar")
                k1, k2, k3 = st.columns(3)
                # Los sliders van en PUNTOS PORCENTUALES enteros y se dividen
                # por 100 abajo. Con floats 0..1 y format printf, Streamlit
                # muestra "0%" en todo el recorrido: el printf no escala a
                # porcentaje, igual que en column_config.
                margen_min = k1.slider(
                    "Rentabilidad mínima aceptada",
                    int(buybox.PISO_DE_MARGEN * 100), 50, 10, 1,
                    format="%d%%", key="mg_bb",
                    help="En negativo aceptás vender a pérdida para ganar la "
                         f"página de catálogo. Piso duro del sistema: "
                         f"{buybox.PISO_DE_MARGEN:.0%}.") / 100
                baja_max = k2.slider(
                    "Baja máxima aceptada", 1,
                    int(buybox.TECHO_DE_BAJA * 100), 15, 1,
                    format="%d%%", key="bj_bb",
                    help=f"Tope duro del sistema: {buybox.TECHO_DE_BAJA:.0%}. "
                         "No se puede bajar más aunque el criterio lo permita."
                    ) / 100
                unid_min = k3.number_input("Unidades mínimas en el período",
                                           min_value=0, value=5, step=1,
                                           key="un_bb")

                j1, j2 = st.columns([2.4, 1.6])
                marcas_bb = j1.multiselect(
                    "Marcas (vacío = todas)",
                    sorted(m for m in dcb["marca"].dropna().unique() if m),
                    key="mk_bb")
                with j2:
                    st.write("")
                    cruzar = st.checkbox(
                        "Permitir las que cruzan escalón", value=False,
                        key="cr_bb",
                        help="Bajar de tramo puede sumar un cargo fijo que no "
                             "estaba. El margen ya lo contempla.")

                if margen_min < 0:
                    st.warning(
                        f"Estás aceptando vender **a pérdida de hasta "
                        f"{abs(margen_min):.0%}** con tal de ganar el Buy Box. "
                        "Puede tener sentido para entrar a una página de "
                        "catálogo o para liquidar, pero conviene mirarlo "
                        "publicación por publicación abajo.", icon="📉")

                sel_bb = buybox.seleccionar(
                    dcb, margen_minimo=margen_min, baja_maxima=baja_max,
                    unidades_minimas=unid_min,
                    permitir_cruzar_escalon=cruzar,
                    marcas=marcas_bb or None)

                st.markdown(cumplen(len(sel_bb)))

                if len(sel_bb):
                    st.caption(
                        "**Tildá filas para elegir a mano.** Si no seleccionás "
                        "ninguna se aplican todas las que cumplen el criterio.")
                    cols_sel = ["item_id", "sku", "marca", "titulo",
                                "precio_actual", "precio_para_ganar",
                                "bajar_pct", "margen_hoy", "margen_al_ganar",
                                "margen_al_ganar_pct", "unidades"]
                    # La lista puede no estar cargada: ahí estas dos no existen.
                    for extra in ("precio_sugerido", "como_ganarlo"):
                        if extra in sel_bb.columns:
                            cols_sel.insert(5, extra)
                    vista_sel = sel_bb[cols_sel]
                    evento = st.dataframe(
                        vista_sel, use_container_width=True, height=320,
                        hide_index=True, key="tabla_bb",
                        on_select="rerun", selection_mode="multi-row",
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "marca": "Marca", "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_sugerido": st.column_config.NumberColumn(
                                "Publicar a", format="%.0f",
                                help="El precio que dice la lista de Suprabond"),
                            "como_ganarlo": st.column_config.TextColumn(
                                "Cómo ganarla", width="medium"),
                            "precio_para_ganar": st.column_config.NumberColumn(
                                "Precio nuevo", format="%.0f"),
                            "bajar_pct": st.column_config.NumberColumn(
                                "Baja", format="percent"),
                            "margen_hoy": st.column_config.NumberColumn(
                                "Margen hoy", format="%.0f"),
                            "margen_al_ganar": st.column_config.NumberColumn(
                                "Margen nuevo", format="%.0f"),
                            "margen_al_ganar_pct": st.column_config.NumberColumn(
                                "Margen %", format="percent"),
                            "unidades": "Unidades"})

                    elegidas = list(getattr(evento.selection, "rows", []) or [])
                    a_aplicar = sel_bb.iloc[elegidas] if elegidas else sel_bb
                    if elegidas:
                        st.info(f"Vas a aplicar solo las **{len(a_aplicar)}** "
                                "que tildaste.", icon="👉")

                    con_perdida = int((a_aplicar["margen_al_ganar"] < 0).sum())
                    if con_perdida:
                        st.error(
                            f"**{con_perdida} de las {len(a_aplicar)} quedan a "
                            f"pérdida** al precio nuevo.", icon="📉")

                    st.divider()
                    st.error(
                        "**Esto cambia los precios en MercadoLibre de verdad.** "
                        "Cada cambio queda en la auditoría con el precio "
                        "anterior, así que se puede revertir a mano.",
                        icon="⚠️")
                    op_bb = st.text_input("Tu nombre (queda en el registro)",
                                          key="op_bb")
                    conf_bb = st.checkbox(
                        f"Confirmo que quiero bajar el precio de "
                        f"{len(a_aplicar)} publicaciones", key="conf_bb")
                    if st.button("Aplicar las bajas en MercadoLibre",
                                 key="go_bb",
                                 disabled=not (conf_bb and op_bb.strip())):
                        barra = st.progress(0.0, text="Aplicando...")
                        res_bb = buybox.aplicar(
                            ml, a_aplicar, operador=op_bb.strip(),
                            callback=lambda i, t, iid: barra.progress(
                                i / t, text=f"Aplicando {i} de {t}: {iid}"))
                        barra.empty()
                        ok = int((res_bb["resultado"] == "OK").sum())
                        if ok == len(res_bb):
                            st.success(f"{ok} precios actualizados.")
                        else:
                            st.error(f"{ok} aplicados, {len(res_bb) - ok} "
                                     "con error.")
                        st.dataframe(res_bb, use_container_width=True,
                                     hide_index=True)
                        # El cache quedo viejo: los precios cambiaron.
                        st.session_state.pop("buybox", None)
                        st.session_state.pop("buybox_costos", None)
                        st.caption("Volvé a correr el análisis para ver el "
                                   "estado nuevo del Buy Box.")
            elif dcb is not None:
                st.info("Ninguna publicación quedó con margen calculable. "
                        "Revisá que los SKU de la planilla coincidan con los "
                        "de MercadoLibre.")

    else:
        st.caption(
            "MercadoLibre le ofrece a cada publicación un menú de campañas. "
            "Cada oferta queda como **candidata** hasta que la tomás.")
        st.info(
            "**Lo primero que hay que mirar es el aporte de ML.** En algunos "
            "tipos MercadoLibre pone parte del descuento de su bolsillo: al "
            "comprador le baja el precio más de lo que te cuesta a vos. La "
            "campaña **¡Gánale a la competencia!** es la respuesta directa a "
            "las publicaciones donde perdés el Buy Box por precio — en vez de "
            "bajarlo vos solo, ML cofinancia la baja.", icon="💡")

        p1, p2 = st.columns([1.4, 3])
        tope_pr = p1.selectbox(
            "Alcance", [120, 300, 600],
            format_func=lambda t: f"Las {t} que más venden", key="tope_pr")
        p2.write("")
        if p2.button("Buscar promociones", use_container_width=True):
            estado = st.empty()
            with st.spinner("Consultando promociones publicación por publicación..."):
                cargos_pr = cargos_cacheados(ml)
                unidades_pr = dict(zip(cargos_pr["sku"],
                                       cargos_pr["unidades_vendidas"]))
                st.session_state["promos"] = promociones.analizar(
                    ml, pubs=pubs, tope=tope_pr, cargos=cargos_pr,
                    unidades=unidades_pr,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado_pr = st.session_state.get("promos")
        if guardado_pr is not None:
            dpr, camp = guardado_pr

            if len(camp):
                st.markdown("##### Campañas abiertas en la cuenta")
                st.dataframe(
                    camp, use_container_width=True, hide_index=True,
                    column_config={
                        "id": None, "tipo": None,
                        "nombre_tipo": "Tipo", "nombre": "Campaña",
                        "estado": "Estado", "desde": "Desde", "hasta": "Hasta",
                        "cierra_inscripcion": "Cierra inscripción"})

            if len(dpr):
                rp = promociones.resumen(dpr)
                q1, q2, q3, q4 = st.columns(4)
                q1.metric("Publicaciones con ofertas", rp["publicaciones"])
                q2.metric("Con aporte de ML", rp["con_aporte_ml"])
                q3.metric("Disponibles sin tomar", rp["disponibles"])
                q4.metric("Ya participás", rp["participando"])

                if rp["negativas"]:
                    st.warning(
                        f"**{rp['negativas']} ofertas dan negativo** ya antes "
                        "del costo de la mercadería: el precio de promoción no "
                        "cubre ni la comisión y el envío.", icon="⚠️")

                st.markdown("##### Por tipo de promoción")
                st.dataframe(
                    pd.DataFrame(rp["por_tipo"].most_common(),
                                 columns=["Promoción", "Ofertas"]),
                    use_container_width=True, hide_index=True, height=220)

                estados_pr = sorted(dpr["diagnostico"].unique())
                filtro_pr = st.multiselect(
                    "Filtrar por diagnóstico", estados_pr,
                    default=[e for e in estados_pr if e != "ya participás"]
                    or estados_pr)
                vpr = dpr[dpr["diagnostico"].isin(filtro_pr)] if filtro_pr else dpr

                st.dataframe(
                    vpr, use_container_width=True, height=420, hide_index=True,
                    column_config={
                        "item_id": "Publicación", "sku": "SKU",
                        "titulo": "Título", "campana_id": None, "tipo": None,
                        "promocion": "Promoción", "nombre": "Campaña",
                        "estado": None, "diagnostico": "Diagnóstico",
                        "precio_actual": st.column_config.NumberColumn(
                            "Tu precio", format="%.0f"),
                        "precio_promo": st.column_config.NumberColumn(
                            "Precio con promo", format="%.0f"),
                        "descuento": st.column_config.NumberColumn(
                            "Descuento", format="percent"),
                        "aporte_ml": st.column_config.NumberColumn(
                            "Pone ML", format="percent"),
                        "aporte_vendedor": st.column_config.NumberColumn(
                            "Ponés vos", format="percent"),
                        "queda_por_unidad": st.column_config.NumberColumn(
                            "Te queda", format="%.0f",
                            help="Por unidad, antes del costo de la mercadería"),
                        "unidades": "Unidades (período)",
                        "vendidas_historico": "Vendidas (histórico)",
                        "desde": "Desde", "hasta": "Hasta"})
                st.download_button("Descargar las promociones",
                                   vpr.to_csv(index=False).encode("utf-8"),
                                   f"promociones_{datetime.now():%Y%m%d}.csv",
                                   "text/csv")
                # ------------------------------------------ alta automatica
                st.divider()
                st.markdown("##### Alta automática por criterio")
                st.caption(
                    "Definís una regla una vez y la herramienta selecciona "
                    "sola qué publicaciones sumar. El alta se aplica en lote "
                    "después de que la revises.")

                r1, r2, r3 = st.columns(3)
                # En puntos porcentuales enteros: ver la nota en Buy Box.
                ap_max = r1.slider("CRAFTERS pone como máximo",
                                   0, 30, 5, 1, format="%d%%",
                                   key="ap_max") / 100
                ml_min = r2.slider("MercadoLibre pone al menos",
                                   0, 30, 0, 1, format="%d%%",
                                   key="ml_min") / 100
                un_min_pr = r3.number_input("Unidades mínimas en el período",
                                            min_value=0, value=1, step=1,
                                            key="un_pr")
                ml_super = st.checkbox(
                    "Exigir que MercadoLibre ponga más que CRAFTERS",
                    value=True, key="ml_sup")

                sel_pr = promociones.seleccionar(
                    dpr, aporte_crafters_max=ap_max,
                    ml_debe_superar=ml_super, aporte_ml_min=ml_min,
                    unidades_minimas=un_min_pr)

                st.markdown(cumplen(len(sel_pr)))
                st.caption(
                    "Solo entran ofertas **disponibles y sin tomar**. Si una "
                    "publicación califica para varias promociones se toma la "
                    "que deja más plata por unidad: sumarla a todas sería "
                    "pisar una con otra.")

                if len(sel_pr):
                    st.dataframe(
                        sel_pr[["item_id", "sku", "titulo", "promocion",
                                "nombre", "precio_actual", "precio_promo",
                                "aporte_ml", "aporte_vendedor",
                                "queda_por_unidad", "unidades"]],
                        use_container_width=True, height=300, hide_index=True,
                        column_config={
                            "item_id": "Publicación", "sku": "SKU",
                            "titulo": "Título", "promocion": "Promoción",
                            "nombre": "Campaña",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_promo": st.column_config.NumberColumn(
                                "Precio con promo", format="%.0f"),
                            "aporte_ml": st.column_config.NumberColumn(
                                "Pone ML", format="percent"),
                            "aporte_vendedor": st.column_config.NumberColumn(
                                "Ponés vos", format="percent"),
                            "queda_por_unidad": st.column_config.NumberColumn(
                                "Te queda", format="%.0f"),
                            "unidades": "Unidades"})

                    st.divider()
                    st.error(
                        "**Esto suma las publicaciones a la promoción en "
                        "MercadoLibre de verdad**, o sea que cambia el precio "
                        "que ve el comprador. Queda registrado en la "
                        "auditoría.", icon="⚠️")
                    op_pr = st.text_input("Tu nombre (queda en el registro)",
                                          key="op_pr")
                    conf_pr = st.checkbox(
                        f"Confirmo que quiero sumar {len(sel_pr)} "
                        "publicaciones a su promoción", key="conf_pr")
                    if st.button("Sumar a las promociones", key="go_pr",
                                 disabled=not (conf_pr and op_pr.strip())):
                        barra = st.progress(0.0, text="Dando de alta...")
                        res_pr = promociones.aplicar(
                            ml, sel_pr, operador=op_pr.strip(),
                            callback=lambda i, t, iid: barra.progress(
                                i / t, text=f"Alta {i} de {t}: {iid}"))
                        barra.empty()
                        ok = int((res_pr["resultado"] == "OK").sum())
                        if ok == len(res_pr):
                            st.success(f"{ok} publicaciones sumadas.")
                        else:
                            st.error(f"{ok} sumadas, {len(res_pr) - ok} "
                                     "con error.")
                        st.dataframe(res_pr, use_container_width=True,
                                     hide_index=True)
                        st.session_state.pop("promos", None)
                        st.caption("Volvé a buscar promociones para ver el "
                                   "estado nuevo.")

                st.caption(
                    "También podés tomarlas a mano desde el panel de "
                    "MercadoLibre; esta sección no reemplaza ese camino.")
            else:
                st.info("Ninguna publicación del alcance elegido tiene "
                        "ofertas disponibles.")

elif seccion == "Precios":
    bloque_carga("precio")

elif seccion == "Mayoristas":
    st.markdown("#### Precios mayoristas por reglas")
    st.caption(
        "Define descuentos por cantidad con reglas por familia, por SKU o "
        "generales. La herramienta toma el precio publicado de cada item y "
        "arma los tramos automáticamente.")

    st.caption(
        "Los tramos se cargan como **precio mayorista exclusivo para negocios**, "
        "igual que desde el panel de MercadoLibre.")

    sub = st.radio("Vista", ["Simulación", "Reglas"], horizontal=True,
                   label_visibility="collapsed")

    if sub == "Reglas":
        st.caption(
            "Gana la regla de **menor orden**, así que lo específico pisa a lo "
            "general. Los criterios son: `sku` (código exacto), `familia` "
            "(código dentro del SKU, ej. CDB), `categoria` (texto de la "
            "categoría de ML), `titulo` y `general`. Separá varios valores "
            "con `|`. Se editan en la hoja "
            f"`{mayoristas.HOJA_REGLAS}` de la planilla.")
        regs = pd.DataFrame(almacen.leer_hoja(mayoristas.HOJA_REGLAS,
                                              mayoristas.COLS_REGLAS))
        if not len(regs):
            regs = pd.DataFrame(mayoristas.REGLAS_INICIALES)
        st.dataframe(regs, use_container_width=True, height=460)

    else:
        if st.button("Simular precios mayoristas"):
            with st.spinner("Aplicando las reglas al catálogo..."):
                st.session_state["may"] = mayoristas.simular(pubs)

        sim = st.session_state.get("may")
        if sim is not None and len(sim):
            aplicables = sim[sim["accion"] == "aplicar"]
            m1, m2, m3 = st.columns(3)
            m1.metric("Publicaciones alcanzadas", len(sim))
            m2.metric("Con tramos calculados", len(aplicables))
            m3.metric("Sin regla o sin tramos", len(sim) - len(aplicables))

            st.markdown("##### Cuántas publicaciones toma cada regla")
            st.dataframe(sim["regla"].value_counts().rename_axis("Regla")
                         .reset_index(name="Publicaciones"),
                         use_container_width=True, height=220)

            filtro = st.multiselect("Filtrar por regla",
                                    sorted(sim["regla"].unique()),
                                    default=sorted(sim["regla"].unique()))
            vista_m = sim[sim["regla"].isin(filtro)]

            st.dataframe(
                vista_m, use_container_width=True, height=380,
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "regla": "Regla",
                    "precio": st.column_config.NumberColumn("Precio", format="%.0f"),
                    "q1_unidades": "Desde (Q1)",
                    "q1_precio": st.column_config.NumberColumn("Precio Q1", format="%.0f"),
                    "q2_unidades": "Desde (Q2)",
                    "q2_precio": st.column_config.NumberColumn("Precio Q2", format="%.0f"),
                    "accion": "Acción", "motivo": "Motivo"})

            st.download_button("Descargar la simulación",
                               vista_m.to_csv(index=False).encode("utf-8"),
                               f"mayoristas_{datetime.now():%Y%m%d_%H%M}.csv",
                               "text/csv")

            st.divider()
            op_may = st.text_input("Tu nombre (queda en el registro)", key="op_may")
            conf_may = st.checkbox(
                f"Confirmo que quiero cargar los tramos en {len(aplicables)} "
                "publicaciones", key="conf_may")
            # Las hechas se leen del DISCO, no del session_state: si la sesión
            # se cortó, el session_state se fue con ella.
            ya_hechas = mayoristas.ya_aplicadas()
            faltan = [i for i in aplicables["item_id"] if i not in ya_hechas]
            if ya_hechas:
                st.info(
                    f"De corridas anteriores quedaron **{len(ya_hechas)} "
                    f"publicaciones ya cargadas**. Faltan **{len(faltan)}**, "
                    "y se retoma desde ahí.", icon="↩️")
                if st.button("Empezar de cero (olvidar lo hecho)",
                             key="reset_may"):
                    mayoristas.olvidar_aplicadas()
                    st.rerun()

            # De a tandas, y no todo junto: una corrida de 2.500 publicaciones
            # tarda más de una hora y la sesión de Streamlit se corta mucho
            # antes. Cada tanda termina, se guarda y vuelve a pintar la
            # pantalla, así la conexión no se queda esperando en silencio.
            #
            # 150 son unos 2 minutos y medio: entra cómodo antes de que la
            # conexión se caiga, y evita tener que apretar 60 veces.
            POR_TANDA = st.select_slider(
                "Cuántas por tanda", [50, 150, 300, 500], value=150,
                key="tanda_may",
                help="Más grandes van más rápido pero, si se corta la sesión, "
                     "se pierde el progreso de la tanda en curso. Lo ya "
                     "aplicado nunca se pierde.")

            if st.button(f"Aplicar las próximas {min(POR_TANDA, len(faltan))} "
                         f"(faltan {len(faltan)})", key="go_may",
                         disabled=not (conf_may and op_may.strip() and faltan)):
                barra = st.progress(0.0, text="Aplicando...")
                res = mayoristas.aplicar(
                    ml, sim, operador=op_may.strip(), tope=POR_TANDA,
                    callback=lambda i, t, f: barra.progress(
                        i / t, text=f"Aplicando {i} de {t}..."))
                barra.empty()
                previo = st.session_state.get("may_res")
                st.session_state["may_res"] = (
                    pd.concat([previo, res]) if previo is not None
                    and len(previo) else res)
                st.rerun()

            if not faltan and ya_hechas:
                st.success(f"Están las {len(ya_hechas)}. No queda ninguna.")

            res = st.session_state.get("may_res")
            if res is not None and len(res):
                ok = int((res["resultado"] == "OK").sum())
                fallaron = res[res["resultado"] != "OK"]
                if not len(fallaron):
                    st.success(f"{ok} publicaciones con precio mayorista "
                               "cargado.")
                else:
                    st.error(f"{ok} cargadas, {len(fallaron)} con error.")
                    motivos = fallaron["detalle"].str.slice(0, 60).value_counts()
                    st.markdown("**Por qué fallaron**")
                    st.dataframe(
                        motivos.rename_axis("Motivo").reset_index(
                            name="Publicaciones"),
                        use_container_width=True, hide_index=True, height=160)
                    if st.button(f"Reintentar las {len(fallaron)} que fallaron",
                                 key="retry_may"):
                        barra = st.progress(0.0, text="Reintentando...")
                        res2 = mayoristas.aplicar(
                            ml, sim[sim["item_id"].isin(fallaron["item_id"])],
                            operador=op_may.strip() or "reintento",
                            callback=lambda i, t, f: barra.progress(
                                i / t, text=f"Reintentando {i} de {t}..."))
                        barra.empty()
                        st.session_state["may_res"] = pd.concat(
                            [res[res["resultado"] == "OK"], res2])
                        # Las que salieron bien ya quedaron anotadas en disco
                        # por `aplicar`: acá no hace falta recordarlas.
                        st.rerun()

                st.dataframe(res, use_container_width=True, height=260)
                st.caption(
                    "Los tramos tardan unos segundos en verse en la publicación. "
                    "El editor de MercadoLibre los muestra en el bloque "
                    "*Precios mayoristas*.")

elif seccion == "Stock ML":
    bloque_carga("stock")

elif seccion == "Control de stock":
    st.markdown("#### Control de stock")
    st.caption(
        "Lleva la cuenta de tus unidades a partir de un stock inicial, "
        "descontando las ventas de MercadoLibre. **No modifica el stock de "
        "MercadoLibre**: es solo control interno con historial.")

    vista = st.radio("Vista", ["Stock actual", "Movimientos", "Ingresos",
                               "Devoluciones", "Cargar stock inicial"],
                     horizontal=True, label_visibility="collapsed")

    # ---------------------------------------------------------- stock actual
    if vista == "Stock actual":
        c1, c2, c3 = st.columns([1.4, 1.4, 2])
        with c1:
            dias_sync = st.selectbox("Revisar últimos", [1, 3, 7, 15, 30],
                                     index=2, format_func=lambda d: f"{d} días")
        with c2:
            st.write("")
            sincronizar = st.button("↻ Sincronizar ventas", use_container_width=True)

        if sincronizar:
            estado = st.empty()
            with st.spinner("Leyendo ventas de MercadoLibre..."):
                r = stock_control.sincronizar(
                    ml, dias=dias_sync, operador="app",
                    callback=lambda m: estado.caption(m))
            estado.empty()
            if r["ok"]:
                if r["movimientos_nuevos"]:
                    st.success(
                        f"{r['ventas']} ventas y {r['cancelaciones']} cancelaciones "
                        f"nuevas ({r['unidades']:.0f} unidades) sobre "
                        f"{r['ordenes_revisadas']} órdenes revisadas.")
                else:
                    st.info(f"Sin novedades: las {r['ordenes_revisadas']} órdenes "
                            "del período ya estaban registradas.")
            else:
                st.error(f"No se pudo guardar: {r['detalle']}")
            st.session_state.pop("stock_df", None)

        if "stock_df" not in st.session_state:
            with st.spinner("Calculando stock..."):
                st.session_state["stock_df"] = stock_control.stock_actual()
        df = st.session_state["stock_df"]

        if not len(df):
            st.info("Todavía no hay movimientos. Cargá el stock inicial y "
                    "después sincronizá las ventas.")
        else:
            negativos = df[df["stock_actual"] < 0]
            m1, m2, m3 = st.columns(3)
            m1.metric("SKU con seguimiento", len(df))
            m2.metric("Unidades en stock", f"{df['stock_actual'].sum():,.0f}"
                      .replace(",", "."))
            m3.metric("SKU en negativo", len(negativos))

            if len(negativos):
                st.warning(
                    f"**{len(negativos)} SKU dan negativo.** Normalmente es "
                    "porque falta cargar su stock inicial, o porque entró "
                    "mercadería que no se registró en Ingresos.", icon="⚠️")

            solo_neg = st.checkbox("Ver solo los negativos")
            st.dataframe(df[df["stock_actual"] < 0] if solo_neg else df,
                         use_container_width=True, height=420)
            st.download_button("Descargar el stock",
                               df.to_csv(index=False).encode("utf-8"),
                               f"stock_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv")

    # ---------------------------------------------------------- movimientos
    elif vista == "Movimientos":
        movs = pd.DataFrame(stock_control.movimientos())
        if not len(movs):
            st.info("Todavía no hay movimientos registrados.")
        else:
            f1, f2 = st.columns([2, 2])
            with f1:
                tipos = sorted(movs["tipo"].unique())
                sel = st.multiselect("Tipo", tipos, default=tipos)
            with f2:
                buscar = st.text_input("Buscar SKU").strip().upper()

            vista_m = movs[movs["tipo"].isin(sel)]
            if buscar:
                vista_m = vista_m[vista_m["sku"].str.contains(buscar, na=False)]

            st.caption(f"{len(vista_m)} movimientos")
            st.dataframe(vista_m.iloc[::-1], use_container_width=True, height=440)
            st.download_button("Descargar el historial",
                               vista_m.to_csv(index=False).encode("utf-8"),
                               f"movimientos_{datetime.now():%Y%m%d_%H%M}.csv",
                               "text/csv")

    # ---------------------------------------------------------- ingresos
    elif vista == "Ingresos":
        st.markdown("##### Cargar mercadería que entra")
        st.caption("Compras a proveedores, o ajustes cuando el conteo físico "
                   "no coincide con el sistema.")

        with st.form("form_ingreso"):
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                sku_in = st.text_input("SKU")
            with c2:
                cant_in = st.number_input("Cantidad", step=1.0, value=1.0)
            with c3:
                tipo_in = st.selectbox("Tipo", ["compra", "ajuste"])
            c4, c5 = st.columns(2)
            with c4:
                ref_in = st.text_input("Remito / factura (opcional)")
            with c5:
                op_in = st.text_input("Tu nombre")
            nota_in = st.text_input("Nota (opcional)")

            if st.form_submit_button("Registrar"):
                if not sku_in.strip() or not op_in.strip():
                    st.error("Hacen falta el SKU y tu nombre.")
                elif tipo_in == "compra" and cant_in <= 0:
                    st.error("Una compra tiene que sumar unidades. "
                             "Para restar, usá 'ajuste'.")
                else:
                    ok, det = stock_control.registrar(
                        tipo_in, sku_in, cant_in, referencia=ref_in,
                        detalle=nota_in, operador=op_in.strip())
                    if ok:
                        st.success(f"Registrado: {cant_in:+.0f} de "
                                   f"{sku_in.strip().upper()}")
                        st.session_state.pop("stock_df", None)
                    else:
                        st.error(f"No se pudo registrar: {det}")

        st.divider()
        st.caption("Últimos ingresos cargados")
        movs = pd.DataFrame(stock_control.movimientos())
        if len(movs):
            ing = movs[movs["tipo"].isin(["compra", "ajuste", "devolucion_apta"])]
            st.dataframe(ing.iloc[::-1].head(25), use_container_width=True)

    # ---------------------------------------------------------- devoluciones
    elif vista == "Devoluciones":
        st.caption(
            "Las devoluciones **no vuelven solas al stock**. Cada una espera "
            "acá hasta que alguien confirme si la unidad está apta para "
            "venderse de nuevo.")

        if st.button("↻ Buscar devoluciones nuevas"):
            with st.spinner("Consultando reclamos en MercadoLibre..."):
                r = stock_control.sincronizar_devoluciones(ml, operador="app")
            if r["ok"]:
                st.success(f"{r['nuevas']} devoluciones nuevas en la bandeja."
                           if r["nuevas"] else "Sin devoluciones nuevas.")
            else:
                st.error(f"No se pudo consultar: {r['detalle']}")

        devs = pd.DataFrame(stock_control.devoluciones())
        if not len(devs):
            st.info("No hay devoluciones registradas.")
        else:
            pendientes = devs[devs["resolucion"] == "pendiente"]
            st.metric("Pendientes de revisar", len(pendientes))

            if len(pendientes):
                st.markdown("##### Resolver una devolución")
                with st.form("form_dev"):
                    ids = pendientes["id_dev"].astype(str).tolist()
                    c1, c2 = st.columns([2, 2])
                    with c1:
                        id_sel = st.selectbox("Devolución", ids)
                        sku_dev = st.text_input(
                            "SKU de la unidad devuelta",
                            help="MercadoLibre no siempre informa el SKU en el "
                                 "reclamo: verificalo en la orden.")
                    with c2:
                        res_sel = st.selectbox(
                            "¿Vuelve al stock?",
                            ["apta", "descarte"],
                            format_func=lambda x: ("Sí, está apta para vender"
                                                   if x == "apta"
                                                   else "No, se descarta"))
                        cant_dev = st.number_input("Unidades", step=1.0, value=1.0)
                    op_dev = st.text_input("Tu nombre")

                    if st.form_submit_button("Guardar resolución"):
                        if res_sel == "apta" and not sku_dev.strip():
                            st.error("Para devolver al stock hace falta el SKU.")
                        elif not op_dev.strip():
                            st.error("Poné tu nombre.")
                        else:
                            ok, det = stock_control.resolver_devolucion(
                                id_sel, res_sel, sku=sku_dev, cantidad=cant_dev,
                                operador=op_dev.strip())
                            if ok:
                                st.success("Resolución guardada." + (
                                    f" {cant_dev:.0f} unidad/es volvieron al stock."
                                    if res_sel == "apta" else ""))
                                st.session_state.pop("stock_df", None)
                            else:
                                st.error(f"No se pudo guardar: {det}")

            st.dataframe(devs.iloc[::-1], use_container_width=True, height=300)

    # ---------------------------------------------------------- stock inicial
    elif vista == "Cargar stock inicial":
        st.caption(
            "El punto de partida del conteo. Subí una planilla con **SKU** y "
            "**cantidad**. Se puede cargar de nuevo más adelante: cada carga "
            "se suma a la anterior, así que sirve también para corregir.")

        arch = st.file_uploader("Planilla de stock inicial", type=["xlsx", "xls", "csv"],
                                key="up_stock_ini")
        if arch:
            try:
                df_ini = act.leer_planilla(arch)
                cols = list(df_ini.columns)
                c1, c2 = st.columns(2)
                with c1:
                    col_sku = st.selectbox("Columna de SKU", cols)
                with c2:
                    col_cant = st.selectbox("Columna de cantidad", cols,
                                            index=min(1, len(cols) - 1))
                op_ini = st.text_input("Tu nombre", key="op_ini")

                previa = pd.DataFrame({
                    "sku": df_ini[col_sku].astype(str).str.strip().str.upper(),
                    "cantidad": df_ini[col_cant].map(act._a_numero)}).dropna()
                previa = previa[previa["sku"].ne("") & previa["sku"].ne("NAN")]

                st.caption(f"{len(previa)} filas listas para cargar")
                st.dataframe(previa.head(30), use_container_width=True)

                if st.button("Cargar stock inicial",
                             disabled=not op_ini.strip() or not len(previa)):
                    ok, det = stock_control.cargar_stock_inicial(
                        previa.to_dict("records"), operador=op_ini.strip())
                    if ok:
                        st.success(f"{len(previa)} SKU cargados.")
                        st.session_state.pop("stock_df", None)
                    else:
                        st.error(f"No se pudo cargar: {det}")
            except Exception as e:
                st.error(f"No pude leer la planilla: {e}")

elif seccion == "Precio óptimo":
    st.markdown("#### Ventana de precio")
    _, _, _cuando_lp, _n_lp = precios_de_lista()
    aviso_lista(_n_lp, _cuando_lp)
    st.caption(
        "Junta las tres cuentas que hasta ahora estaban separadas: el **piso** "
        "(abajo no llegás al margen), el **techo útil** (arriba perdés la "
        "página de catálogo) y el **escalón de cargo fijo** (dentro de la "
        "ventana no todos los precios rinden igual). Devuelve un precio "
        "sugerido por SKU, con el motivo.")

    with st.expander("Los seis casos y por qué piden cosas distintas"):
        st.markdown(
            "- **Ventana amplia** — podés acomodar el precio *y* quedarte con "
            "la página. Es el único caso donde no se resigna nada.\n"
            "- **Bajar para ganar** — ganar la página exige bajar. El margen "
            "lo aguanta, pero se resigna neto por unidad: **solo conviene si "
            "el volumen extra lo compensa**, y eso no sale de ningún dato de "
            "la API. Por eso no entra en la selección automática.\n"
            "- **Sin ventana** — ganar la página exige vender por debajo de "
            "tu piso. No es problema de precio sino de costo, o de contra "
            "quién te estás midiendo.\n"
            "- **Ya ganás** — tenés la página; lo único a mirar es si podés "
            "acomodar el precio sin perderla.\n"
            "- **Catálogo en otra publicación** — la página la pelea otra "
            "publicación del mismo SKU, que este cambio de precio **no "
            "toca**. El Buy Box de esas se resuelve en *Ganar la venta*.\n"
            "- **Fuera de catálogo** — no hay página que ganar, manda el "
            "piso.")

    costos_vt = bloque_costos("vt")
    otros_vt = controles_otros_conceptos("vt")

    t1, t2, t3 = st.columns(3)
    iva_vt = t1.selectbox(
        "IVA a descontar", [0.21, 0.105, 0.0],
        format_func=lambda x: f"{x:.1%}" if x else "Sin descontar", key="iva_vt")
    objetivo_vt = t2.slider(
        "Margen objetivo", 0, 40, 15, 1, format="%d%%", key="obj_vt",
        help="Alimenta el cálculo: si lo cambiás hay que volver a apretar "
             "«Calcular». El resto de los filtros se aplican al instante."
        ) / 100
    t3.write("")
    if costos_vt is not None and t3.button("Calcular la ventana",
                                           use_container_width=True):
        estado = st.empty()
        with st.spinner("Cruzando piso, Buy Box y escalones..."):
            cat_ids = [p["id"] for p in pubs
                       if p.get("status") == "active"
                       and p.get("catalog_listing")]
            ptw = buybox.traer_price_to_win(
                ml, cat_ids, callback=lambda m: estado.caption(str(m)))
            st.session_state["vent"] = ventana.analizar(
                costos_vt, cargos_cacheados(ml), pubs, iva=iva_vt,
                otros_conceptos=otros_vt, objetivo=objetivo_vt,
                ptw_por_item=ptw, precios_lista=precios_de_lista()[0])
        estado.empty()
    st.caption(
        f"La primera corrida consulta el Buy Box de cada publicación de "
        f"catálogo y tarda unos minutos; después se cachea "
        f"{buybox.VIGENCIA_HORAS} horas.")

    dvt = st.session_state.get("vent")
    if dvt is not None and len(dvt):
        rv = ventana.resumen(dvt)
        c1, c2, c3 = st.columns(3)
        c1.metric("Ventana amplia", rv["ventana_amplia"])
        c2.metric("Bajar para ganar", rv["bajar_para_ganar"])
        c3.metric("Sin ventana", rv["sin_ventana"])
        c4, c5, c6 = st.columns(3)
        c4.metric("Ya ganás la página", rv["ya_ganan"])
        c5.metric("Catálogo en otra pub.", rv["catalogo_aparte"])
        c6.metric("Fuera de catálogo", rv["fuera"])

        st.metric("Impacto de los que mejoran", pesos(rv["impacto"]))
        st.caption(
            "El impacto asume **el mismo volumen** que el período medido. "
            "Cambiar el precio cambia el volumen, así que es una referencia "
            "de tamaño, no una proyección.")

        if rv["cruzan_escalon"]:
            st.info(
                f"**{rv['cruzan_escalon']} sugerencias cruzan un escalón de "
                "cargo fijo.** Ojo con el de \\$33.000: ahí el cargo fijo se "
                "hace cero, pero el envío pasa a pagarlo el vendedor "
                "(~\\$7.641), así que lo que conviene es quedarse **debajo**, "
                "no cruzarlo hacia arriba.", icon="🪜")

        st.markdown("###### Criterio")
        d1, d2 = st.columns(2)
        cambio_max = d1.slider("Cambio máximo de precio", 1, 100, 20, 1,
                               format="%d%%", key="cm_vt") / 100
        unid_vt = d2.number_input("Unidades mínimas en el período",
                                  min_value=0, value=5, step=1, key="un_vt")
        e1, e2 = st.columns([2.4, 1.6])
        casos_vt = e1.multiselect(
            "Casos a incluir", sorted(dvt["caso"].unique()),
            default=[c for c in sorted(dvt["caso"].unique())
                     if c != "bajar para ganar"],
            key="cs_vt",
            help="«Bajar para ganar» queda afuera por defecto: resigna neto "
                 "por unidad y solo conviene si el volumen lo paga.")
        marcas_vt = e2.multiselect(
            "Marcas (vacío = todas)",
            sorted(m for m in dvt["marca"].dropna().unique() if m),
            key="mk_vt")

        sel_vt = ventana.seleccionar(
            dvt, casos=casos_vt or None, cambio_maximo=cambio_max,
            unidades_minimas=unid_vt, marcas=marcas_vt or None)
        st.markdown(cumplen(len(sel_vt)))

        if len(sel_vt):
            st.caption("**Tildá filas para elegir a mano.** Si no seleccionás "
                       "ninguna van todas las que cumplen.")
            ev_vt = st.dataframe(
                sel_vt[["sku", "marca", "titulo", "caso", "precio_actual",
                        "precio_publicacion", "piso", "precio_para_ganar",
                        "precio_sugerido", "como_ganarlo",
                        "cambio_pct", "neto_actual", "neto_sugerido",
                        "impacto_periodo", "unidades", "cruza_escalon"]],
                use_container_width=True, height=360, hide_index=True,
                key="tabla_vt", on_select="rerun", selection_mode="multi-row",
                column_config={
                    "sku": "SKU", "marca": "Marca", "titulo": "Título",
                    "caso": "Caso",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_publicacion": st.column_config.NumberColumn(
                        "Publicar a", format="%.0f",
                        help="El precio que dice la lista de Suprabond"),
                    "como_ganarlo": st.column_config.TextColumn(
                        "Cómo ganar la página", width="medium"),
                    "piso": st.column_config.NumberColumn(
                        "Piso", format="%.0f",
                        help="Abajo de acá no llegás al margen objetivo"),
                    "precio_para_ganar": st.column_config.NumberColumn(
                        "Para ganar", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Sugerido", format="%.0f"),
                    "cambio_pct": st.column_config.NumberColumn(
                        "Cambio", format="percent"),
                    "neto_actual": st.column_config.NumberColumn(
                        "Neto hoy", format="%.0f"),
                    "neto_sugerido": st.column_config.NumberColumn(
                        "Neto sugerido", format="%.0f"),
                    "impacto_periodo": st.column_config.NumberColumn(
                        "Impacto", format="%.0f"),
                    "unidades": "Unidades",
                    "cruza_escalon": st.column_config.CheckboxColumn(
                        "Cruza escalón")})

            elegidas_vt = list(getattr(ev_vt.selection, "rows", []) or [])
            aplicar_vt = sel_vt.iloc[elegidas_vt] if elegidas_vt else sel_vt
            if elegidas_vt:
                st.info(f"Vas a aplicar solo las **{len(aplicar_vt)}** que "
                        "tildaste.", icon="👉")

            with st.expander("Ver el motivo de cada sugerencia"):
                for _, f in aplicar_vt.head(20).iterrows():
                    st.markdown(f"**{f['sku']}** · {f['caso']} · "
                                f"{pesos_md(f['precio_actual'])} → "
                                f"{pesos_md(f['precio_sugerido'])}")
                    st.caption(f["motivo"])

            st.divider()
            st.warning(
                "**Cambiar precios cambia lo que ve el comprador.** El "
                "cálculo dice qué precio te conviene según tus costos y la "
                "competencia de catálogo, **no si el mercado lo va a "
                "pagar**.", icon="⚠️")

            if st.button("Simular el cambio de precios", key="sim_vt"):
                st.session_state["vent_sim"] = act.simular(
                    ventana.planilla_de_precios(aplicar_vt), pubs, "precio",
                    col_clave="sku", col_valor="precio")

            sim_vt = st.session_state.get("vent_sim")
            if sim_vt is not None and len(sim_vt):
                revisar = int((sim_vt["accion"] == "revisar").sum())
                f1, f2 = st.columns(2)
                f1.metric("Listas para aplicar",
                          int((sim_vt["accion"] == "actualizar").sum()))
                f2.metric("Marcadas para revisar", revisar)
                if revisar:
                    st.error(
                        f"**{revisar} superan el "
                        f"{act.UMBRAL_ALERTA_PRECIO:.0%} de variación** y no "
                        "se aplican salvo que lo pidas aparte.", icon="🛑")
                st.dataframe(sim_vt, use_container_width=True, height=280,
                             hide_index=True)

                op_vt = st.text_input("Tu nombre (queda en el registro)",
                                      key="op_vt")
                inc_vt = st.checkbox("Incluir también las marcadas para "
                                     "revisar", key="rev_vt")
                conf_vt = st.checkbox(
                    "Confirmo que quiero cambiar estos precios en "
                    "MercadoLibre", key="conf_vt")
                if st.button("Aplicar en MercadoLibre", key="go_vt",
                             disabled=not (conf_vt and op_vt.strip())):
                    barra = st.progress(0.0, text="Aplicando...")
                    res_vt = act.aplicar(
                        ml, sim_vt, "precio", operador=op_vt.strip(),
                        incluir_revisar=inc_vt,
                        callback=lambda i, t, f: barra.progress(
                            i / t, text=f"Aplicando {i} de {t}..."))
                    barra.empty()
                    ok = int((res_vt["resultado"] == "OK").sum())
                    if ok == len(res_vt):
                        st.success(f"{ok} precios actualizados.")
                    else:
                        st.error(f"{ok} aplicados, {len(res_vt) - ok} con error.")
                    st.dataframe(res_vt, use_container_width=True,
                                 hide_index=True)
                    st.session_state.pop("vent", None)
                    st.session_state.pop("vent_sim", None)

        st.download_button(
            "Descargar el análisis completo",
            dvt.to_csv(index=False).encode("utf-8"),
            f"ventana_{datetime.now():%Y%m%d}.csv", "text/csv")

elif seccion == "Competencia":
    _, _, _cuando_c, _n_c = precios_de_lista()
    aviso_lista(_n_c, _cuando_c)
    st.markdown("#### Mejor precio de la competencia por EAN")
    st.caption(
        "Subí una planilla con los **EAN** (códigos de barras) y te dice quién "
        "vende más barato cada producto, a cuánto, y en qué posición estamos.")

    with st.expander("Qué alcance tiene esta búsqueda"):
        st.markdown(
            "MercadoLibre **cerró el buscador libre** para aplicaciones, así que "
            "la búsqueda va por el **catálogo**: vemos a todos los que venden ese "
            "producto de catálogo.\n\n"
            "- Si alguien publica el producto **por fuera del catálogo**, no "
            "aparece.\n"
            "- Si el EAN no tiene producto de catálogo, se reporta como "
            "`sin_catalogo`.\n\n"
            "Antes de reaccionar a una diferencia grande, conviene abrir la "
            "publicación del competidor: puede tratarse de otra presentación "
            "(unidad contra pack) aunque comparta el catálogo.")

    st.markdown("##### Tus más vendidos")
    st.caption(
        "Toma los artículos que más vendiste en el período, busca su código de "
        "barras y compara contra el catálogo. No hace falta mantener ninguna "
        "planilla: sale de tus ventas reales.")

    t1, t2, t3 = st.columns([1.1, 1.1, 2])
    cuantos = t1.selectbox("Cuántos", [20, 50, 100], index=1)
    dias_top = t2.selectbox("Período", [30, 60, 90], index=0,
                            format_func=lambda d: f"{d} días")
    if t3.button(f"Comparar mis {cuantos} más vendidos",
                 use_container_width=True):
        estado = st.empty()
        with st.spinner("Buscando tus más vendidos..."):
            eans_top, detalle, df_top = competencia.eans_mas_vendidos(
                ml, n=cuantos, dias=dias_top,
                callback=lambda m: estado.caption(str(m)))
        estado.caption(detalle)
        if eans_top:
            barra = st.progress(0.0, text="Consultando la competencia...")
            st.session_state["comp"] = competencia.analizar(
                ml, eans_top,
                callback=lambda i, t_, e: barra.progress(
                    i / t_, text=f"Consultando {i} de {t_}..."),
                precios_lista=precios_de_lista()[1])
            barra.empty()
            st.session_state["comp_detalle"] = detalle
            ok_h, det_h = competencia.guardar_comparacion(
                st.session_state["comp"], origen="mas_vendidos")
            st.session_state["comp_guardado"] = (ok_h, det_h)
        else:
            st.warning("Ninguno de tus más vendidos tiene código de barras "
                       "cargado. Sin EAN no se pueden buscar en el catálogo.")

    if st.session_state.get("comp_detalle"):
        st.caption(st.session_state["comp_detalle"])

    st.divider()
    st.markdown("##### O subí una planilla")
    arch_ean = st.file_uploader("Planilla con EAN (.xlsx o .csv)",
                               type=["xlsx", "xls", "csv"], key="up_ean")
    if arch_ean:
        try:
            eans, col_detectada = competencia.leer_planilla_eans(arch_ean)
            st.caption(f"Columna detectada: **{col_detectada}** · "
                       f"{len(eans)} EAN únicos")
        except Exception as e:
            st.error(f"No pude leer la planilla: {e}")
            eans = []

        if eans and st.button(f"Buscar precios de {len(eans)} EAN"):
            barra = st.progress(0.0, text="Consultando MercadoLibre...")
            st.session_state["comp"] = competencia.analizar(
                ml, eans,
                callback=lambda i, t, e: barra.progress(
                    i / t, text=f"Consultando {i} de {t} ({e})..."),
                precios_lista=precios_de_lista()[1])
            barra.empty()
            ok_h, det_h = competencia.guardar_comparacion(
                st.session_state["comp"], origen="planilla")
            st.session_state["comp_guardado"] = (ok_h, det_h)

    guardado = st.session_state.get("comp_guardado")
    if guardado:
        ok_h, det_h = guardado
        (st.caption if ok_h else st.warning)(
            f"📋 {det_h}" if ok_h
            else f"La comparación no se guardó en la planilla: {det_h}")

    df = st.session_state.get("comp")
    if df is not None and len(df):
        ok = df[df["estado"] == "ok"]
        perdiendo = ok[ok["diferencia"].notna() & (ok["diferencia"] > 0)]
        ganando = ok[ok["mejor_vendedor"] == "NOSOTROS"]

        m1, m2, m3 = st.columns(3)
        m1.metric("EAN con competencia", len(ok))
        m2.metric("Somos los más baratos", len(ganando))
        m3.metric("Estamos por encima", len(perdiendo))

        if len(perdiendo):
            peor = perdiendo.nlargest(1, "diferencia").iloc[0]
            st.warning(
                f"**En {len(perdiendo)} productos estamos más caros que el "
                f"más barato.** El caso extremo: EAN {peor['ean']}, "
                f"nosotros {pesos_md(peor['nuestro_precio'])} contra "
                f"{pesos_md(peor['mejor_precio'])} de *{peor['mejor_vendedor']}* "
                f"({peor['diferencia']:+.0%}).", icon="📉")

        sin_cat = df[df["estado"] != "ok"]
        if len(sin_cat):
            st.info(f"{len(sin_cat)} EAN sin datos de competencia "
                    "(sin producto de catálogo o sin vendedores activos).")

        st.dataframe(
            df, use_container_width=True, height=420,
            column_config={
                "ean": "EAN", "producto": "Producto",
                "mejor_precio": st.column_config.NumberColumn(
                    "Mejor precio", format="%.0f"),
                "mejor_vendedor": "Lo vende",
                "reputacion": "Reputación",
                "nuestro_precio": st.column_config.NumberColumn(
                    "Nuestro precio", format="%.0f"),
                "diferencia": st.column_config.NumberColumn(
                    "Diferencia", format="percent",
                    help="Cuánto estamos por encima del más barato"),
                "posicion": "Posición",
                "competidores": "Vendedores",
                "precio_publicacion": st.column_config.NumberColumn(
                    "Publicar a", format="%.0f",
                    help="El precio que dice la lista de Suprabond"),
                "descuento_para_ganar": st.column_config.NumberColumn(
                    "Descuento para pasar", format="percent"),
                "entra_en_descuento": st.column_config.CheckboxColumn(
                    "Entra en el permitido"),
                "como_ganarlo": st.column_config.TextColumn(
                    "Qué hace falta", width="medium"),
                "estado": "Estado", "detalle": "Detalle",
                "product_id": "Producto ML"})

        st.download_button("Descargar el análisis",
                           df.to_csv(index=False).encode("utf-8"),
                           f"competencia_{datetime.now():%Y%m%d_%H%M}.csv",
                           "text/csv")

    st.divider()
    with st.expander("📋 Historial de comparaciones"):
        st.caption(
            "Cada comparación queda guardada en la hoja "
            f"`{competencia.HOJA_HISTORIAL}`. Sirve para ver cómo evolucionó "
            "el precio de un competidor o el tuyo a lo largo del tiempo.")
        if st.button("Cargar el historial"):
            try:
                st.session_state["comp_hist"] = competencia.historial()
            except Exception as e:
                st.error(f"No pude leer el historial: {e}")

        hcomp = st.session_state.get("comp_hist")
        if hcomp is not None and len(hcomp):
            c1, c2 = st.columns(2)
            c1.metric("Mediciones guardadas", len(hcomp))
            c2.metric("Productos distintos", hcomp["ean"].nunique())

            ean_sel = st.selectbox(
                "Ver la evolución de un producto",
                ["(todos)"] + sorted(hcomp["ean"].unique()),
                format_func=lambda e: e if e == "(todos)" else
                f"{e} · {hcomp[hcomp.ean == e].iloc[-1]['producto'][:45]}")
            v = hcomp if ean_sel == "(todos)" else hcomp[hcomp.ean == ean_sel]

            if ean_sel != "(todos)" and len(v) > 1:
                serie = v.copy()
                for c in ("mejor_precio", "nuestro_precio"):
                    serie[c] = pd.to_numeric(serie[c], errors="coerce")
                st.line_chart(serie.set_index("fecha")[
                    ["mejor_precio", "nuestro_precio"]])

            st.dataframe(v.iloc[::-1], use_container_width=True, height=300)
            st.download_button("Descargar el historial",
                               v.to_csv(index=False).encode("utf-8"),
                               f"historial_competencia_{datetime.now():%Y%m%d}.csv",
                               "text/csv", key="dl_hcomp")
        elif hcomp is not None:
            st.info("Todavía no hay comparaciones guardadas.")

elif seccion == "Oportunidades":
    st.markdown("#### Dónde hay plata sobre la mesa")
    op = st.radio("Vista", ["Visitas vs ventas", "Tramos de comisión",
                            "Premium vs Clásica",
                            "Precios espejo", "Duplicados", "Factura de ML",
                            "Envíos", "Candidatos a Full",
                            "Salud del catálogo"],
                  horizontal=True, label_visibility="collapsed")

    if op == "Candidatos a Full":
        st.caption(
            "Por qué productos empezar si se agranda el uso de Full, ordenados "
            "por el tamaño del premio: cuánta plata de envío quema cada uno "
            "por mes.")
        st.warning(
            "**Esto no estima cuánto se ahorraría.** MercadoLibre no expone "
            "ningún endpoint de recomendación de Full, y CRAFTERS tiene 20 SKU "
            "en Full sobre 997: con esa muestra no se puede comparar contra "
            "los del depósito propio sin inventar el número. Lo que sí está "
            "medido es cuánto envío paga hoy cada producto.", icon="ℹ️")

        f1, f2 = st.columns([1.2, 3])
        dias_f = f1.selectbox("Período", [30, 60, 90], index=2,
                              format_func=lambda d: f"{d} días", key="d_full")
        if f2.button("Analizar candidatos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo costos de envío..."):
                st.session_state["full"] = full.analizar(
                    ml, dias_f, pubs=pubs,
                    callback=lambda m: estado.caption(str(m)))
            estado.empty()

        guardado_f = st.session_state.get("full")
        if guardado_f is not None:
            cand, foto = guardado_f
            if not len(foto):
                st.info("Sin datos suficientes de envío para comparar.")
            else:
                st.markdown("##### Dónde se paga el envío")
                st.caption(
                    "El dato que ordena todo: CRAFTERS paga envío casi solo "
                    "arriba de $33.000. Debajo de esa franja la mediana de "
                    "envío pagado por el vendedor es cero.")
                st.dataframe(
                    foto, use_container_width=True, hide_index=True,
                    column_config={
                        "franja": "Franja de precio",
                        "sku_propios": "SKU propios",
                        "sku_en_full": "SKU en Full",
                        "envio_propio": st.column_config.NumberColumn(
                            "Envío/u propio", format="%.0f"),
                        "envio_full": st.column_config.NumberColumn(
                            "Envío/u Full", format="%.0f"),
                        "paga_envio": "Pagan envío",
                        "plata_envio_mes": st.column_config.NumberColumn(
                            "Plata en envío/mes", format="%.0f"),
                        "comparable": st.column_config.CheckboxColumn(
                            "¿Comparable?",
                            help=f"Necesita {full.MINIMO_POR_FRANJA}+ SKU de "
                                 "cada lado para poder comparar")})

                if len(cand):
                    st.metric("Plata de envío que juntan los candidatos",
                              pesos(cand["plata_envio_mensual"].sum()) + "/mes")
                    st.caption(
                        f"Candidatos: no están en Full, vendieron "
                        f"{full.MINIMO_UNIDADES}+ unidades en el período y "
                        f"pagan envío. Mirá la columna **u/mes**: un producto "
                        f"que quema mucho envío pero rota poco es mal "
                        f"candidato, porque el almacenamiento de Full se come "
                        f"la diferencia.")
                    st.dataframe(
                        cand[["sku", "titulo", "unidades_por_mes",
                              "precio_prom", "envio_prom", "envio_sobre_precio",
                              "plata_envio_mensual", "cobertura_envio"]],
                        use_container_width=True, height=420, hide_index=True,
                        column_config={
                            "sku": "SKU", "titulo": "Título",
                            "unidades_por_mes": st.column_config.NumberColumn(
                                "u/mes", format="%.0f"),
                            "precio_prom": st.column_config.NumberColumn(
                                "Precio", format="%.0f"),
                            "envio_prom": st.column_config.NumberColumn(
                                "Envío/u", format="%.0f"),
                            "envio_sobre_precio": st.column_config.NumberColumn(
                                "Envío / precio", format="percent"),
                            "plata_envio_mensual": st.column_config.NumberColumn(
                                "Envío/mes", format="%.0f"),
                            "cobertura_envio": st.column_config.NumberColumn(
                                "Cobertura", format="percent",
                                help="Qué proporción de las unidades tiene "
                                     "dato real de envío")})
                    st.download_button(
                        "Descargar los candidatos",
                        cand.to_csv(index=False).encode("utf-8"),
                        f"candidatos_full_{datetime.now():%Y%m%d}.csv",
                        "text/csv")
                else:
                    st.info("Ningún producto cumple las condiciones de "
                            "candidato en este período.")

    elif op == "Salud del catálogo":
        st.caption(
            "Qué hay que arreglar en los datos para que el resto de las "
            "herramientas funcione bien. Ordenado por lo que cada publicación "
            "vendió: arreglar la ficha de algo que vende 3.000 unidades vale "
            "más que la de algo que nunca vendió.")

        if st.button("Revisar el catálogo"):
            st.session_state["salud"] = salud.analizar(pubs)

        dfs = st.session_state.get("salud")
        if dfs is not None and len(dfs):
            res = salud.resumen(dfs)
            st.metric("Publicaciones con algo para arreglar", len(dfs))

            cols = st.columns(len(res) or 1)
            for c, (k, n) in zip(cols, sorted(res.items(), key=lambda x: -x[1])):
                c.metric(k.capitalize(), n)

            with st.expander("Qué rompe cada problema"):
                st.markdown(
                    "- **Sin SKU**: la publicación es invisible para las "
                    "herramientas de precio, stock, rentabilidad y espejos.\n"
                    "- **SKU contradictorio**: se resuelve por `SELLER_SKU`, "
                    "pero la discrepancia suele indicar carga descuidada y "
                    "puede apuntar al producto equivocado.\n"
                    "- **Sin código de barras**: no se puede comparar contra "
                    "la competencia.\n"
                    "- **Pausada con stock**: no vende y tiene mercadería "
                    "inmovilizada.\n"
                    "- **Activa sin stock**: ocupa lugar y no puede vender.")

            filtro_s = st.multiselect("Filtrar por problema", sorted(res),
                                      default=sorted(res))
            vs = dfs[dfs["problemas"].apply(
                lambda x: any(f in x for f in filtro_s))] if filtro_s else dfs

            st.dataframe(
                vs, use_container_width=True, height=420,
                column_config={
                    "item_id": "Publicación", "sku": "SKU", "titulo": "Título",
                    "estado": "Estado", "stock": "Stock",
                    "vendidas": "Vendidas",
                    "precio": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "problemas": "Qué arreglar", "cuantos": None,
                    "prioridad": None})
            st.download_button("Descargar la lista",
                               vs.to_csv(index=False).encode("utf-8"),
                               f"salud_catalogo_{datetime.now():%Y%m%d}.csv",
                               "text/csv")
        elif dfs is not None:
            st.success("El catálogo no tiene problemas de datos. 👌")

    elif op == "Envíos":
        st.caption(
            "En qué productos se va la plata de envío. El caso típico es el "
            "producto voluminoso con precio bajo: paga el mismo envío que uno "
            "caro, pero sobre un precio mucho menor.")
        st.caption(
            "El costo es el que paga CRAFTERS (`senders[].cost`), no el "
            "comprador. Se muestrean unas ventas por SKU, así que la columna "
            "**cobertura** dice qué proporción tiene dato real.")

        e1, e2 = st.columns([1.2, 3])
        dias_e = e1.selectbox("Período", [30, 60, 90], index=2,
                              format_func=lambda d: f"{d} días", key="d_env")
        if e2.button("Analizar envíos", use_container_width=True):
            estado = st.empty()
            with st.spinner("Trayendo costos de envío..."):
                st.session_state["envios"] = envios.analizar(
                    ml, dias_e, callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfv = st.session_state.get("envios")
        if dfv is not None and len(dfv):
            pierde = int((dfv["diagnostico"] == "pierde_plata").sum())
            critico = int((dfv["diagnostico"] == "envio_critico").sum())
            v1, v2, v3 = st.columns(3)
            v1.metric("SKU medidos", len(dfv))
            v2.metric("Envío crítico (+35%)", critico)
            v3.metric("Pierden plata", pierde)

            if pierde:
                peor = dfv[dfv["diagnostico"] == "pierde_plata"].iloc[0]
                st.error(
                    f"**{pierde} productos pierden plata solo con el envío y "
                    f"la comisión**, antes de contar el costo de la "
                    f"mercadería. El peor: `{peor['sku']}` se vende a "
                    f"{pesos_md(peor['precio_prom'])} y el envío cuesta "
                    f"{pesos_md(peor['envio_prom'])}.", icon="🚚")

            solo_probl = st.checkbox("Ver solo los problemáticos", value=True)
            vv = (dfv[dfv["diagnostico"] != "normal"] if solo_probl else dfv)

            st.dataframe(
                vv, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU",
                    "precio_prom": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "envio_prom": st.column_config.NumberColumn(
                        "Envío", format="%.0f"),
                    "envio_sobre_precio": st.column_config.NumberColumn(
                        "Envío / precio", format="percent"),
                    "comision_prom": st.column_config.NumberColumn(
                        "Comisión", format="%.0f"),
                    "queda_antes_del_costo": st.column_config.NumberColumn(
                        "Queda", format="%.0f",
                        help="Antes de restar el costo de la mercadería"),
                    "margen_bruto": st.column_config.NumberColumn(
                        "Margen bruto", format="percent"),
                    "unidades_vendidas": "Unidades",
                    "cobertura_envio": st.column_config.NumberColumn(
                        "Cobertura", format="percent"),
                    "plata_en_envio": st.column_config.NumberColumn(
                        "Plata en envío", format="%.0f"),
                    "diagnostico": "Diagnóstico",
                    "ordenes": None, "comision_sobre_precio": None,
                    "cargos_totales": None, "items_sin_comision": None})
            st.download_button("Descargar el análisis",
                               vv.to_csv(index=False).encode("utf-8"),
                               f"envios_{datetime.now():%Y%m%d}.csv", "text/csv")

            st.info(
                "Qué hacer con los que pierden: subir el precio, dejar de "
                "ofrecer envío gratis, venderlos solo por cantidad, o "
                "discontinuarlos. Ojo con los de **pocas unidades**: puede ser "
                "un envío puntual al interior y no un patrón.", icon="💡")

    elif op == "Factura de ML":
        st.caption(
            "MercadoLibre te factura entre \\$22M y \\$35M por mes. Cada orden "
            "trae la comisión que ML se cobró por esa venta. Esto compara las "
            "dos cosas, período por período.")
        st.info(
            "**No es una auditoría contable.** La factura incluye conceptos "
            "que no salen de las órdenes (envíos, publicidad, cargos por "
            "publicación), así que es normal que sea mayor. Lo que importa es "
            "si esa proporción **se mantiene estable**: un salto repentino es "
            "lo que amerita revisar.", icon="🧾")

        n_per = st.selectbox("Períodos a comparar", [3, 4, 6], index=0)
        if st.button("Conciliar"):
            estado = st.empty()
            try:
                with st.spinner("Trayendo facturación y órdenes..."):
                    st.session_state["concil"] = conciliacion.conciliar(
                        ml, n_per, callback=lambda m: estado.caption(str(m)))
            except Exception as e:
                # Sin este except, Streamlit Cloud **tapa el mensaje** ("error
                # redacted to prevent data leaks") y no queda forma de saber
                # que contesto ML. Se captura Exception y no MeliError porque
                # con st.cache_resource el cliente puede vivir en otra copia
                # del modulo (ver es_error_de_api en meli.py).
                estado.empty()
                if es_error_de_api(e):
                    st.error(f"MercadoLibre rechazó el pedido: {e}")
                    st.info(
                        "La API de facturación tiene un límite de tasa muy "
                        "bajo (aguanta unas pocas llamadas seguidas). Si "
                        "apretaste **Conciliar** varias veces, esperá un "
                        "minuto y probá de nuevo.", icon="⏳")
                else:
                    st.error(f"Falló la conciliación: {type(e).__name__}: {e}")
                st.stop()
            estado.empty()

        dfk = st.session_state.get("concil")
        if dfk is not None and len(dfk):
            ult = dfk.iloc[0]
            n1, n2, n3 = st.columns(3)
            n1.metric("Último período facturado", pesos(ult["facturado_ml"]))
            n2.metric("Son comisiones de venta",
                      pesos(ult["comisiones_calculadas"]))
            n3.metric("Otros conceptos", pesos(ult["otros_conceptos"]),
                      f"{ult['proporcion_otros']:.0%} del total")

            if "alerta" in dfk and dfk["alerta"].any():
                st.warning(
                    "Hay períodos que se desvían más de 10 puntos del "
                    "promedio. Vale revisar qué cambió: publicidad nueva, "
                    "cargos por publicación o ajustes.", icon="⚠️")
            else:
                st.success(
                    "La proporción se mantiene estable entre períodos: no hay "
                    "señales de un cobro fuera de lo normal.")

            st.dataframe(
                dfk, use_container_width=True,
                column_config={
                    "periodo": "Período",
                    "facturado_ml": st.column_config.NumberColumn(
                        "ML facturó", format="%.0f"),
                    "comisiones_calculadas": st.column_config.NumberColumn(
                        "Comisiones de venta", format="%.0f"),
                    "otros_conceptos": st.column_config.NumberColumn(
                        "Otros conceptos", format="%.0f"),
                    "proporcion_otros": st.column_config.NumberColumn(
                        "% otros", format="percent"),
                    "desvio_vs_promedio": st.column_config.NumberColumn(
                        "Desvío", format="percent"),
                    "impago": st.column_config.NumberColumn(
                        "Impago", format="%.0f"),
                    "ordenes": "Órdenes", "unidades": "Unidades",
                    "alerta": "Revisar"})
            st.download_button("Descargar la conciliación",
                               dfk.to_csv(index=False).encode("utf-8"),
                               f"conciliacion_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    elif op == "Premium vs Clásica":
        st.caption(
            "Cuando un SKU está publicado en **Premium** (con cuotas) y en "
            "**Clásica** a la vez, la Premium paga ~12 puntos más de comisión: "
            "su precio tiene que ser más alto o cada venta deja menos. Acá "
            "están las que no lo cubren.")
        st.info(
            "**El recargo que empata no es la resta de comisiones.** Con 25,8% "
            "contra 13,5% la diferencia es de 12,3 puntos, pero el recargo se "
            "aplica sobre el precio nuevo, que también paga comisión: hay que "
            "subir **16,6%**. Con 12,3% la venta sigue perdiendo plata.",
            icon="🧮")

        if st.button("Analizar Premium vs Clásica", key="fin_go"):
            paso = st.empty()
            try:
                with st.spinner("Releyendo precios en vivo..."):
                    try:
                        sug_fin = LP.mapa_precios()
                    except Exception:                  # noqa: BLE001
                        sug_fin = {}
                    st.session_state["fin_df"] = financiacion.analizar(
                        ml, pubs=pubs, sugeridos=sug_fin,
                        callback=lambda m: paso.caption(str(m)))
            except Exception as e:                     # noqa: BLE001
                paso.empty()
                st.error(f"No pude analizar: {type(e).__name__}: {e}")
                st.stop()
            paso.empty()
            st.session_state.pop("fin_res", None)

        dfin = st.session_state.get("fin_df")
        if dfin is not None and len(dfin):
            malos = financiacion.no_cubre(dfin)
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Pares Premium/Clásica", len(dfin))
            f2.metric("No cubren la financiación", len(malos),
                      help="Las corregidas quedan con la brecha en cero y "
                           "salen de esta cuenta.")
            f3.metric("Al mismo precio o más baratas",
                      int((malos["dif_precio"] <= financiacion.TOLERANCIA).sum()))
            f4.metric("Se deja por unidad",
                      pesos(-malos["brecha"].sum()) if len(malos) else "—",
                      help="Suma de lo que cada Premium deja de ganar en cada "
                           "venta, comparada con la Clásica del mismo SKU")

            sin_log = malos[~malos["misma_logistica"]]
            if len(sin_log):
                st.warning(
                    f"**{len(sin_log)} pares tienen logística distinta** (una "
                    "en Full y la otra no). Ahí el envío no es el mismo, así "
                    "que la brecha real es todavía mayor que la calculada.",
                    icon="📦")

            st.divider()
            st.markdown("##### Cómo corregirlo")
            salida_fin = st.radio(
                "Qué hacer con las que no cubren",
                ["Subir el precio de la Premium",
                 "Apagar la Premium y quedarse con la Clásica"],
                key="fin_salida", horizontal=True)

        if dfin is not None and len(dfin) and salida_fin.startswith("Apagar"):
            st.warning(
                "**Apagar la Premium pierde las cuotas sin interés** para ese "
                "producto, que suele ser lo que la hace vender. Es reversible "
                "—queda pausada y se puede reactivar— pero mirá la columna de "
                "unidades antes: si la Premium vende bastante más que la "
                "Clásica, subirle el precio probablemente convenga más.",
                icon="🔌")
            plan_off = financiacion.plan_apagado(dfin)
            listas_off = plan_off[plan_off["accion"] == "apagar"]
            frenadas = plan_off[plan_off["accion"] == "revisar"]

            o1, o2, o3 = st.columns(3)
            o1.metric("Se pueden apagar", len(listas_off))
            o2.metric("Frenadas", len(frenadas),
                      help="La Clásica no está activa o no tiene stock, así "
                           "que no puede tomar la venta")
            o3.metric("Deja de perder por unidad",
                      pesos(listas_off["gana_por_unidad"].sum())
                      if len(listas_off) else "—")

            st.dataframe(
                plan_off[["sku", "titulo", "item_id", "precio_actual",
                          "clasica", "precio_clasica", "vendidas_premium",
                          "vendidas_clasica", "stock_clasica",
                          "gana_por_unidad", "accion", "motivo"]],
                use_container_width=True, hide_index=True)

            gana_pr = listas_off[
                listas_off["vendidas_premium"] > listas_off["vendidas_clasica"]]
            if len(gana_pr):
                st.info(
                    f"En **{len(gana_pr)} de las {len(listas_off)}** la Premium "
                    "vendió más que la Clásica. Ahí apagarla puede costar "
                    "ventas: son las candidatas naturales a subirles el precio "
                    "en vez de apagarlas.", icon="⚖️")

            if len(listas_off):
                op_off = st.text_input("Tu nombre (queda en el registro)",
                                       key="fin_op_off")
                conf_off = st.checkbox(
                    f"Confirmo que quiero pausar {len(listas_off)} "
                    "publicaciones Premium", key="fin_conf_off")
                if st.button(f"Apagar {len(listas_off)} Premium",
                             key="fin_off_go",
                             disabled=not (conf_off and op_off.strip())):
                    barra = st.progress(0.0, text="Apagando...")
                    try:
                        st.session_state["fin_res"] = financiacion.apagar(
                            ml, plan_off, operador=op_off.strip(),
                            callback=lambda i, t, f: barra.progress(
                                min(i / max(t, 1), 1.0),
                                text=f"{i} de {t}: {f['sku']}"))
                    except Exception as e:             # noqa: BLE001
                        barra.empty()
                        st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
                        st.stop()
                    barra.empty()
                    st.session_state.pop("fin_df", None)

        if dfin is not None and len(dfin) and salida_fin.startswith("Subir"):
            b1, b2 = st.columns([1.4, 2])
            base_fin = b1.radio(
                "Precio de partida",
                ["Precio sugerido del SKU", "Precio de la Clásica",
                 "Igualar el neto exacto"],
                key="fin_base",
                help="El sugerido es `ListaPrecio × 2,12`, una decisión "
                     "comercial ya tomada. Si el SKU no lo tiene cargado, se "
                     "usa el precio de la Clásica.")
            auto_fin = b2.checkbox(
                "Spread de financiación automático", value=True, key="fin_auto",
                help="El que empata el neto con la Clásica. Se calcula por "
                     "categoría, porque las comisiones cambian según cuál sea.")
            spread_fin = None
            if not auto_fin:
                spread_fin = b2.slider(
                    "Spread sobre el precio de partida", 0.0, 30.0, 16.6, 0.1,
                    format="%.1f%%", key="fin_spread",
                    help="En 0% se publica el precio de partida tal cual, sin "
                         "cobrar la financiación. Es una decisión válida: "
                         "sirve para alinear precios a propósito.") / 100

            modo = {"Precio sugerido del SKU": "sugerido",
                    "Precio de la Clásica": "clasica",
                    "Igualar el neto exacto": "igualar"}[base_fin]
            if modo == "igualar" and not auto_fin:
                st.caption("*Igualar el neto* calcula el precio exacto contra "
                           "los escalones reales, así que ignora el slider.")

            techo_fin = st.slider(
                "Cuánto se acepta subir un precio de una vez", 5, 60,
                int(financiacion.TECHO_DE_SUBIDA * 100), 5, format="%d%%",
                key="fin_techo",
                help="Las que necesitan más que esto quedan en «revisar» en "
                     "vez de aplicarse. Es el freno que más sorprende: con "
                     "base «Clásica» hay publicaciones que necesitan +30% "
                     "para empatar el neto.") / 100
            plan_fin = financiacion.plan(dfin, base=modo, spread=spread_fin,
                                         techo=techo_fin)
            listas = plan_fin[plan_fin["accion"] == "aplicar"]
            revisar = plan_fin[plan_fin["accion"] == "revisar"]
            quietas = plan_fin[plan_fin["accion"] == "ninguna"]

            g1, g2, g3 = st.columns(3)
            g1.metric("Se pueden aplicar", len(listas))
            g2.metric("Quedan para revisar", len(revisar))
            g3.metric("Mejora por unidad",
                      pesos(listas["gana_por_unidad"].sum()) if len(listas) else "—",
                      help="Suma de lo que gana cada publicación en cada venta "
                           "después del cambio")

            vista = plan_fin[["sku", "titulo", "item_id", "precio_actual",
                              "precio_clasica", "sugerido", "precio_nuevo",
                              "cambio", "gana_por_unidad", "origen", "accion",
                              "motivo"]]
            st.dataframe(vista, use_container_width=True, hide_index=True,
                         column_config={
                             "cambio": st.column_config.NumberColumn(
                                 "cambio", format="%.1f%%"),
                         })
            st.download_button(
                "Descargar el plan", vista.to_csv(index=False).encode("utf-8"),
                f"premium_vs_clasica_{datetime.now():%Y%m%d}.csv", "text/csv",
                key="fin_csv")

            if len(revisar):
                with st.expander(f"Por qué {len(revisar)} quedan afuera"):
                    st.dataframe(
                        revisar["motivo"].value_counts().rename_axis("motivo")
                        .reset_index(name="cuántas"),
                        use_container_width=True, hide_index=True)

            if len(listas):
                st.divider()
                st.markdown("##### Aplicar los precios")
                st.warning(
                    f"**Sube el precio de {len(listas)} publicaciones "
                    "Premium.** Subir un precio puede bajar las ventas: la "
                    "alternativa a cobrarlo es apagar la Premium y quedarse "
                    "con la Clásica.", icon="⬆️")
                cruzan = listas[
                    (listas["precio_actual"] < tramos.UMBRAL_ENVIO_GRATIS)
                    & (listas["precio_nuevo"] >= tramos.UMBRAL_ENVIO_GRATIS)]
                if len(cruzan):
                    st.info(
                        f"{len(cruzan)} cruzan los "
                        f"{pesos_md(tramos.UMBRAL_ENVIO_GRATIS)} y ML les va a "
                        "prender el envío gratis, que paga el vendedor. Se "
                        "verifica después de escribir y se revierte si el "
                        "envío se come la mejora.", icon="📦")

                op_fin = st.text_input("Tu nombre (queda en el registro)",
                                       key="fin_op")
                conf_fin = st.checkbox(
                    f"Confirmo que quiero cambiar {len(listas)} precios",
                    key="fin_conf")
                if st.button(f"Aplicar {len(listas)} precios", key="fin_apply",
                             disabled=not (conf_fin and op_fin.strip())):
                    barra = st.progress(0.0, text="Aplicando...")
                    try:
                        st.session_state["fin_res"] = financiacion.aplicar(
                            ml, plan_fin, operador=op_fin.strip(),
                            callback=lambda i, t, f: barra.progress(
                                min(i / max(t, 1), 1.0),
                                text=f"{i} de {t}: {f['sku']}"))
                    except Exception as e:             # noqa: BLE001
                        barra.empty()
                        st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
                        st.stop()
                    barra.empty()
                    # Los precios cambiaron: el análisis quedó viejo.
                    st.session_state.pop("fin_df", None)

        # El resultado va afuera de las dos salidas: se muestra igual hayas
        # subido precios o apagado publicaciones.
        res_fin = st.session_state.get("fin_res")
        if res_fin is not None and len(res_fin):
            ok_fin = int((res_fin["resultado"] == "OK").sum())
            if ok_fin == len(res_fin):
                st.success(f"{ok_fin} publicaciones actualizadas.")
            else:
                st.error(f"{ok_fin} de {len(res_fin)} salieron bien.")
            st.dataframe(res_fin, use_container_width=True, hide_index=True)

        if dfin is not None and not len(dfin):
            st.success("No hay ningún SKU con Premium y Clásica a la vez. 🎉")

    elif op == "Precios espejo":
        st.caption(
            "Casi la mitad del catálogo son publicaciones duplicadas del mismo "
            "producto. Cuando dos tienen precios distintos, **competís contra "
            "vos mismo**: el que compara compra la más barata y la otra no "
            "vende nunca.")
        st.caption(
            "Las Premium se comparan solo contra Premium: es esperable que "
            "valgan más, porque pagan ~12 puntos más de comisión. El precio "
            "sugerido es el de la publicación **que más vendió** del grupo.")

        if st.button("Buscar precios desincronizados"):
            with st.spinner("Comparando..."):
                st.session_state["espejos"] = espejos.analizar(pubs)

        dfe = st.session_state.get("espejos")
        if dfe is not None and len(dfe):
            caras = int((dfe["diferencia"] > 0).sum())
            e1, e2, e3 = st.columns(3)
            e1.metric("Publicaciones a emparejar", len(dfe))
            e2.metric("SKU afectados", dfe["sku"].nunique())
            e3.metric("Más caras que su gemela", caras)

            if caras:
                st.warning(
                    f"**{caras} publicaciones están más caras que otra igual "
                    "tuya.** Salvo que haya un motivo, esas no venden: el "
                    "comprador elige la barata.", icon="🔀")

            st.dataframe(
                dfe, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "tipo": "Tipo", "item_id": "Publicación",
                    "titulo": "Título",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Sugerido", format="%.0f"),
                    "diferencia": st.column_config.NumberColumn(
                        "Diferencia", format="percent"),
                    "vendidas": "Vendidas",
                    "vendidas_referencia": "Vendidas (referencia)",
                    "publicaciones_del_grupo": "En el grupo",
                    "spread_del_grupo": st.column_config.NumberColumn(
                        "Spread", format="percent"),
                    "riesgo": "Qué pasa"})

            st.download_button(
                "Descargar para la sección Precios",
                dfe[["item_id", "precio_sugerido"]].rename(
                    columns={"item_id": "MLA", "precio_sugerido": "Precio"}
                ).to_csv(index=False).encode("utf-8"),
                f"espejos_{datetime.now():%Y%m%d}.csv", "text/csv",
                help="Sale por MLA y no por SKU: acá cada publicación lleva su "
                     "propio precio, no todas el mismo")
        elif dfe is not None:
            st.success("No hay publicaciones espejo con precios distintos. 👌")

    elif op == "Duplicados":
        st.caption(
            "Publicaciones del mismo SKU que compiten entre sí. Deja la que "
            "más vendió y borra la otra — salvo que vendan parecido, en cuyo "
            "caso **se quedan las dos**.")
        st.info(
            "**Casi ningún SKU repetido es un duplicado.** De 997 SKU "
            "activos, 720 tienen más de una publicación, pero la mayoría es "
            "deliberada: las de **catálogo** las crea ML aparte y son las que "
            "ganan el Buy Box, y las que mezclan **Premium y Clásica** son "
            "una decisión de precio. Solo se tocan los grupos del mismo tipo, "
            "sin catálogo y sin Full.", icon="🧩")

        if st.button("Analizar duplicados"):
            with st.spinner("Agrupando por SKU..."):
                st.session_state["dup"] = duplicados.analizar(pubs)

        dd = st.session_state.get("dup")
        if dd is not None and len(dd):
            por_clase = dd.groupby("clase")["sku"].nunique()
            c1, c2, c3 = st.columns(3)
            c1.metric("Grupos que se pueden tocar",
                      int(por_clase.get("limpio", 0)))
            c2.metric("A borrar",
                      int((dd["decision"] == "borrar").sum()))
            c3.metric("Empates que se dejan",
                      int((dd["decision"] == "dejar - empate").sum()))

            with st.expander("Por qué no se tocan los otros grupos"):
                st.dataframe(
                    dd[dd["clase"] != "limpio"]
                    .groupby("clase")
                    .agg(grupos=("sku", "nunique"),
                         publicaciones=("item_id", "size")).reset_index(),
                    use_container_width=True, hide_index=True)
                st.caption(
                    "Borrar una publicación de catálogo es tirar la que gana "
                    "el Buy Box. Borrar una Premium o una Clásica cambia la "
                    "oferta, no limpia un duplicado.")

            limpio = dd[dd["clase"] == "limpio"]
            st.dataframe(
                limpio[["sku", "titulo", "item_id", "precio", "unidades_30d",
                        "importe_30d", "decision", "motivo"]],
                use_container_width=True, height=360, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título", "item_id": "Publicación",
                    "precio": st.column_config.NumberColumn(
                        "Precio", format="%.0f"),
                    "unidades_30d": st.column_config.NumberColumn(
                        "Unid. 30d", format="%.0f"),
                    "importe_30d": st.column_config.NumberColumn(
                        "Vendido 30d", format="%.0f"),
                    "decision": "Qué se hace", "motivo": "Por qué"})

            aborrar = dd[dd["decision"] == "borrar"]
            if not len(aborrar):
                st.success("No hay ninguna para borrar.")
            else:
                st.error(
                    f"**Vas a borrar {len(aborrar)} publicaciones y esto no "
                    "tiene vuelta atrás.** Borrar son dos pasos, cerrar y "
                    "eliminar, y **ya el primero es definitivo**: una "
                    "publicación cerrada no se puede reactivar — la API "
                    "acepta el pedido y la deja cerrada igual. No vuelven el "
                    "ID, la antigüedad, las preguntas ni las ventas "
                    "históricas. Antes de borrar, cada publicación se guarda "
                    "entera en la hoja `duplicados_borrados`: eso alcanza "
                    "para volver a publicar el producto, no para recuperar "
                    "ésta.", icon="🛑")

                op_dp = st.text_input("Tu nombre (queda en el registro)",
                                      key="dp_op")
                escrito = st.text_input(
                    "Escribí BORRAR para habilitar el botón", key="dp_conf")
                if st.button("Borrar en MercadoLibre", key="dp_go",
                             disabled=not (escrito.strip().upper() == "BORRAR"
                                           and op_dp.strip())):
                    barra = st.progress(0.0, text="Borrando...")
                    try:
                        res_dp = duplicados.borrar(
                            ml, dd, operador=op_dp.strip(),
                            callback=lambda i, t, f: barra.progress(
                                i / t, text=f"Borrando {i} de {t}..."))
                    except Exception as e:
                        barra.empty()
                        st.error(f"La corrida se cortó: "
                                 f"{type(e).__name__}: {e}")
                        st.stop()
                    barra.empty()
                    st.session_state["dup_res"] = res_dp
                    st.session_state.pop("dup", None)

        st.divider()
        st.markdown("##### Publicaciones que comparten ficha de catálogo")
        st.caption(
            "Esto **no lo ve el análisis de arriba**, que agrupa por SKU. Acá "
            "se agrupa por la ficha de catálogo, que es donde MercadoLibre "
            "decide qué es un duplicado — aunque los títulos sean distintos, "
            "o incluso los SKU.")

        if st.button("Revisar fichas de catálogo", key="cat_go"):
            with st.spinner("Agrupando por ficha..."):
                st.session_state["dup_cat"] = duplicados.por_catalogo(pubs)

        dc = st.session_state.get("dup_cat")
        if dc is not None and len(dc):
            choque = dc[dc["clase"] == "choque entre tiendas"]
            misma = dc[dc["clase"] == "duplicada en la misma tienda"]
            mal = dc[dc["clase"] == "SKU distintos"]
            moderadas = dc[dc["moderacion"] != ""]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Choque entre tiendas",
                      choque["catalog_product_id"].nunique())
            k2.metric("Duplicada en la misma tienda",
                      misma["catalog_product_id"].nunique())
            k3.metric("Fichas con SKU distintos",
                      mal["catalog_product_id"].nunique())
            k4.metric("Con moderación encima", len(moderadas))

            st.warning(
                "**El círculo vicioso.** Cuando dos publicaciones nuestras "
                "compiten en la misma ficha, MercadoLibre modera una por "
                "duplicada y la otra queda en `waiting_for_patch`, pausada "
                "esperando volver a competir. Se rompe dejando en catálogo la "
                "de **una sola tienda** y rechazando la sugerencia de "
                "catálogo en la otra — la de la segunda tienda vale la pena "
                "igual, pero sin competir en la ficha.", icon="🔁")
            n_sacar = int((dc["accion"] == "sacar del catálogo").sum())
            n_mirar = int((dc["accion"] == "mirar a mano").sum())
            if n_sacar:
                st.info(
                    f"**{n_sacar} publicaciones convendría sacar del "
                    f"catálogo** (columna *Acción*): no venden y su hermana "
                    f"en la misma ficha sí. Otras **{n_mirar} venden "
                    f"parecido a la mejor**, así que ahí no hay una obvia y "
                    f"se decide mirando.\n\n**Esto no se puede hacer desde "
                    f"acá:** MercadoLibre no deja modificar `catalog_listing` "
                    f"por API (contesta *field_not_updatable*), así que va "
                    f"por el panel. Y ojo, **salir del catálogo no se "
                    f"deshace**: volver a entrar crea una publicación nueva, "
                    f"sin historia.", icon="📤")

            if len(moderadas):
                st.error(
                    f"**{len(moderadas)} publicaciones tienen moderación "
                    "encima ahora mismo.** Es el síntoma, no la causa: "
                    "`forbidden` es la que ML bajó por duplicada y "
                    "`waiting_for_patch` la que quedó esperando.", icon="⛔")

            if len(mal):
                st.error(
                    f"**{mal['catalog_product_id'].nunique()} fichas tienen "
                    "dos SKU nuestros colgados.** O son el mismo producto con "
                    "dos códigos, o uno está mal asociado — pasa con "
                    "variantes que se parecen, como un destornillador plano y "
                    "uno Phillips. Eso no se arregla borrando: hay que "
                    "corregir la asociación.", icon="🔗")

            _OPC = {"Choque entre tiendas": choque,
                    "Duplicada en la misma tienda": misma,
                    "SKU distintos": mal,
                    "Con moderación": moderadas}
            cual = st.radio("Ver", list(_OPC), horizontal=True,
                            key="cat_ver", label_visibility="collapsed")
            v = _OPC[cual]
            st.dataframe(
                v[["catalog_product_id", "item_id", "accion", "motivo",
                   "tienda", "tipo",
                   "estado", "moderacion", "sku", "titulo", "en_catalogo",
                   "unidades_30d", "importe_30d"]],
                use_container_width=True, height=380, hide_index=True,
                column_config={
                    "catalog_product_id": "Ficha",
                    "item_id": "Publicación",
                    "accion": "Acción", "motivo": "Por qué",
                    "tienda": "Tienda oficial",
                    "tipo": "Tipo", "estado": "Estado",
                    "moderacion": "Moderación", "sku": "SKU",
                    "titulo": "Título",
                    "en_catalogo": st.column_config.CheckboxColumn(
                        "En catálogo"),
                    "unidades_30d": st.column_config.NumberColumn(
                        "Unid. 30d", format="%.0f"),
                    "importe_30d": st.column_config.NumberColumn(
                        "Vendido 30d", format="%.0f"),
                    })

            st.download_button(
                "Descargar el detalle", dc.to_csv(index=False).encode("utf-8"),
                f"fichas_catalogo_{datetime.now():%Y%m%d}.csv", "text/csv",
                key="cat_dl")
            st.info(
                "**Es un informe, no una acción.** Salir de catálogo no se "
                "deshace, y la que conviene dejar no siempre es la que más "
                "vendió: a veces la tradicional tiene la antigüedad y las "
                "preguntas. Por eso acá no hay botón de borrar.", icon="📋")

        res_dp = st.session_state.get("dup_res")
        if res_dp is not None and len(res_dp):
            hechas = int((res_dp["resultado"] == "BORRADA").sum())
            if hechas == len(res_dp):
                st.success(f"{hechas} publicaciones borradas.")
            else:
                st.warning(f"{hechas} borradas de {len(res_dp)}. "
                           "Mirá el detalle.")
            st.dataframe(res_dp, use_container_width=True, hide_index=True)
            st.caption("El catálogo quedó viejo: apretá **Actualizar "
                       "catálogo** arriba para verlo sin las borradas.")

    elif op == "Tramos de comisión":
        st.caption(
            "MercadoLibre cobra un porcentaje **más un cargo fijo por unidad**, "
            "y ese cargo salta en escalones de precio. El de \\$33.000 **parece "
            "una oportunidad y es lo contrario**: ahí el cargo fijo desaparece, "
            "pero el envío pasa a pagarlo el vendedor y cuesta bastante más de "
            "lo que ahorrás.")

        with st.expander("Los escalones de tu cuenta"):
            st.markdown(
                "| Precio | Cargo fijo | Envío |\n|---|---|---|\n"
                "| menos de \\$16.000 | \\$1.250 | lo paga el comprador |\n"
                "| \\$16.000 a \\$23.999 | \\$2.505 | lo paga el comprador |\n"
                "| \\$24.000 a \\$32.999 | \\$3.005 | lo paga el comprador |\n"
                "| **\\$33.000 o más** | **\\$0** | **lo pagás vos (~\\$7.641)** |\n\n"
                "Los cargos fijos están medidos contra la API por búsqueda "
                "binaria. El umbral del envío está medido sobre 5.170 ventas "
                "reales: debajo de \\$33.000 solo el 6% de las órdenes tiene "
                "costo de envío para vos; desde \\$33.000, el **99%**.\n\n"
                "Por eso cruzar \\$33.000 hacia arriba **cuesta ~\\$4.600 por "
                "unidad**: ahorrás \\$3.005 de cargo fijo y te cargás \\$7.641 de "
                "envío. La oportunidad está al revés: los productos apenas por "
                "encima de \\$33.000 dejan más plata bajando a \\$32.999 — y "
                "encima se venden más baratos.")

        if st.button("Analizar el catálogo"):
            with st.spinner("Calculando..."):
                st.session_state["tramos"] = tramos.analizar(pubs)

        dft = st.session_state.get("tramos")
        if dft is not None and len(dft):
            t1, t2, t3 = st.columns(3)
            t1.metric("Publicaciones a reprecificar", len(dft))
            t2.metric("Mejor caso por unidad",
                      pesos(dft["gana_por_unidad"].max()))
            cruzan = int((dft["cargo_fijo_nuevo"] == 0).sum())
            t3.metric("Cruzan a cargo fijo cero", cruzan)

            solo_grandes = st.checkbox(
                "Ver solo las que ganan más de $1.000 por unidad", value=True)
            v = dft[dft["gana_por_unidad"] > 1000] if solo_grandes else dft

            st.dataframe(
                v, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "precio_actual": st.column_config.NumberColumn(
                        "Precio hoy", format="%.0f"),
                    "precio_sugerido": st.column_config.NumberColumn(
                        "Precio sugerido", format="%.0f"),
                    "cambia_precio": st.column_config.NumberColumn(
                        "Cambia", format="percent"),
                    "motivo": "Por qué",
                    "envio_actual": st.column_config.NumberColumn(
                        "Envío hoy", format="%.0f"),
                    "envio_nuevo": st.column_config.NumberColumn(
                        "Envío nuevo", format="%.0f"),
                    "sube_precio": None,
                    "gana_por_unidad": st.column_config.NumberColumn(
                        "Ganás por unidad", format="%.0f"),
                    "neto_actual": st.column_config.NumberColumn(
                        "Neto hoy", format="%.0f"),
                    "neto_sugerido": st.column_config.NumberColumn(
                        "Neto nuevo", format="%.0f"),
                    "cargo_fijo_actual": st.column_config.NumberColumn(
                        "Fijo hoy", format="%.0f"),
                    "cargo_fijo_nuevo": st.column_config.NumberColumn(
                        "Fijo nuevo", format="%.0f"),
                    "vendidos": "Vendidas", "impacto": None, "item_id": None})

            st.download_button(
                "Descargar para usar en la sección Precios",
                v[["sku", "precio_sugerido"]].rename(
                    columns={"precio_sugerido": "Precio"}
                ).to_csv(index=False).encode("utf-8"),
                f"precios_sugeridos_{datetime.now():%Y%m%d}.csv", "text/csv",
                help="Sale con las columnas SKU y Precio, listo para subir en "
                     "la sección Precios")

            st.info(
                "Casi todas las sugerencias son **bajas** de precio: al "
                "quedar debajo de \\$33.000 el envío vuelve a pagarlo el "
                "comprador. Bajar no baja la conversión, así que se pueden "
                "aplicar con más tranquilidad que una suba.", icon="💡")

            # ------------------------------------------------ aplicar
            st.divider()
            st.markdown("##### Aplicar los cambios en MercadoLibre")

            cruzan_df = dft[dft["envio_actual"] > 0]
            solo_cruzan = st.checkbox(
                f"Solo las {len(cruzan_df)} que cruzan el umbral de envío",
                value=True, key="tr_solo",
                help="Son las que hoy pagan el envío y dejan de pagarlo al "
                     "bajar de $33.000. Las demás solo bajan de escalón de "
                     "cargo fijo y ganan centavos.")
            objetivo = cruzan_df if solo_cruzan else dft

            st.caption(
                f"Alcance: **{len(objetivo)} publicaciones**. No se toca el "
                "envío: se escribe solo el precio. MercadoLibre apaga el envío "
                "gratis obligatorio solo, en el momento, porque lo deriva del "
                "precio — está medido. Aun así **cada publicación que cruza el "
                "umbral se verifica después de escribirla**, y si el envío "
                "quedó prendido se le devuelve el precio anterior.")

            st.warning(
                "**Esto cambia precios en MercadoLibre de verdad.** Los "
                "precios se releen antes de escribir, así que las que se "
                "hayan movido desde el último análisis quedan afuera. Todo "
                "queda en la auditoría con el precio anterior.", icon="⚠️")

            if st.button("Revisar contra los precios de hoy", key="tr_plan"):
                # El resultado de una corrida anterior no puede quedar colgado
                # abajo de un plan nuevo: se lee como si fuera de este.
                st.session_state.pop("tramos_res", None)
                paso = st.empty()
                try:
                    with st.spinner("Leyendo el estado actual..."):
                        st.session_state["tramos_plan"] = tramos.plan(
                            ml, objetivo,
                            callback=lambda m: paso.caption(str(m)))
                except Exception as e:
                    paso.empty()
                    st.error(f"No pude leer el estado actual: "
                             f"{type(e).__name__}: {e}")
                    st.stop()
                paso.empty()

            plan_tr = st.session_state.get("tramos_plan")
            if plan_tr is not None and len(plan_tr):
                van = plan_tr[plan_tr["accion"] == "aplicar"]
                fuera = plan_tr[plan_tr["accion"] == "omitir"]

                p1, p2, p3 = st.columns(3)
                p1.metric("Se van a aplicar", len(van))
                p2.metric("Quedan afuera", len(fuera))
                p3.metric("Ganan por unidad",
                          pesos(van["gana_por_unidad"].sum()) if len(van)
                          else "$0")

                if len(fuera):
                    with st.expander(f"Por qué quedan afuera {len(fuera)}"):
                        st.dataframe(
                            fuera[["sku", "titulo", "precio_actual", "motivo"]],
                            use_container_width=True, hide_index=True)

                if not len(van):
                    st.info("No quedó ninguna para aplicar.")
                else:
                    st.dataframe(
                        van[["sku", "titulo", "precio_actual", "precio_nuevo",
                             "cambia_precio", "gana_por_unidad",
                             "cruza_umbral"]],
                        use_container_width=True, height=260, hide_index=True,
                        column_config={
                            "sku": "SKU", "titulo": "Título",
                            "precio_actual": st.column_config.NumberColumn(
                                "Precio hoy", format="%.0f"),
                            "precio_nuevo": st.column_config.NumberColumn(
                                "Precio nuevo", format="%.0f"),
                            "cambia_precio": st.column_config.NumberColumn(
                                "Cambia", format="percent"),
                            "gana_por_unidad": st.column_config.NumberColumn(
                                "Ganás por unidad", format="%.0f"),
                            "cruza_umbral": st.column_config.CheckboxColumn(
                                "Saca el envío")})

                    op_tr = st.text_input("Tu nombre (queda en el registro)",
                                          key="tr_op")
                    conf_tr = st.checkbox(
                        f"Confirmo que quiero cambiar estos {len(van)} precios "
                        "en MercadoLibre", key="tr_conf")
                    if st.button("Aplicar en MercadoLibre", key="tr_go",
                                 disabled=not (conf_tr and op_tr.strip())):
                        barra = st.progress(0.0, text="Aplicando...")
                        try:
                            res_tr = tramos.aplicar(
                                ml, plan_tr, operador=op_tr.strip(),
                                callback=lambda i, t, f: barra.progress(
                                    i / t, text=f"Aplicando {i} de {t}..."))
                        except Exception as e:
                            barra.empty()
                            st.error(f"La corrida se cortó: "
                                     f"{type(e).__name__}: {e}")
                            st.stop()
                        barra.empty()
                        st.session_state["tramos_res"] = res_tr

            res_tr = st.session_state.get("tramos_res")
            if res_tr is not None and len(res_tr):
                ok = int((res_tr["resultado"] == "OK").sum())
                rev = int((res_tr["resultado"] == "REVERTIDA").sum())
                mal = len(res_tr) - ok - rev

                if ok == len(res_tr):
                    st.success(f"{ok} precios actualizados.")
                else:
                    st.error(f"{ok} aplicados · {rev} revertidos · "
                             f"{mal} con error.")
                if rev:
                    st.warning(
                        f"En **{rev}** MercadoLibre no apagó el envío gratis "
                        "al bajar el precio, así que se les devolvió el precio "
                        "anterior: con el envío prendido el cambio perdía "
                        "plata en vez de ganarla.", icon="↩️")

                st.dataframe(res_tr, use_container_width=True,
                             hide_index=True)
                st.download_button(
                    "Descargar el resultado",
                    res_tr.to_csv(index=False).encode("utf-8"),
                    f"tramos_aplicados_{datetime.now():%Y%m%d_%H%M}.csv",
                    "text/csv", key="tr_dl")
                st.caption("Los precios cambiaron: volvé a apretar **Analizar "
                           "el catálogo** para ver el estado nuevo.")

    else:
        st.caption(
            "Cruza cuántas veces vieron cada publicación contra cuánto vendió. "
            "Detecta lo que se ve y no vende (precio, fotos o descripción) y "
            "lo que vende sin exposición (candidatas a empujar).")
        st.warning(
            "MercadoLibre solo deja consultar las visitas **de a una "
            "publicación por vez**, así que este análisis hace ~2.275 llamadas "
            "y tarda unos 10 minutos. Queda cacheado por rango de fechas.",
            icon="⏳")

        c1, c2 = st.columns([1.2, 3])
        dias_c = c1.selectbox("Período", [15, 30, 60], index=1,
                              format_func=lambda d: f"{d} días")
        if c2.button("Analizar visitas y ventas"):
            estado = st.empty()
            with st.spinner("Esto tarda varios minutos..."):
                st.session_state["conv"] = conversion.analizar(
                    ml, dias_c, callback=lambda m: estado.caption(str(m)))
            estado.empty()

        dfc = st.session_state.get("conv")
        if dfc is not None and len(dfc):
            conv_med = dfc.attrs.get("conversion_mediana", 0)
            k1, k2, k3 = st.columns(3)
            k1.metric("Visitas del período", f"{int(dfc['visitas'].sum()):,}"
                      .replace(",", "."))
            k2.metric("Conversión mediana", f"{conv_med:.2%}")
            k3.metric("Se ven y no venden",
                      int((dfc["diagnostico"] == "no_vende").sum()))

            perdidas = int(dfc[dfc["diagnostico"] == "no_vende"]["visitas"].sum())
            if perdidas:
                st.warning(f"**{perdidas:,} visitas se fueron sin comprar** en "
                           "publicaciones que no vendieron ni una unidad."
                           .replace(",", "."), icon="📉")

            diag = st.multiselect(
                "Ver", sorted(dfc["diagnostico"].unique()),
                default=[d for d in ["no_vende", "convierte_poco", "escalar",
                                     "falta_exposicion"]
                         if d in dfc["diagnostico"].unique()])
            vc = dfc[dfc["diagnostico"].isin(diag)] if diag else dfc

            st.dataframe(
                vc, use_container_width=True, height=420,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "precio": st.column_config.NumberColumn("Precio", format="%.0f"),
                    "visitas": "Visitas", "unidades": "Vendidas",
                    "conversion": st.column_config.NumberColumn(
                        "Conversión", format="percent"),
                    "importe": st.column_config.NumberColumn(
                        "Facturado", format="%.0f"),
                    "diagnostico": "Diagnóstico",
                    "recomendacion": "Qué hacer",
                    "item_id": None, "medida": None, "stock": "Stock"})
            st.download_button("Descargar el análisis",
                               vc.to_csv(index=False).encode("utf-8"),
                               f"conversion_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

elif seccion == "Preguntas":
    st.markdown("#### Respuestas automáticas con IA")

    # La Sheet se lee una vez por minuto, no en cada interacción: leerla en
    # cada render es lento y hace pegarle al límite de la API de Google.
    @st.cache_data(ttl=60, show_spinner=False)
    def _met(con_historial):
        # Va `ml` para que "Esperando respuesta" cruce contra MercadoLibre:
        # una pregunta contestada desde el panel de ML queda abierta en el
        # registro para siempre, y el contador decía 40 con la bandeja vacía.
        return preg.metricas(incluir_historial=con_historial, ml=ml)

    try:
        cfg = preg.config()
        activa = preg.ia_activa()
    except Exception as e:
        st.error(f"No pude leer la configuración de la planilla: {e}")
        st.stop()

    met = _met(False)
    if met.get("error"):
        st.warning(f"Los contadores no se pudieron actualizar: {met['error']}",
                   icon="📊")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Respondidas por la IA", met["respondidas_ia"],
              help="Preguntas que la IA contestó y publicó sola")
    m2.metric("Resueltas a mano", met.get("resueltas_a_mano", 0),
              help="Las que respondió una persona desde Gestión manual")
    m3.metric("Esperando respuesta", met["derivadas_a_persona"],
              help="Preguntas que la IA derivó y que MercadoLibre confirma "
                   "que siguen sin responder. Miralas en Gestión manual."
                   if met.get("pendientes_verificados") else
                   "Abiertas en el registro de la IA. **No se pudo confirmar "
                   "contra MercadoLibre**, así que puede incluir preguntas ya "
                   "contestadas desde el panel de ML.")
    m4.metric("Se resolvieron solas",
              f"{met['tasa_automatica']:.0%}" if met.get("preguntas_unicas")
              else "—",
              help=f"De las {met.get('preguntas_unicas', 0)} preguntas que "
                   "procesó la IA, cuántas pudo cerrar sin ayuda. Es "
                   "acumulado desde que se activó, no de hoy.")

    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    c1.metric("Estado", "Activa" if activa else "Apagada")
    c2.metric("Confianza mínima", cfg.get("min_confianza", "media").capitalize())
    c3.caption(f"Firma: **{cfg.get('firma','')}**  \nSe cambia en la hoja "
               f"`{preg.HOJA_CONFIG}` de la planilla.")

    if not activa:
        st.warning("La IA está **apagada**. Poné `ia_activa = si` en la hoja "
                   f"`{preg.HOJA_CONFIG}` para que vuelva a responder.", icon="⏸️")

    vista_p = st.radio("Vista", ["Dashboard", "Gestión manual",
                                 "Historial completo", "Registro de la IA",
                                 "Fuentes"],
                       horizontal=True, label_visibility="collapsed")

    if vista_p == "Dashboard":
        st.caption(
            "Redacta con el historial de respuestas de la cuenta, los datos de "
            "la publicación y las fuentes cargadas. **Si el contexto no alcanza, "
            "no responde**: deja la pregunta para que la vea una persona.")

        # Solo las contestables: las de publicaciones inactivas no entran al
        # circuito en ningún lado.
        pend = preg.pendientes_respondibles(ml)
        st.metric("Preguntas sin responder", len(pend))
        if pend:
            with st.expander("Ver las preguntas pendientes"):
                for q in pend:
                    st.markdown(f"- `{q['id']}` · {(q.get('text') or '')[:160]}")

        b1, b2 = st.columns(2)
        simular = b1.button("Redactar sin publicar", use_container_width=True,
                            disabled=not pend)
        aplicar = b2.button("Redactar y PUBLICAR", use_container_width=True,
                            disabled=not pend or not activa)

        if simular or aplicar:
            barra = st.progress(0.0, text="Trabajando...")
            r = preg.procesar(
                ml, publicar_de_verdad=aplicar,
                callback=lambda i, t_, q: barra.progress(
                    i / max(t_, 1), text=f"Pregunta {i} de {t_}..."))
            barra.empty()
            st.session_state["preg_res"] = r

        r = st.session_state.get("preg_res")
        if r:
            if "error" in r:
                st.error(r["error"])
            else:
                res = pd.DataFrame(r["resultados"])
                if len(res):
                    pub = (res["estado"] == "publicada").sum()
                    rev = (res["estado"] == "para_revisar").sum()
                    sim = (res["estado"] == "simulada").sum()
                    if pub:
                        st.success(f"{pub} respuestas publicadas en MercadoLibre.")
                    if sim:
                        st.info(f"{sim} redactadas (no se publicaron: fue una prueba).")
                    inact = (res["estado"] == "publicacion_inactiva").sum()
                    if inact:
                        st.info(
                            f"**{inact} no se pudieron responder porque la "
                            "publicación está pausada.** MercadoLibre no lo "
                            "permite. Si la reactivás, se responden en la "
                            "próxima corrida.", icon="⏸️")
                    err = (res["estado"] == "error_tecnico").sum()
                    if err:
                        motivo = res[res["estado"] == "error_tecnico"].iloc[0]["motivo"]
                        st.error(
                            f"**{err} fallaron por un problema técnico**, no "
                            "porque faltara contexto. Hay que corregir esto "
                            f"antes de volver a intentar:\n\n> {motivo}",
                            icon="🔧")
                    if rev:
                        st.warning(
                            f"**{rev} quedaron sin responder** porque el "
                            "contexto no alcanzaba. Están en "
                            "**Gestión manual**: ahí las respondés y se "
                            "publican.",
                            icon="👤")
                    for _, f in res.iterrows():
                        with st.container(border=True):
                            st.markdown(f"**{f['estado']}** · confianza "
                                        f"{f['confianza']} · `{f['question_id']}`")
                            st.markdown(f"**P:** {f['pregunta']}")
                            st.markdown(f"**R:** {f['respuesta'] or '_(no respondió)_'}")
                            st.caption(f"Motivo: {f['motivo']}")

    elif vista_p == "Gestión manual":
        st.caption(
            "**Todas** las preguntas sin responder de la cuenta, las haya "
            "tocado la IA o no. Escribí la respuesta y se publica en "
            "MercadoLibre; también podés pedirle un borrador a la IA para esa "
            "pregunta puntual. Las que alguien ya contestó desde el panel de "
            "ML desaparecen solas.")

        if st.button("↻ Actualizar la bandeja"):
            st.session_state.pop("preg_band", None)

        if "preg_band" not in st.session_state:
            try:
                with st.spinner("Buscando pendientes..."):
                    st.session_state["preg_band"] = preg.bandeja(ml)
            except Exception as e:
                st.error(f"No pude leer los pendientes: {e}")
                st.session_state["preg_band"] = []
        band = st.session_state["preg_band"]

        if not band:
            st.success("No queda ninguna pregunta pendiente. 🎉")
        else:
            st.metric("Esperando una respuesta", len(band))
            nombre = st.text_input("Tu nombre (queda en el registro)",
                                   key="op_band")

            for b in band:
                with st.container(border=True):
                    st.markdown(f"**{b['pregunta']}**")
                    st.caption(f"{b['comprador']} · {b['fecha']} · "
                               f"publicación `{b['item_id']}`")

                    if b["estado"] == "publicacion_inactiva":
                        st.info("La publicación está pausada. MercadoLibre no "
                                "deja responder hasta que la reactives.",
                                icon="⏸️")
                    elif b["motivo"]:
                        st.caption(f"La IA no respondió porque: {b['motivo']}")

                    # Un borrador pedido a mano pisa lo que hubiera antes.
                    clave_borr = f"borr_{b['question_id']}"
                    valor = st.session_state.get(clave_borr, b["borrador"])

                    texto = st.text_area(
                        "Tu respuesta", value=valor, height=110,
                        key=f"resp_{b['question_id']}",
                        placeholder="Escribí acá la respuesta que se va a "
                                    "publicar en MercadoLibre...")

                    c_a, c_ia, c_b = st.columns([1, 1.4, 2.6])
                    if c_ia.button("✨ Sugerir con IA",
                                   key=f"ia_{b['question_id']}",
                                   help="Le pide un borrador a la IA para esta "
                                        "pregunta. No publica nada: lo editás vos."):
                        with st.spinner("Redactando..."):
                            txt, aviso = preg.borrador(
                                ml, b["question_id"], b["item_id"],
                                b["pregunta"], b["comprador"])
                        if txt:
                            st.session_state[clave_borr] = txt
                            if aviso:
                                st.warning(aviso, icon="⚠️")
                            st.rerun()
                        else:
                            st.info(aviso or "La IA no pudo redactar nada.")

                    if c_a.button("Publicar", key=f"pub_{b['question_id']}",
                                  disabled=not nombre.strip()
                                  or b["estado"] == "publicacion_inactiva"):
                        ok, det = preg.responder_a_mano(
                            ml, b["question_id"], texto, nombre,
                            item_id=b["item_id"], pregunta=b["pregunta"],
                            motivo_previo=b["motivo"])
                        if ok:
                            st.success("Publicada." + (f" {det}" if det else ""))
                            st.session_state.pop(clave_borr, None)
                            st.session_state.pop("preg_band", None)
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"No se pudo publicar: {det}")
                    c_b.markdown(
                        f"[Ver la publicación en MercadoLibre]"
                        f"(https://articulo.mercadolibre.com.ar/"
                        f"{str(b['item_id']).replace('MLA','MLA-')}) ↗")

    elif vista_p == "Historial completo":
        st.caption(
            "Todas las preguntas de la cuenta con su respuesta, hayan sido "
            "contestadas por la IA o por una persona. Vive en la hoja "
            f"`{preg.HOJA_HISTORIAL}` de la planilla.")

        if st.button("↻ Sincronizar con MercadoLibre"):
            estado = st.empty()
            with st.spinner("Trayendo preguntas..."):
                r = preg.sincronizar_historial(
                    ml, callback=lambda m: estado.caption(m))
            estado.empty()
            if r["ok"]:
                st.success(f"{r['nuevas']} preguntas nuevas y "
                           f"{r['actualizadas']} actualizadas. "
                           f"Total en la planilla: {r['total']}.")
            else:
                st.error(f"No se pudo guardar: {r['detalle']}")
            st.session_state.pop("preg_hist", None)

        if "preg_hist" not in st.session_state:
            try:
                st.session_state["preg_hist"] = pd.DataFrame(preg.historial())
            except Exception as e:
                st.error(f"No pude leer el historial de la planilla: {e}")
                st.session_state["preg_hist"] = pd.DataFrame()
        hc = st.session_state["preg_hist"]

        if not len(hc):
            st.info("Todavía no hay historial. Apretá **Sincronizar** para "
                    "traerlo de MercadoLibre.")
        else:
            meth = _met(True)
            h1, h2, h3 = st.columns(3)
            h1.metric("Preguntas", meth["historial_total"])
            h2.metric("Respondidas por la IA", meth["historial_por_ia"])
            h3.metric("Respondidas por una persona",
                      meth["historial_por_persona"])

            f1, f2 = st.columns([2, 2])
            with f1:
                quien = st.multiselect(
                    "Quién respondió",
                    sorted(x for x in hc["respondida_por"].unique() if x),
                    default=sorted(x for x in hc["respondida_por"].unique() if x))
            with f2:
                buscar_q = st.text_input("Buscar en la pregunta o la respuesta")

            vista_h = hc[hc["respondida_por"].isin(quien)] if quien else hc
            if buscar_q:
                m_ = (vista_h["pregunta"].str.contains(buscar_q, case=False, na=False)
                      | vista_h["respuesta"].str.contains(buscar_q, case=False,
                                                          na=False))
                vista_h = vista_h[m_]

            st.caption(f"{len(vista_h)} preguntas")
            st.dataframe(vista_h.iloc[::-1], use_container_width=True, height=440,
                         column_config={
                             "question_id": "ID", "fecha_pregunta": "Fecha",
                             "item_id": "Publicación", "publicacion": "Título",
                             "comprador": "Comprador", "pregunta": "Pregunta",
                             "respuesta": "Respuesta",
                             "respondida_por": "Respondió",
                             "estado_ml": "Estado", "sincronizado": "Sincronizado"})
            st.download_button(
                "Descargar el historial completo",
                vista_h.to_csv(index=False).encode("utf-8"),
                f"historial_preguntas_{datetime.now():%Y%m%d}.csv", "text/csv")

    elif vista_p == "Registro de la IA":
        st.caption("Solo lo que procesó la IA, con el motivo de cada decisión.")
        try:
            hist = pd.DataFrame(almacen.leer_hoja(preg.HOJA_RESPUESTAS,
                                                  preg.COLS_RESPUESTAS))
        except Exception as e:
            st.error(f"No pude leer el registro de la planilla: {e}")
            hist = pd.DataFrame()
        if not len(hist):
            st.info("Todavía no hay respuestas registradas.")
        else:
            st.caption(f"{len(hist)} registros · todo lo publicado queda acá")
            st.dataframe(hist.iloc[::-1], use_container_width=True, height=440)
            st.download_button("Descargar el historial",
                               hist.to_csv(index=False).encode("utf-8"),
                               f"respuestas_ia_{datetime.now():%Y%m%d}.csv",
                               "text/csv")

    else:
        st.caption("Documentos y sitios que la IA usa como referencia, además "
                   "del historial de respuestas de la cuenta.")

        f1, f2 = st.columns(2)
        with f1:
            st.markdown("##### Subir un documento")
            doc = st.file_uploader("Ficha técnica, manual, tabla (.pdf o .txt)",
                                   type=["pdf", "txt", "md"], key="up_doc")
            op_doc = st.text_input("Tu nombre", key="op_doc")
            if doc and op_doc.strip() and st.button("Cargar documento"):
                try:
                    texto = (preg.leer_pdf(doc) if doc.name.lower().endswith(".pdf")
                             else doc.getvalue().decode("utf-8", "ignore"))
                    if not texto.strip():
                        st.error("No pude extraer texto (¿es un PDF escaneado?).")
                    else:
                        preg.agregar_fuente("documento", doc.name, texto,
                                            operador=op_doc.strip())
                        st.success(f"Cargado: {len(texto):,} caracteres."
                                   .replace(",", "."))
                except Exception as e:
                    st.error(f"No pude leer el archivo: {e}")

        with f2:
            st.markdown("##### Agregar un sitio")
            url = st.text_input("URL (ej: una ficha técnica online)")
            op_web = st.text_input("Tu nombre", key="op_web")
            if url and op_web.strip() and st.button("Traer la página"):
                try:
                    titulo, texto = preg.bajar_web(url)
                    preg.agregar_fuente("web", titulo, texto, url=url,
                                        operador=op_web.strip())
                    st.success(f"Cargado «{titulo}»: {len(texto):,} caracteres."
                               .replace(",", "."))
                except Exception as e:
                    st.error(f"No pude traer la página: {e}")

        st.divider()
        try:
            fs = pd.DataFrame(preg.fuentes())
        except Exception as e:
            st.error(f"No pude leer las fuentes: {e}")
            fs = pd.DataFrame()
        if len(fs):
            st.dataframe(fs.drop(columns=["contenido"], errors="ignore"),
                         use_container_width=True)
        else:
            st.info("Todavía no hay fuentes cargadas. El historial de "
                    "respuestas de la cuenta se usa igual.")

elif seccion == "Rentabilidad":
    st.markdown("#### Rentabilidad por SKU")
    st.caption(
        "Subí una planilla con el **costo** de cada SKU. La herramienta le suma "
        "el precio de venta actual en MercadoLibre y los cargos reales que cobró "
        "ML en las ventas históricas de ese SKU (comisión, recargo por "
        "financiación, cargo fijo y envío).")

    costos_rent = bloque_costos("rent")

    st.markdown("###### Lista de precios del proveedor")
    bloque_lista_precios("rent")
    mapa_lista_rent, _, cuando_lista_rent, n_lista_rent = precios_de_lista()

    st.divider()

    c1, c2, c3 = st.columns(3)
    with c1:
        dias = st.selectbox("Historia a considerar", [30, 60, 90, 180],
                            index=2, key="dias_rent")
    with c2:
        con_envios = st.checkbox("Incluir costo de envío", value=True,
                                 help="Consulta el costo real de envío de una "
                                      "muestra de ventas por SKU. Tarda más.")
    with c3:
        # 21% por defecto: los costos de CRAFTERS se cargan SIN IVA y los
        # precios de ML lo incluyen. Con "Sin descontar" el margen sale
        # inflado en 21 puntos, que es muchisimo.
        iva = st.selectbox("IVA a descontar del precio", [0.21, 0.105, 0.0],
                           format_func=lambda x: f"{x:.1%}" if x else "Sin descontar",
                           help="La planilla de costos de CRAFTERS está SIN "
                                "IVA y los precios de ML lo incluyen, así que "
                                "corresponde descontarlo. Ponelo en 'Sin "
                                "descontar' solo si cambiás a costos con IVA.")

    otros_rent = controles_otros_conceptos("rent")

    # El único lugar donde se mira el descuento del proveedor. En las
    # secciones que deciden precios va el costo pleno: bajar un precio
    # contando con un descuento que puede no estar deja la venta en pérdida.
    usar_desc = st.checkbox(
        f"Aplicar el descuento del proveedor ({rent.DESCUENTO_PROVEEDOR:.0%} "
        "sobre el costo)", value=True, key="desc_rent",
        help="Es el costo real al que se compra, así que corresponde para "
             "medir cuánta plata se ganó. Las secciones que proponen precios "
             "usan siempre el costo pleno, y eso no se puede cambiar acá.")

    if costos_rent is not None and st.button("Calcular rentabilidad"):
        costos = costos_rent

        with st.spinner(f"Trayendo ventas de los últimos {dias} días..."):
            ordenes = rent.traer_historico(ml, dias)

        envios = None
        if con_envios:
            barra = st.progress(0.0, text="Trayendo costos de envío...")
            envios = rent.traer_costos_envio(
                ml, ordenes, muestra_por_sku=5,
                callback=lambda i, t: barra.progress(
                    min(i / max(t, 1), 1.0), text=f"Costos de envío {i}/{t}..."))
            barra.empty()

        # El precio de lista no siempre es lo que paga el comprador: ~12% de
        # las publicaciones tiene una promocion encima.
        barra = st.progress(0.0, text="Consultando precios reales de venta...")
        ids = rent.items_de_costos(costos, pubs)
        precios_venta = rent.precios_reales(
            ml, ids,
            callback=lambda i, t: barra.progress(min(i / max(t, 1), 1.0),
                                                 text=f"Precios reales {i}/{t}..."))
        barra.empty()

        cargos = rent.cargos_por_sku(ordenes, envios)
        st.session_state["rent"] = rent.calcular(
            costos, cargos, pubs, iva=iva, precios_venta=precios_venta,
            otros_conceptos=otros_rent, con_descuento=usar_desc,
            precios_lista=mapa_lista_rent)

    df = st.session_state.get("rent")
    if df is not None and len(df):
        con_datos = df[df["margen_pct"].notna()]

        if bool(df["con_descuento"].iloc[0]) if "con_descuento" in df else False:
            st.caption(
                f"Margen calculado con el **descuento del "
                f"{rent.DESCUENTO_PROVEEDOR:.0%}** sobre el costo de lista.")
        else:
            st.caption("Margen calculado con el **costo pleno**, sin el "
                       "descuento del proveedor.")

        m1, m2, m3 = st.columns(3)
        m1.metric("SKU analizados", len(df))
        m2.metric("Margen promedio",
                  f"{con_datos['margen_pct'].mean():.1%}" if len(con_datos) else "—")
        m3.metric("SKU con margen negativo",
                  int((con_datos["margen_pct"] < 0).sum()) if len(con_datos) else 0)

        # Cuánto se aparta el precio publicado del que dice la lista.
        if "vs_sugerido" in df:
            con_lista = df[df["vs_sugerido"].notna()]
            if len(con_lista):
                debajo = int((con_lista["vs_sugerido"] < 0.99).sum())
                arriba = int((con_lista["vs_sugerido"] > 1.01).sum())
                if debajo:
                    st.warning(
                        f"**{debajo} SKU están por debajo del precio mínimo "
                        "de la lista.** Subirlos al mínimo es la acción más "
                        "directa: está en *Precio óptimo*.", icon="🏷️")
                if arriba:
                    st.caption(
                        f"Otros {arriba} están por encima del mínimo, que "
                        "**está permitido**: el número de la lista es un "
                        "piso, no un techo.")

        negativos = con_datos[con_datos["margen_pct"] < 0]
        if len(negativos):
            st.error(f"**{len(negativos)} SKU se venden a pérdida.** "
                     "Están primeros en la tabla.")

        sin_precio = df[df["precio_ml"].isna()]
        if len(sin_precio):
            st.warning(f"{len(sin_precio)} SKU de la planilla no tienen "
                       "publicación activa en MercadoLibre.")

        en_promo = df[df.get("en_promo", False) == True]  # noqa: E712
        if len(en_promo):
            st.info(
                f"**{len(en_promo)} SKU tienen una promoción activa.** El margen "
                "está calculado sobre lo que realmente paga el comprador, que es "
                "menor al precio de lista. Mirá la columna *Precio lista* para "
                "comparar.", icon="🏷️")

        st.dataframe(
            df, use_container_width=True, height=420,
            column_config={
                "sku": "SKU",
                "item_id": "Publicación",
                "tipo": "Tipo",
                "precio_ml": st.column_config.NumberColumn(
                    "Precio real", format="%.0f",
                    help="Lo que realmente paga el comprador hoy"),
                "precio_lista": st.column_config.NumberColumn(
                    "Precio lista", format="%.0f"),
                "en_promo": st.column_config.CheckboxColumn("En promo"),
                "costo": st.column_config.NumberColumn("Costo", format="%.0f"),
                "comision_prom": st.column_config.NumberColumn("Comisión", format="%.0f"),
                "envio_prom": st.column_config.NumberColumn(
                    "Envío", format="%.0f"),
                "envio_base": st.column_config.TextColumn(
                    "Base del envío",
                    help="'solo' = medido en envíos donde el producto viajó "
                         "solo, que es el costo real. 'prorrateado' = viajó "
                         "acompañado y se le asignó la parte que le toca por "
                         "valor"),
                "cargos_totales": st.column_config.NumberColumn("Cargos", format="%.0f"),
                "impuestos": st.column_config.NumberColumn(
                    "Impuestos", format="%.0f"),
                "logistico": st.column_config.NumberColumn(
                    "Logístico", format="%.0f"),
                "general": st.column_config.NumberColumn(
                    "General", format="%.0f"),
                "otros_conceptos": st.column_config.NumberColumn(
                    "Otros conceptos", format="%.0f",
                    help="Impuestos + logístico + general"),
                "margen_sin_otros": st.column_config.NumberColumn(
                    "Margen antes de otros", format="%.0f",
                    help="Solo descontando costo, comisión y envío"),
                "margen": st.column_config.NumberColumn("Margen $", format="%.0f"),
                "margen_pct": st.column_config.NumberColumn("Margen %", format="percent"),
                "unidades_90d": "Unid. vendidas",
                "base_cargos": "Base",
                "estado": "Estado",
                "detalle": "Detalle",
            })

        st.download_button(
            "Descargar el análisis", df.to_csv(index=False).encode("utf-8"),
            f"rentabilidad_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv")

        st.caption(
            "Los cargos salen del promedio real por unidad de las ventas del "
            "período elegido. Los SKU con `base_cargos = sin_ventas` no "
            "registraron ventas: ahí el margen no descuenta comisión.")

        st.divider()
        st.markdown("##### Usar estos márgenes para el tope de publicidad")
        st.caption(
            "El proceso de publicidad apaga un anuncio cuando su ACOS supera "
            "lo que **ese producto** banca según su margen — no un número "
            "único para todo el catálogo. El equilibrio es ACOS = margen: "
            "gastar en publicidad el mismo porcentaje que deja el producto se "
            "come toda la ganancia. Se usa el "
            f"**{publicidad.config().get('factor_margen', 0.6):.0%} del "
            "margen**, así queda ganancia.")

        m_ant, f_ant = publicidad.margenes_por_sku()
        if m_ant:
            st.caption(f"Hay {len(m_ant)} SKU guardados, medidos el {f_ant}.")
        if st.button("Guardar los márgenes para publicidad", key="rent_mg"):
            ok, det = publicidad.guardar_margenes(df)
            if ok:
                nuevos, _ = publicidad.margenes_por_sku()
                st.success(f"{len(nuevos)} SKU guardados. El próximo análisis "
                           "de publicidad los usa.")
            else:
                st.error(str(det))

elif seccion == "Publicidad":
    st.markdown("#### Publicidad")
    st.caption(
        "Tres anunciantes, uno por marca, con una campaña cada uno. La capa "
        "de campañas es chica; lo que mueve la aguja son los anuncios.")

    dias_pub = st.selectbox("Período a medir", [7, 15, 30, 60], index=2,
                            format_func=lambda d: f"últimos {d} días")
    hasta_pub = datetime.now().date() - timedelta(days=1)
    desde_pub = hasta_pub - timedelta(days=dias_pub - 1)

    # Con st.tabs Streamlit ejecuta y renderiza las TRES vistas en cada rerun
    # —incluida la que baja miles de anuncios de la API— y ademas las apila
    # visualmente mientras recalcula. Se elige con un selector para que en el
    # DOM exista solo la vista activa.
    _VISTAS_PUB = ["Cómo va", "Qué haría con los anuncios",
                   "Correr el proceso", "Topes y estratégicos"]
    vista_pub = st.segmented_control(
        "Vista", _VISTAS_PUB, default=_VISTAS_PUB[0],
        key="pub_vista", label_visibility="collapsed") or _VISTAS_PUB[0]

    _sesion_del_panel()

    if vista_pub == "Cómo va":
        if st.button("Traer campañas"):
            try:
                with st.spinner("Leyendo publicidad..."):
                    st.session_state["pub_camp"] = [
                        (a, publicidad.campanas(ml, a["advertiser_id"]))
                        for a in publicidad.anunciantes(ml)]
            except Exception as e:
                st.error(f"No pude leer publicidad: {type(e).__name__}: {e}")
                st.stop()

        camps = st.session_state.get("pub_camp")
        if camps:
            for a, cs in camps:
                st.markdown(f"**{a['advertiser_name']}** · anunciante "
                            f"`{a['advertiser_id']}`")
                for c in cs:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Campaña", c["name"])
                    m2.metric("Estado", c["status"])
                    m3.metric("Presupuesto", pesos(c.get("budget") or 0))
                    m4.metric("ACOS objetivo", f"{c.get('acos_target', 0):.0f}%")
                st.divider()

            with st.expander("Crear una campaña"):
                if not _sesion_panel_viva(
                        st.session_state.get("sesion_sello", 0))[0]:
                    st.caption("Hace falta la cookie del panel: cargala en el "
                               "cartel de arriba.")
                else:
                    nom = st.text_input("Nombre", key="nc_nombre")
                    d1, d2, d3 = st.columns(3)
                    adv_nc = d1.selectbox(
                        "Anunciante", [a["advertiser_id"] for a, _ in camps],
                        format_func=lambda i: next(
                            a["advertiser_name"] for a, _ in camps
                            if a["advertiser_id"] == i), key="nc_adv")
                    pres_nc = d2.number_input("Presupuesto", 1000, 999999,
                                              20000, 1000, key="nc_pres")
                    acos_nc = d3.number_input("ACOS objetivo %", 1, 100, 15,
                                              key="nc_acos")
                    st.caption(
                        "**Nace pausada.** Una campaña con presupuesto "
                        "empieza a gastar apenas se activa, así que "
                        "encenderla es un paso aparte.")
                    if st.button("Crear", key="nc_go",
                                 disabled=not nom.strip()):
                        ok, det = panel_ads.crear_campana(
                            panel_ads.leer_sesion(), adv_nc, nom.strip(),
                            pres_nc, acos_nc)
                        if ok:
                            st.success(f"Creada con id {det}. Está pausada.")
                            st.session_state.pop("pub_camp", None)
                        else:
                            st.error(str(det))

    elif vista_pub == "Qué haría con los anuncios":
        st.caption(
            f"Mide del {desde_pub:%d/%m} al {hasta_pub:%d/%m}. Son ~1.500 "
            "anuncios por anunciante, así que la lectura tarda unos minutos.")

        cfg = publicidad.config()
        st.markdown("Topes vigentes: **ACOS máx** {:.0f}% · **ROAS mín** "
                    "{:.1f} · se ignora lo que tenga menos de {:.0f} clics"
                    .format(cfg["acos_max"], cfg["roas_min"],
                            cfg["clicks_minimos"]))

        # Los candidatos a entrar en campana salen del mismo analisis de
        # Visitas vs ventas, que tarda varios minutos: se reusa el que ya
        # este en memoria en vez de recalcularlo.
        conv_pub = st.session_state.get("conv")
        if conv_pub is None and (Path(__file__).parent / "conversion.csv").exists():
            conv_pub = pd.read_csv(Path(__file__).parent / "conversion.csv")
        if conv_pub is None:
            st.caption(
                "Para proponer **qué sumar a las campañas** hace falta el "
                "análisis de *Visitas vs ventas* (Oportunidades). Sin eso, "
                "acá solo se evalúan los anuncios que ya existen.")

        if st.button("Analizar los anuncios"):
            paso = st.empty()
            try:
                with st.spinner("Bajando anuncios y métricas..."):
                    df_ads, advs_pub, camps_pub = publicidad.traer_todo(
                        ml, desde_pub.isoformat(), hasta_pub.isoformat(),
                        callback=lambda m: paso.caption(str(m)))
                    plan_ads = publicidad.analizar(df_ads, pubs)
                    nuevos = publicidad.candidatos(
                        conv_pub, pubs, df_ads, advs_pub, camps_pub)
                    if len(nuevos):
                        plan_ads = pd.concat([plan_ads, nuevos],
                                             ignore_index=True)
                    st.session_state["pub_plan"] = plan_ads
                    # Hacen falta para resolver los candidatos a sumar: sin
                    # los estados de campaña no se puede distinguir "activar
                    # donde está" de "mudarlo a una campaña que corra".
                    st.session_state["pub_camps"] = {
                        c["id"]: c.get("status")
                        for cs in (camps_pub or {}).values() for c in cs}
            except Exception as e:
                paso.empty()
                st.error(f"No pude analizar: {type(e).__name__}: {e}")
                st.stop()
            paso.empty()

        pl = st.session_state.get("pub_plan")
        if pl is not None and len(pl):
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Anuncios", len(pl))
            g2.metric("Gasto", pesos(pl["gasto"].sum()))
            g3.metric("Facturado", pesos(pl["facturado"].sum()))
            acos_gral = (pl["gasto"].sum() / pl["facturado"].sum() * 100
                         if pl["facturado"].sum() else 0)
            g4.metric("ACOS general", f"{acos_gral:.0f}%")

            resumen_pub = (pl.groupby("accion")
                           .agg(anuncios=("item_id", "size"),
                                gasto=("gasto", "sum"),
                                unidades=("unidades", "sum")).reset_index())
            st.dataframe(resumen_pub, use_container_width=True,
                         hide_index=True,
                         column_config={
                             "accion": "Qué haría",
                             "gasto": st.column_config.NumberColumn(
                                 "Gasto", format="%.0f"),
                             "unidades": st.column_config.NumberColumn(
                                 "Unidades", format="%.0f")})

            apagar = pl[pl["accion"] == "pausar"]
            if len(apagar):
                st.warning(
                    f"**{len(apagar)} anuncios** gastaron "
                    f"{pesos(apagar['gasto'].sum())} y facturaron "
                    f"{pesos(apagar['facturado'].sum())}.", icon="🔥")

            sumar = pl[pl["accion"] == "agregar"]
            if len(sumar) and "campana_activa" in sumar:
                dormidas = int((~sumar["campana_activa"].fillna(False)).sum())
                if dormidas:
                    st.warning(
                        f"**{dormidas} de las {len(sumar)} irían a una "
                        "campaña pausada** y ahí no van a gastar ni a "
                        "mostrarse. La campaña general (Crafters) está en "
                        "pausa: si querés que corran, hay que activarla.",
                        icon="😴")
            if len(sumar):
                st.info(
                    f"**{len(sumar)} publicaciones convierten y no se "
                    "publicitan.** Salen de *Visitas vs ventas*: ya "
                    "demostraron que venden, les falta gente que las vea. No "
                    "entran las que tienen visitas y no venden — ahí el "
                    "problema es el precio o las fotos, y pagar clics no lo "
                    "arregla.", icon="🎯")

            ver = st.selectbox("Ver", ["pausar", "agregar", "activar",
                                       "revisar", "ninguna"], index=0,
                               key="pub_ver")
            v = pl[pl["accion"] == ver]
            st.dataframe(
                v[["sku", "titulo", "anunciante", "estado_ad", "gasto",
                   "facturado", "unidades", "acos", "roas", "motivo"]],
                use_container_width=True, height=380, hide_index=True,
                column_config={
                    "sku": "SKU", "titulo": "Título",
                    "anunciante": "Campaña", "estado_ad": "Estado",
                    "gasto": st.column_config.NumberColumn(
                        "Gasto", format="%.0f"),
                    "facturado": st.column_config.NumberColumn(
                        "Facturado", format="%.0f"),
                    "unidades": st.column_config.NumberColumn(
                        "Unid.", format="%.0f"),
                    "acos": st.column_config.NumberColumn(
                        "ACOS %", format="%.0f"),
                    "roas": st.column_config.NumberColumn(
                        "ROAS", format="%.1f"),
                    "motivo": "Por qué"})

            st.download_button(
                "Descargar el plan",
                pl.to_csv(index=False).encode("utf-8"),
                f"publicidad_{datetime.now():%Y%m%d}.csv", "text/csv",
                key="pub_dl")

            st.divider()
            st.markdown("##### Aplicar en MercadoLibre")

            if _sesion_panel_viva(st.session_state.get('sesion_sello', 0))[0]:
                st.info(
                    "Los cambios se aplican por el **panel de publicidad**, "
                    "no por la API: MercadoLibre no habilitó la escritura de "
                    "Product Ads para esta aplicación. Funciona con la cookie "
                    "`ssid` guardada en los secrets.", icon="🔑")
            else:
                st.error(
                    "**No hay forma de aplicar los cambios ahora mismo.** La "
                    "API de MercadoLibre rechaza toda escritura de publicidad "
                    "para esta aplicación —*«User does not have permission to "
                    "write»*, y falla igual con la cuenta dueña de los "
                    "anunciantes— y tampoco está cargada la sesión del panel, "
                    "que es la vía alternativa. Para habilitarla hay que "
                    "poner la cookie `ssid` en los secrets, bajo "
                    "`[ads]`.", icon="🔒")

            n_apagar = int((pl["accion"] == "pausar").sum())
            n_sumar = int((pl["accion"] == "agregar").sum())
            n_prender = int((pl["accion"] == "activar").sum())

            como_apagar = st.radio(
                f"A los {n_apagar} que hay que apagar",
                ["Pausarlos (quedan en la campaña)",
                 "Sacarlos de la campaña (quedan en idle)",
                 "No tocarlos"],
                key="pub_apagar", horizontal=False)

            ejecutar = pl.copy()
            elegidas = []
            if como_apagar.startswith("Pausar"):
                elegidas.append("pausar")
            elif como_apagar.startswith("Sacar"):
                # Las reglas marcan 'pausar'; sacarlas de la campaña es la
                # misma decision con otra intensidad.
                ejecutar.loc[ejecutar["accion"] == "pausar", "accion"] = "sacar"
                elegidas.append("sacar")

            if n_sumar and st.checkbox(
                    f"Sumar las {n_sumar} que convierten y no se publicitan",
                    key="pub_sumar"):
                elegidas.append("agregar")
            if n_prender and st.checkbox(
                    f"Encender las {n_prender} apagadas que rinden",
                    key="pub_prender"):
                elegidas.append("activar")

            elegidas = tuple(elegidas)
            cuantas = (int(ejecutar["accion"].isin(elegidas).sum())
                       if elegidas else 0)

            if any(a in elegidas for a in ("agregar", "activar")):
                st.warning(
                    "Estás incluyendo acciones que **empiezan a gastar**. Un "
                    "anuncio que entra a una campaña arranca activo.",
                    icon="💸")

            if "agregar" in elegidas and len(sumar):
                dormidas_g = sumar[~sumar.get(
                    "campana_activa", pd.Series(True, index=sumar.index))
                    .fillna(False)]
                if len(dormidas_g):
                    st.error(
                        f"**{len(dormidas_g)} de las que vas a sumar van a "
                        "una campaña pausada, así que se va a prender.** Y "
                        "prender una campaña enciende **todo lo que ya tiene "
                        "adentro**, no solo lo que estás agregando: la "
                        "general de Crafters tiene 4.557 anuncios, ~1.550 en "
                        "estado corrible, con un tope de \\$78.859. Eso es "
                        "empezar a gastar en mil quinientos anuncios que "
                        "nadie revisó, no sumar 24.", icon="🚨")

            # ---- Revisar contra ML antes de escribir -------------------
            # **El plan de la tabla no sirve para escribir tal cual.** Sale de
            # `ads/search`, que viene atrasado y trae el `ad_group` del
            # anunciante donde el anuncio está *delegado* —sin campaña— en vez
            # del que realmente corre. Aplicar así devolvía 409 en 856 de
            # 1.104. Acá se le vuelve a preguntar a ML uno por uno.
            # `sacar` queda afuera: usa otro endpoint y sus fallas benignas
            # ya están contempladas aparte.
            revisables = tuple(a for a in elegidas
                               if a in ("pausar", "activar", "agregar"))
            firma = (elegidas, cuantas, como_apagar)
            if st.session_state.get("pub_firma") != firma:
                for k in ("pub_plan_rev", "pub_desc", "pub_arr"):
                    st.session_state.pop(k, None)

            if revisables and st.button(
                    "Revisar contra MercadoLibre", key="pub_rev"):
                b2 = st.progress(0.0, text="Resolviendo...")
                plan_r, desc_r = [], []

                # ---- apagar: el ad_group que de verdad corre --------------
                f_apagar = ejecutar[ejecutar["accion"] == "pausar"]
                if "pausar" in revisables and len(f_apagar):
                    pr, de = panel_ads.resolver_para_escribir(
                        ml, f_apagar, accion="pausar",
                        callback=lambda i, t, d: b2.progress(
                            min(i / max(t, 1), 1.0),
                            text=f"apagar: {i} de {t}"))
                    plan_r.append(pr)
                    desc_r.append(de)

                # ---- sumar y prender: lo mismo, con la función que ya
                # existía y que hasta ahora sólo usaba el cron ------------
                # `ads/search` **no devuelve los anuncios sin actividad en la
                # ventana**, así que muchos candidatos llegan sin ad_group, y
                # los que llegan con uno traen el del anunciante donde el
                # anuncio está delegado. Sumar así entra activo y gastando
                # sobre el ad_group equivocado.
                sumables = tuple(a for a in revisables
                                 if a in ("agregar", "activar"))
                f_sumar = ejecutar[ejecutar["accion"].isin(sumables)]
                if len(f_sumar):
                    b2.progress(0.0, text="Resolviendo los que se suman...")
                    rc = publicidad.resolver_candidatos(
                        ml, f_sumar,
                        estados_camp=st.session_state.get("pub_camps") or {},
                        callback=lambda m: b2.progress(0.0, text=str(m)))
                    if rc is not None and len(rc):
                        # `resolver_candidatos` puede cambiar la acción
                        # (agregar ↔ activar) o descartarla con el motivo.
                        sirven = rc[rc["accion"].isin(sumables)
                                    & rc["ad_group_id"].notna()]
                        fuera = rc[~rc.index.isin(sirven.index)].copy()
                        if len(fuera):
                            fuera["descarte"] = fuera.get(
                                "motivo", "no se puede sumar")
                            desc_r.append(fuera)
                        plan_r.append(sirven)
                plan_r = (pd.concat(plan_r, ignore_index=True)
                          if plan_r else pd.DataFrame())
                b2.progress(1.0, text="Buscando publicaciones de arrastre...")
                # Se mira por acción: apagando molesta la hermana que corre,
                # sumando molesta la que está quieta y arrancaría a gastar.
                arrs = []
                for acc, ests in panel_ads.ARRASTRE.items():
                    f_acc = (plan_r[plan_r["accion"] == acc]
                             if len(plan_r) else plan_r)
                    if not len(f_acc):
                        continue
                    a = panel_ads.hermanos_arrastrados(ml, f_acc, estados=ests)
                    if len(a):
                        a["por"] = acc
                        arrs.append(a)
                st.session_state["pub_arr"] = (
                    pd.concat(arrs, ignore_index=True) if arrs
                    else pd.DataFrame())
                b2.empty()
                st.session_state["pub_plan_rev"] = plan_r
                st.session_state["pub_desc"] = (
                    pd.concat(desc_r, ignore_index=True)
                    if desc_r else pd.DataFrame())
                st.session_state["pub_firma"] = firma

            plan_rev = st.session_state.get("pub_plan_rev")
            if plan_rev is not None:
                desc = st.session_state.get("pub_desc")
                arr = st.session_state.get("pub_arr")
                n_ag = (plan_rev["ad_group_id"].nunique()
                        if len(plan_rev) else 0)
                st.info(
                    f"**Quedan {len(plan_rev)} publicaciones para tocar, en "
                    f"{n_ag} anuncios de MercadoLibre.**", icon="🔍")

                if desc is not None and len(desc):
                    st.warning(
                        f"**{len(desc)} se descartaron**: pedírselas al panel "
                        "devuelve error y no cambia nada.", icon="🧹")
                    with st.expander(f"Ver las {len(desc)} descartadas"):
                        st.dataframe(
                            desc["descarte"].value_counts()
                            .rename_axis("motivo").reset_index(name="cuántas"),
                            use_container_width=True, hide_index=True)
                        st.dataframe(desc, use_container_width=True,
                                     hide_index=True)

                if arr is not None and len(arr):
                    n_off = int((arr["por"] == "pausar").sum())
                    n_on = len(arr) - n_off
                    partes_arr = []
                    if n_off:
                        partes_arr.append(
                            f"**se apagan {n_off} publicaciones más** que hoy "
                            "están corriendo")
                    if n_on:
                        partes_arr.append(
                            f"**se encienden {n_on} publicaciones más**, que "
                            "arrancan a gastar")
                    st.error(
                        "Un anuncio de MercadoLibre no es una publicación: es "
                        "una *familia*, y el estado vive en la familia. Al "
                        f"tocar los {n_ag} de arriba, de arrastre "
                        + " y ".join(partes_arr) + " y que no están en el "
                        "plan.", icon="👨‍👩‍👧")
                    with st.expander(f"Ver las {len(arr)} de arrastre"):
                        st.dataframe(arr, use_container_width=True,
                                     hide_index=True)

            op_pub = st.text_input("Tu nombre (queda en el registro)",
                                   key="pub_op")
            conf_pub = st.checkbox(
                f"Confirmo que quiero aplicar {cuantas} cambios en la "
                "publicidad de MercadoLibre", key="pub_conf")
            # Sin revisar no se aplica: es el paso que evita mandarle al
            # panel 856 anuncios que no puede tocar.
            falta_revisar = bool(revisables) and plan_rev is None
            if falta_revisar:
                st.caption("Primero **Revisar contra MercadoLibre**: sin eso "
                           "no se sabe qué anuncio hay que tocar.")
            if st.button(f"Aplicar {cuantas} cambios", key="pub_go",
                         disabled=not (conf_pub and op_pub.strip() and cuantas
                                       and not falta_revisar
                                       and _sesion_panel_viva(
                                           st.session_state.get('sesion_sello', 0))[0])):
                barra = st.progress(0.0, text="Aplicando...")
                try:
                    sesion_ads = panel_ads.leer_sesion()
                    partes = []
                    # Sumar a una campaña pausada no sirve: el anuncio entra
                    # activo pero la campaña no corre.
                    if "agregar" in elegidas:
                        st.session_state["pub_prendidas"] = (
                            panel_ads.despertar_campanas(
                                sesion_ads, ml,
                                (plan_rev if plan_rev is not None
                                 else ejecutar).pipe(
                                    lambda d: d[d["accion"] == "agregar"]),
                                callback=lambda m: barra.progress(
                                    0.0, text=str(m))))
                    # Cada acción va por separado: el endpoint de sacar de
                    # campaña es otro y acepta lotes mucho más chicos.
                    for acc in elegidas:
                        # Para pausar y activar va el plan ya resuelto contra
                        # ML, con el ad_group que de verdad corre; para sacar
                        # y agregar, el original (tienen sus propias reglas).
                        base_acc = (plan_rev if acc in revisables
                                    and plan_rev is not None else ejecutar)
                        filas = base_acc[base_acc["accion"] == acc]
                        if not len(filas):
                            continue
                        partes.append(panel_ads.aplicar(
                            sesion_ads, ml, filas, accion=acc,
                            callback=lambda i, t, d: barra.progress(
                                min(i / max(t, 1), 1.0),
                                text=f"{acc}: {i} de {t} ({d})")))
                    res_pub = (pd.concat(partes, ignore_index=True)
                               if partes else pd.DataFrame())
                except Exception as e:
                    barra.empty()
                    st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
                    st.stop()
                barra.empty()
                st.session_state["pub_res"] = res_pub
                # La revisión ya se usó: dejarla en pantalla invita a
                # aplicarla de nuevo sobre estados que acaban de cambiar.
                for k in ("pub_plan_rev", "pub_desc", "pub_arr", "pub_firma"):
                    st.session_state.pop(k, None)

            prendidas = st.session_state.get("pub_prendidas")
            if prendidas:
                st.warning(
                    "Se prendieron campañas para que los anuncios nuevos "
                    "corran: " + ", ".join(
                        f"**{c['nombre']}** (tope {pesos(c['presupuesto'] or 0)})"
                        for c in prendidas)
                    + ". Con eso también arrancó todo lo que ya tenían "
                      "adentro.", icon="🔛")

            res_pub = st.session_state.get("pub_res")
            if res_pub is not None and len(res_pub):
                ok_pub = int((res_pub["resultado"] == "OK").sum())
                if ok_pub == len(res_pub):
                    st.success(f"{ok_pub} anuncios actualizados.")
                else:
                    st.error(f"{ok_pub} aplicados, "
                             f"{len(res_pub) - ok_pub} con error.")
                    if res_pub["detalle"].astype(str).str.contains(
                            "permission|401|503", case=False).any():
                        st.info(
                            "Los errores dicen que falta permiso: es lo de "
                            "arriba, no un problema de esta pantalla.",
                            icon="🔒")
                st.dataframe(res_pub, use_container_width=True,
                             hide_index=True)

    elif vista_pub == "Correr el proceso":
        st.caption(
            "Lo mismo que corre solo los martes a las 9: mide, apaga lo que "
            "pasa el ACOS objetivo y suma lo que convierte y no se publicita. "
            "**Sin topes**: hace todo lo que califica.")

        conv_ya = st.session_state.get("conv")
        if conv_ya is not None and len(conv_ya):
            st.caption(f"Va a reusar el análisis de *Visitas vs ventas* que "
                       f"ya está en memoria ({len(conv_ya)} publicaciones), "
                       "así que tarda unos 2 minutos.")
        else:
            st.warning(
                "No hay análisis de *Visitas vs ventas* en memoria, así que "
                "lo va a medir: es **una llamada por publicación** y tarda "
                "unos 15 minutos. No cierres la pestaña. Si primero corrés "
                "esa sección (en Oportunidades), esto baja a 2 minutos.",
                icon="⏳")

        aplicar_pub = st.checkbox(
            "Aplicar de verdad (sin tildar, solo muestra qué haría)",
            key="cron_aplicar")
        if aplicar_pub:
            st.error(
                "Va a **apagar y encender anuncios de verdad**, sin tope de "
                "cantidad. Encender gasta desde el momento; el único límite "
                "es el presupuesto de cada campaña.", icon="🚨")

        if st.button("Correr el proceso ahora", key="cron_go",
                     type="primary" if aplicar_pub else "secondary",
                     disabled=aplicar_pub and not _sesion_panel_viva(
                         st.session_state.get('sesion_sello', 0))[0]):
            caja = st.empty()
            lineas = []

            def _log(m):
                lineas.append(str(m))
                # Solo el final: el log entero son cientos de líneas y
                # repintarlo completo en cada paso vuelve la app un plomo.
                caja.code("\n".join(lineas[-18:]), language=None)

            try:
                with st.spinner("Corriendo..."):
                    publicidad_cron.correr(aplicar=aplicar_pub, log=_log,
                                           conv=conv_ya, ml=ml)
            except Exception as e:
                _log(f"\nSE CORTÓ: {type(e).__name__}: {e}")
                st.error(f"La corrida se cortó: {type(e).__name__}: {e}")
            st.session_state["cron_log"] = "\n".join(lineas)
            if aplicar_pub:
                # Los estados cambiaron: lo que estaba en pantalla quedó viejo.
                # La revisión también: apunta a ad_groups que ya se movieron.
                for k in ("pub_plan", "pub_plan_rev", "pub_desc", "pub_arr",
                          "pub_firma"):
                    st.session_state.pop(k, None)

        if st.session_state.get("cron_log"):
            st.download_button(
                "Descargar el log completo",
                st.session_state["cron_log"].encode("utf-8"),
                f"publicidad_{datetime.now():%Y%m%d_%H%M}.txt", "text/plain",
                key="cron_dl")

    elif vista_pub == "Topes y estratégicos":
        st.caption(
            "Los topes y la lista de estratégicos viven en la Google Sheet, "
            "no en un archivo: en la nube el disco se borra en cada deploy.")

        cfg = publicidad.config()
        t1, t2 = st.columns(2)
        nuevo_cfg = {}
        with t1:
            nuevo_cfg["acos_max"] = st.number_input(
                "ACOS máximo %", 1.0, 200.0, float(cfg["acos_max"]), 1.0,
                help="Arriba de esto el anuncio se pausa.")
            nuevo_cfg["roas_min"] = st.number_input(
                "ROAS mínimo", 0.1, 50.0, float(cfg["roas_min"]), 0.1)
            nuevo_cfg["gasto_minimo"] = st.number_input(
                "Gasto mínimo para juzgar", 0.0, 999999.0,
                float(cfg["gasto_minimo"]), 500.0)
        with t2:
            nuevo_cfg["acos_bueno"] = st.number_input(
                "ACOS bueno %", 1.0, 200.0, float(cfg["acos_bueno"]), 1.0,
                help="Debajo de esto, un anuncio apagado se propone encender.")
            nuevo_cfg["roas_bueno"] = st.number_input(
                "ROAS bueno", 0.1, 50.0, float(cfg["roas_bueno"]), 0.1)
            nuevo_cfg["clicks_minimos"] = st.number_input(
                "Clics mínimos para juzgar", 0, 10000,
                int(cfg["clicks_minimos"]), 5)

        if st.button("Guardar topes"):
            ok, det = publicidad.guardar_config(nuevo_cfg)
            st.success("Topes guardados.") if ok else st.error(det)

        st.divider()
        st.markdown("##### SKU estratégicos")
        st.caption(
            "Estos SKU **no los toca ninguna regla**, ganen o pierdan. Son "
            "los que se publicitan por decisión comercial: lanzamientos, los "
            "que traen tráfico, los que se defienden de un competidor. Sin "
            "esta lista, la primera corrida los apaga a todos.")

        est = publicidad.estrategicos()
        df_est = pd.DataFrame(
            [{"sku": k, "nota": v} for k, v in est.items()]
            or [{"sku": "", "nota": ""}])
        editado = st.data_editor(df_est, num_rows="dynamic",
                                 use_container_width=True, key="pub_est",
                                 column_config={"sku": "SKU",
                                                "nota": "Por qué"})
        if st.button("Guardar estratégicos"):
            filas = [{"sku": str(r["sku"]).strip().upper(),
                      "nota": str(r["nota"] or "")}
                     for _, r in editado.iterrows()
                     if str(r.get("sku", "")).strip()]
            ok, det = publicidad.guardar_estrategicos(filas)
            st.success(f"{len(filas)} SKU guardados.") if ok else st.error(det)


# ============================================================ promos por planilla

elif seccion == "PROMOS":
    st.markdown("#### Descuentos en lote")
    modo_pp = st.segmented_control(
        "Modo", ["Desde planilla", "Replicar una campaña", "Activar por regla",
                 "Igualar la mejor propia"],
        default="Desde planilla", key="modo_pp",
        label_visibility="collapsed") or "Desde planilla"

    @st.cache_data(ttl=600, show_spinner=False)
    def _camps_todas(_ml, sello):
        return promos_campanas.campanas(_ml)

    @st.cache_data(ttl=600, show_spinner=False)
    def _camps_propias(_ml, sello):
        return promos_planilla.campanas_propias(_ml)

    @st.cache_data(ttl=600, show_spinner=False)
    def _elegibles_pp(_ml, campana_id, sello):
        return promos_planilla.elegibles(_ml, campana_id)

    if "sello_promos" not in st.session_state:
        st.session_state["sello_promos"] = 0
    sello_pp = st.session_state["sello_promos"]

    st.button("↻ Releer campañas", key="rl_pp",
              on_click=lambda: st.session_state.__setitem__(
                  "sello_promos", st.session_state["sello_promos"] + 1))

    # ---------------------------------------------------------------- planilla
    if modo_pp == "Desde planilla":
        st.caption(
            "Subís una planilla con una columna de **SKU o EAN** (también sirve "
            "el código MLA) y otra con el **descuento en porcentaje**, y cada "
            "producto entra a la campaña con ese descuento. Los que no estén en "
            "la planilla no se tocan.")

        try:
            camps = _camps_propias(ml, sello_pp)
        except Exception as e:
            st.error(f"No pude traer las campañas: {e}")
            st.stop()
        if not len(camps):
            st.warning(
                "**No hay ninguna campaña propia vigente.** Se crean desde el "
                "panel de MercadoLibre, en *Publicaciones → Promociones → Crear "
                "campaña propia*. Por API no se pueden crear: el pedido "
                "contesta que sí y no crea nada.", icon="⚠️")
            st.stop()

        etiquetas = {f"{c['nombre']}  ·  hasta el {c['hasta']}": c["campana_id"]
                     for _, c in camps.iterrows()}
        campana_id = etiquetas[st.selectbox("Campaña", list(etiquetas),
                                            key="camp_pp")]

        with st.spinner("Preguntándole a MercadoLibre qué publicaciones acepta..."):
            try:
                eleg = _elegibles_pp(ml, campana_id, sello_pp)
            except Exception as e:
                st.error(f"No pude traer las elegibles: {e}")
                st.stop()

        e1, e2, e3 = st.columns(3)
        e1.metric("Publicaciones que acepta", cumplen(len(eleg)))
        e2.metric("Ya con descuento",
                  sum(1 for e in eleg.values() if e["estado_promo"] == "started"))
        minimos = [1 - e["max_precio"] / e["original_price"]
                   for e in eleg.values()
                   if e.get("max_precio") and e.get("original_price")]
        maximos = [1 - e["min_precio"] / e["original_price"]
                   for e in eleg.values()
                   if e.get("min_precio") and e.get("original_price")]
        if minimos:
            e3.metric("Descuento admitido",
                      f"{min(minimos):.0%} a {max(maximos):.0%}")
            st.caption(
                f"El rango lo fija MercadoLibre **por publicación**, no por "
                f"campaña: el mínimo va de {min(minimos):.1%} a "
                f"{max(minimos):.1%} según el artículo. Lo que quede afuera se "
                f"marca en la simulación y no se aplica.")

        archivo_pp = st.file_uploader("Planilla (.xlsx o .csv)",
                                      type=["xlsx", "xls", "csv"], key="up_pp")
        if not archivo_pp:
            st.session_state.pop("sim_pp", None)
            st.stop()
        try:
            df_pp = promos_planilla.leer_planilla(archivo_pp)
        except Exception as e:
            st.error(f"No pude leer la planilla: {e}")
            st.stop()

        ck_auto, cp_auto = promos_planilla.detectar_columnas(df_pp)
        cols_pp = list(df_pp.columns)
        p1, p2, p3 = st.columns([2, 2, 1])
        col_clave_pp = p1.selectbox(
            "Columna de SKU / EAN / MLA", cols_pp,
            index=cols_pp.index(ck_auto) if ck_auto in cols_pp else 0,
            key="ck_pp")
        col_pct_pp = p2.selectbox(
            "Columna de descuento", cols_pp,
            index=cols_pp.index(cp_auto) if cp_auto in cols_pp else 0,
            key="cp_pp")
        p3.metric("Filas", cumplen(len(df_pp)))
        st.caption("El descuento se lee igual escrito `30`, `30%`, `0,30` o "
                   "`0.3`. De 1 para abajo se toma como fracción.")

        if st.button("Simular los descuentos", key="sim_btn_pp"):
            try:
                st.session_state["sim_pp"] = promos_planilla.simular(
                    df_pp, pubs, eleg, col_clave_pp, col_pct_pp, ml=ml)
            except Exception as e:
                st.error(f"Error al simular: {e}")

        sim_pp = st.session_state.get("sim_pp")
        if sim_pp is None or not len(sim_pp):
            st.stop()

        rc = promos_planilla.resumen(sim_pp)
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Altas nuevas", rc.get("alta", 0))
        q2.metric("Cambian de precio", rc.get("actualizar", 0))
        q3.metric("Ya estaban igual", rc.get("sin_cambio", 0))
        q4.metric("Fuera de rango", rc.get("fuera_de_rango", 0))

        st.dataframe(sim_pp, use_container_width=True, height=340,
                     hide_index=True,
                     column_config={"descuento": st.column_config.NumberColumn(
                         "Descuento", format="percent")})

        n_pp = rc.get("alta", 0) + rc.get("actualizar", 0)
        if not n_pp:
            st.info("No hay nada para aplicar.")
            st.stop()
        st.error(f"**Esto carga {n_pp} publicaciones a la campaña de verdad** y "
                 "cambia el precio que ve el comprador.", icon="⚠️")
        o1, o2 = st.columns([2, 3])
        op_pp = o1.text_input("Tu nombre (queda en el registro)", key="op_pp")
        conf_pp = o2.checkbox(f"Confirmo cargar {n_pp} publicaciones",
                              key="conf_pp")
        if st.button("Cargar a la campaña", key="go_pp",
                     disabled=not (conf_pp and op_pp.strip())):
            barra = st.progress(0.0, text="Cargando...")
            st.session_state["res_pp"] = promos_planilla.aplicar(
                ml, sim_pp, campana_id, operador=op_pp.strip(),
                callback=lambda i, t, iid: barra.progress(
                    i / t, text=f"Cargando {i} de {t}: {iid}"))
            barra.empty()
        res_pp = st.session_state.get("res_pp")
        if res_pp is not None and len(res_pp):
            ok = int((res_pp["resultado"] == "OK").sum())
            (st.success if ok == len(res_pp) else st.error)(
                f"{ok} cargadas, {len(res_pp) - ok} con error.")
            st.dataframe(res_pp, use_container_width=True, height=260,
                         hide_index=True)

    # ---------------------------------------------------------------- replicar
    elif modo_pp == "Replicar una campaña":
        st.caption(
            "Copia los descuentos de una campaña a otra. **Copia el "
            "porcentaje, no el importe**: entre una campaña y la otra los "
            "precios se movieron, y repetir el precio viejo aplicaría un "
            "descuento distinto al que quisiste.")
        try:
            todas = _camps_todas(ml, sello_pp)
        except Exception as e:
            st.error(f"No pude traer las campañas: {e}")
            st.stop()
        if not len(todas):
            st.warning("No hay campañas disponibles.")
            st.stop()

        propias = todas[todas["tipo"] == "SELLER_CAMPAIGN"]
        et_todas = {f"{r['nombre'] or r['id']} · {r['nombre_tipo']} · "
                    f"{r['desde']}→{r['hasta']}": r["id"]
                    for _, r in todas.iterrows()}
        et_prop = {f"{r['nombre'] or r['id']} · {r['desde']}→{r['hasta']}":
                   r["id"] for _, r in propias.iterrows()}
        if not len(propias):
            st.warning("Para replicar hace falta al menos una campaña propia "
                       "de destino.", icon="⚠️")
            st.stop()

        r1, r2 = st.columns(2)
        origen = et_todas[r1.selectbox("Desde", list(et_todas), key="or_pp")]
        destino = et_prop[r2.selectbox("Hacia (campaña propia)",
                                       list(et_prop), key="de_pp")]
        tipos = dict(zip(todas["id"], todas["tipo"]))

        if origen == destino:
            st.info("Elegí dos campañas distintas.")
            st.stop()

        if st.button("Ver qué se replicaría", key="sim_rep"):
            caja = st.status("Leyendo las dos campañas...", expanded=True)
            try:
                st.session_state["plan_rep"] = promos_campanas.replicar(
                    ml, origen, tipos.get(origen, "SELLER_CAMPAIGN"),
                    destino, tipos.get(destino, "SELLER_CAMPAIGN"),
                    callback=caja.write)
                caja.update(label="Listo", state="complete", expanded=False)
            except Exception as e:
                caja.update(label="Falló", state="error")
                st.error(str(e))

        plan = st.session_state.get("plan_rep")
        if plan is None or not len(plan):
            st.stop()
        rr = promos_campanas.resumen(plan)
        m1, m2, m3 = st.columns(3)
        m1.metric("A dar de alta", rr.get("a dar de alta", 0))
        m2.metric("Ya estaban", rr.get("ya estaban", 0))
        m3.metric("Descuento promedio", f"{rr.get('descuento promedio', 0):.1%}")
        if rr.get("no elegibles"):
            st.warning(f"**{rr['no elegibles']} no entran en la campaña "
                       "destino.** Lo decide MercadoLibre.", icon="⚠️")
        st.dataframe(plan, use_container_width=True, height=320,
                     hide_index=True,
                     column_config={"descuento": st.column_config.NumberColumn(
                         "Descuento", format="percent")})

        n_rep = rr.get("a dar de alta", 0)
        if not n_rep:
            st.info("No hay nada para replicar.")
            st.stop()
        st.error(f"**Esto da de alta {n_rep} publicaciones** en la campaña "
                 "destino y cambia el precio que ve el comprador.", icon="⚠️")
        c1, c2 = st.columns([2, 3])
        op_rep = c1.text_input("Tu nombre", key="op_rep")
        cf_rep = c2.checkbox(f"Confirmo replicar {n_rep}", key="cf_rep")
        if st.button("Replicar", key="go_rep",
                     disabled=not (cf_rep and op_rep.strip())):
            barra = st.progress(0.0, text="Aplicando...")
            st.session_state["res_rep"] = promos_campanas.aplicar(
                ml, plan, operador=op_rep.strip(),
                callback=lambda i, t, iid: barra.progress(
                    i / t, text=f"{i} de {t}: {iid}"))
            barra.empty()
        rres = st.session_state.get("res_rep")
        if rres is not None and len(rres):
            ok = int((rres["resultado"] == "OK").sum())
            (st.success if ok == len(rres) else st.error)(
                f"{ok} replicadas, {len(rres) - ok} con error.")
            st.dataframe(rres, use_container_width=True, height=260,
                         hide_index=True)

    # ------------------------------------------- igualar la mejor propia
    elif modo_pp == "Igualar la mejor propia":
        st.caption(
            "Busca, en todas las campañas, el mayor descuento **puesto por "
            "nosotros** —descontando lo que pone MercadoLibre— y lo iguala en "
            "las demás campañas donde podemos elegir el precio.\n\n"
            "**Respeta el vencimiento.** La fecha no se puede fijar por "
            "oferta: la promo dura lo que dura la campaña. Así que si el "
            "descuento original vence antes que la campaña destino, **no se "
            "replica** — replicarlo lo estiraría.")
        t1, t2 = st.columns([2, 3])
        tope_n = t1.number_input("Tope de lo que ponemos (%)", 0, 90, 40, 5,
                                 key="tope_ig",
                                 help="0 = sin tope. Corta las que nos "
                                      "cuesten más que eso.")
        if st.button("Ver qué se igualaría", key="sim_ig"):
            caja = st.status("Buscando promociones activas...", expanded=True)
            try:
                its = promos_campanas.items_con_promo(ml, callback=caja.write)
                caja.write(f"{len(its)} publicaciones con promo; comparando...")
                st.session_state["plan_ig"] = \
                    promos_campanas.igualar_mejor_propia(
                        ml, its, callback=caja.write,
                        tope_nuestro=(tope_n / 100) if tope_n else None)
                caja.update(label="Listo", state="complete", expanded=False)
            except Exception as e:
                caja.update(label="Falló", state="error")
                st.error(str(e))

        plan_ig = st.session_state.get("plan_ig")
        if plan_ig is None or not len(plan_ig):
            st.stop()
        ri = promos_campanas.resumen(plan_ig)
        w1, w2, w3 = st.columns(3)
        w1.metric("A igualar", ri.get("a dar de alta", 0))
        w2.metric("Frenadas", ri.get("no cumplen la regla", 0))
        w3.metric("Descuento promedio", f"{ri.get('descuento promedio', 0):.1%}")
        st.caption("Las frenadas dicen por qué: casi siempre porque la "
                   "campaña destino dura más que la promo original.")
        st.dataframe(plan_ig, use_container_width=True, height=320,
                     hide_index=True,
                     column_config={
                         "descuento": st.column_config.NumberColumn(
                             "Descuento", format="percent"),
                         "min_precio": None, "max_precio": None,
                         "stock_min": None, "stock_max": None})

        n_ig = ri.get("a dar de alta", 0)
        if not n_ig:
            st.info("No hay nada para igualar.")
            st.stop()
        st.error(f"**Esto da de alta {n_ig} promociones de verdad** y cambia "
                 "el precio que ve el comprador.", icon="⚠️")
        z1, z2 = st.columns([2, 3])
        op_ig = z1.text_input("Tu nombre", key="op_ig")
        cf_ig = z2.checkbox(f"Confirmo igualar {n_ig}", key="cf_ig")
        if st.button("Igualar", key="go_ig",
                     disabled=not (cf_ig and op_ig.strip())):
            barra = st.progress(0.0, text="Aplicando...")
            st.session_state["res_ig"] = promos_campanas.aplicar(
                ml, plan_ig, operador=op_ig.strip(),
                callback=lambda i, t, iid: barra.progress(
                    i / t, text=f"{i} de {t}: {iid}"))
            barra.empty()
        rr = st.session_state.get("res_ig")
        if rr is not None and len(rr):
            ok = int((rr["resultado"] == "OK").sum())
            (st.success if ok == len(rr) else st.error)(
                f"{ok} igualadas, {len(rr) - ok} con error.")
            st.dataframe(rr, use_container_width=True, height=260,
                         hide_index=True)

    # ------------------------------------------------------------- por regla
    else:
        st.caption(
            "Acepta de una todas las ofertas que cumplan una condición. Sirve "
            "para las que **MercadoLibre arma y fija el precio** (relámpago, "
            "compartidas, de temporada): ahí no hay nada que negociar, lo "
            "único que decide es cuánto descuento te pide.\n\nEligiendo "
            "**TODAS** recorre todas las campañas de MercadoLibre de una vez. "
            "Las campañas propias quedan afuera a propósito: ahí el descuento "
            "lo elegís vos. Si una publicación entra en dos campañas, se "
            "queda en la que pide **menos** descuento.")
        try:
            todas = _camps_todas(ml, sello_pp)
        except Exception as e:
            st.error(f"No pude traer las campañas: {e}")
            st.stop()

        TODAS = "— TODAS las campañas de MercadoLibre —"
        et = {TODAS: TODAS}
        et.update({f"{r['nombre'] or r['id']} · {r['nombre_tipo']}": r["id"]
                   for _, r in todas.iterrows()})
        g1, g2 = st.columns([3, 2])
        cid = et[g1.selectbox("Campaña", list(et), key="cid_rg")]
        tope = g2.number_input("Tope de descuento (%)", 1, 60, 5, 1,
                               key="tope_rg",
                               help="Solo entran las que piden hasta ese "
                                    "descuento.")
        tipos = dict(zip(todas["id"], todas["tipo"]))
        dar_tope = st.checkbox(
            "Entrar con el tope, no con el mínimo", key="tope_full_rg",
            help="Por defecto se entra con el descuento MÍNIMO que pide cada "
                 "campaña, que es lo más barato. Tildado, se entra con el "
                 "tope de arriba: si la campaña se conforma con 3% y el tope "
                 "es 10%, va 10%. Sirve para pelear posición, porque ML "
                 "ordena las ofertas por descuento. Nunca se pasa del máximo "
                 "que ML permite en cada publicación.")

        if st.button("Ver cuáles cumplen", key="sim_rg"):
            caja = st.status("Leyendo la campaña...", expanded=True)
            objetivo = (tope / 100) if dar_tope else None
            try:
                if cid == TODAS:
                    st.session_state["plan_rg"] = \
                        promos_campanas.por_regla_todas(
                            ml, tope_descuento=tope / 100,
                            callback=caja.write,
                            descuento_objetivo=objetivo)
                else:
                    st.session_state["plan_rg"] = promos_campanas.por_regla(
                        ml, cid, tipos.get(cid, "LIGHTNING"),
                        tope_descuento=tope / 100, callback=caja.write,
                        descuento_objetivo=objetivo)
                caja.update(label="Listo", state="complete", expanded=False)
            except Exception as e:
                caja.update(label="Falló", state="error")
                st.error(str(e))

        plan_rg = st.session_state.get("plan_rg")
        if plan_rg is None or not len(plan_rg):
            st.stop()
        rg = promos_campanas.resumen(plan_rg)
        v1, v2, v3 = st.columns(3)
        v1.metric("Cumplen la regla", rg.get("a dar de alta", 0))
        v2.metric("Piden más que el tope", rg.get("no cumplen la regla", 0))
        v3.metric("Descuento promedio", f"{rg.get('descuento promedio', 0):.1%}")
        # min_precio/max_precio son el rango que ML permite elegir. Acá el
        # precio lo fija ML, así que no significan nada y confunden.
        st.dataframe(plan_rg, use_container_width=True, height=320,
                     hide_index=True,
                     column_config={
                         "descuento": st.column_config.NumberColumn(
                             "Descuento", format="percent"),
                         "precio_original": st.column_config.NumberColumn(
                             "Precio hoy", format="%.2f"),
                         "precio_promo": st.column_config.NumberColumn(
                             "Precio con la promo", format="%.2f"),
                         "min_precio": None, "max_precio": None,
                         "campana_id": None})

        n_rg = rg.get("a dar de alta", 0)
        if not n_rg:
            st.info("Ninguna oferta cumple esa condición.")
            st.stop()
        st.error(f"**Esto activa {n_rg} promociones de verdad** y cambia el "
                 "precio que ve el comprador.", icon="⚠️")
        h1, h2 = st.columns([2, 3])
        op_rg = h1.text_input("Tu nombre", key="op_rg")
        cf_rg = h2.checkbox(f"Confirmo activar {n_rg}", key="cf_rg")
        if st.button("Activar las que cumplen", key="go_rg",
                     disabled=not (cf_rg and op_rg.strip())):
            barra = st.progress(0.0, text="Activando...")
            st.session_state["res_rg"] = promos_campanas.aplicar(
                ml, plan_rg, operador=op_rg.strip(),
                callback=lambda i, t, iid: barra.progress(
                    i / t, text=f"{i} de {t}: {iid}"))
            barra.empty()
        rres = st.session_state.get("res_rg")
        if rres is not None and len(rres):
            ok = int((rres["resultado"] == "OK").sum())
            (st.success if ok == len(rres) else st.error)(
                f"{ok} activadas, {len(rres) - ok} con error.")
            st.dataframe(rres, use_container_width=True, height=260,
                         hide_index=True)



# ======================================================================= kits

elif seccion == "KITS":
    st.markdown("#### Armar kits")
    st.caption(
        "Qué conviene vender junto, y **cuánto se puede descontar sin ganar "
        "menos** que vendiéndolo suelto.")

    bajado = kits_mod.cuando_se_bajo()
    c1, c2, c3 = st.columns([2, 2, 2])
    c1.metric("Ventas analizadas", bajado or "todavía no")
    dias_k = c2.number_input("Días de historia", 30, 730,
                             kits_mod.DIAS, 30, key="dias_k")
    with c3:
        st.write("")
        rebajar = st.button("↻ Volver a bajar las ventas", key="rb_k",
                            help="12 meses son ~25.000 órdenes: tarda varios "
                                 "minutos. Después queda cacheado.")

    if rebajar:
        caja = st.status("Bajando ventas...", expanded=True)
        try:
            kits_mod.canastas(ml, dias=int(dias_k), refrescar=True,
                              callback=caja.write)
            caja.update(label="Listo", state="complete", expanded=False)
            st.cache_data.clear()
        except Exception as e:
            caja.update(label="Falló", state="error")
            st.error(str(e))

    if not bajado and not rebajar:
        st.info("Todavía no se bajaron las ventas. Apretá **Volver a bajar "
                "las ventas** para empezar.")
        st.stop()

    @st.cache_data(ttl=1800, show_spinner=False)
    def _cargos_k(sello):
        """Lo que cuesta vender cada SKU: comisión, envío y cargo fijo."""
        import rentabilidad as rent_k
        hist = json.loads((Path(__file__).resolve().parent /
                           "historico_ventas.json").read_text(encoding="utf-8"))
        envios = {}
        ruta_e = Path(__file__).resolve().parent / "costos_envio.json"
        if ruta_e.exists():
            envios = json.loads(ruta_e.read_text(encoding="utf-8"))
        return rent_k.cargos_por_sku(hist.get("ordenes", hist), envios)

    try:
        cargos_k = _cargos_k(0)
    except Exception as e:
        st.warning(f"Sin datos de costos de venta ({e}). Los ahorros usan "
                   f"una comisión típica.", icon="⚠️")
        cargos_k = None

    vista_k = st.segmented_control(
        "Vista", ["Multipacks del mismo producto", "Kits de varios productos",
                  "Los que cruzan $33.000"],
        default="Multipacks del mismo producto", key="vista_k",
        label_visibility="collapsed") or "Multipacks del mismo producto"

    st.info(
        "**El ahorro de un kit es el cargo fijo, no la comisión.** La comisión "
        "es un porcentaje y da igual cobrarla en una venta o en tres; el cargo "
        "fijo se paga **por venta**. En un producto de $2.500 son $1.250 — la "
        "mitad del precio.\n\n**Ojo con cruzar los \\$33.000**: ahí el cargo "
        "fijo se hace cero pero aparecen ~\\$7.641 de envío a cargo nuestro. "
        "Esos kits se descartan solos.", icon="💡")

    if vista_k == "Multipacks del mismo producto":
        @st.cache_data(ttl=1800, show_spinner="Calculando multipacks...")
        def _multi(sello):
            return kits_mod.multipacks(pubs=pubs, cargos=cargos_k)

        mp = _multi(st.session_state.get("sello_catalogo", 0))
        if not len(mp):
            st.info("No hay multipacks que ahorren.")
            st.stop()

        solo_firme = st.checkbox(
            "Solo los que ahorran cargo fijo (sin supuestos)", value=True,
            key="firme_k",
            help="El ahorro de envío supone que, sin el pack, habrían sido "
                 "compras separadas. El de cargo fijo es real siempre.")
        v = mp[mp["ahorro_de"] == "cargo fijo"] if solo_firme else mp

        m1, m2, m3 = st.columns(3)
        m1.metric("Multipacks", len(v))
        m2.metric("Ahorro total por venta", pesos(float(v["ahorro_ml"].sum())))
        m3.metric("Descuento que banca",
                  f"{v['descuento_que_banca'].mean():.0%} promedio")

        st.dataframe(
            v, use_container_width=True, height=380, hide_index=True,
            column_config={
                "unidades": "Unid.", "producto": "Producto",
                "precio_unidad": st.column_config.NumberColumn(
                    "Precio c/u", format="$%.0f"),
                "precio_suelto": st.column_config.NumberColumn(
                    "Sueltos", format="$%.0f"),
                "precio_kit_sugerido": st.column_config.NumberColumn(
                    "Precio del pack", format="$%.0f"),
                "ahorro_ml": st.column_config.NumberColumn(
                    "Ahorro", format="$%.0f"),
                "descuento_que_banca": st.column_config.NumberColumn(
                    "Descuento que banca", format="percent"),
                "crear_kit": st.column_config.LinkColumn(
                    "Crear", display_text="armar"),
                "sku": None, "item": None, "user_product": None,
                "origen": None, "supuesto": None,
                "ahorro_cargo_fijo": None, "ahorro_envio": None})
        st.download_button("Descargar", v.to_csv(index=False).encode("utf-8"),
                           f"multipacks_{datetime.now():%Y%m%d}.csv",
                           "text/csv", key="dl_mp")
        st.session_state["kits_para_registrar"] = v

    # El `else` suelto hacia que la vista del umbral corriera TAMBIEN esta:
    # con tres opciones ya no alcanza, hay que nombrarla.
    elif vista_k == "Kits de varios productos":
        @st.cache_data(ttl=1800, show_spinner="Buscando qué se compra junto...")
        def _varios(sello, dias):
            cs = kits_mod.canastas(ml, dias=int(dias))
            return kits_mod.kits_de_varios(cs, cargos=cargos_k, pubs=pubs)

        try:
            kv = _varios(st.session_state.get("sello_catalogo", 0), dias_k)
        except Exception as e:
            st.error(f"No pude calcular los kits: {e}")
            st.stop()
        if not len(kv):
            st.info("No hay grupos con evidencia suficiente. Probá ampliando "
                    "los días de historia.")
            st.stop()

        k1, k2, k3 = st.columns(3)
        k1.metric("Kits propuestos", len(kv))
        k2.metric("De 3 o más productos", int((kv["productos"] >= 3).sum()))
        k3.metric("Ahorro promedio", pesos(float(kv["ahorro_ml"].mean())))

        cuantos = st.multiselect(
            "Cuántos productos", sorted(kv["productos"].unique()),
            default=sorted(kv["productos"].unique()), key="cnt_k")
        v = kv[kv["productos"].isin(cuantos)]

        st.dataframe(
            v, use_container_width=True, height=380, hide_index=True,
            column_config={
                "productos": "N°", "detalle": "Kit", "motivo": "Por qué",
                "precio_suelto": st.column_config.NumberColumn(
                    "Sueltos", format="$%.0f"),
                "precio_kit_sugerido": st.column_config.NumberColumn(
                    "Precio del kit", format="$%.0f"),
                "ahorro_ml": st.column_config.NumberColumn(
                    "Ahorro", format="$%.0f"),
                "descuento_que_banca": st.column_config.NumberColumn(
                    "Descuento que banca", format="percent"),
                "crear_kit": st.column_config.LinkColumn(
                    "Crear", display_text="armar"),
                "skus": None, "items": None, "user_product": None,
                "origen": None, "juntos": None, "lift": None,
                "confianza": None, "cruza_umbral": None})
        st.download_button("Descargar", v.to_csv(index=False).encode("utf-8"),
                           f"kits_{datetime.now():%Y%m%d}.csv", "text/csv",
                           key="dl_kv")
        st.session_state["kits_para_registrar"] = v

    # ------------------------------------------- los que cruzan el umbral
    elif vista_k == "Los que cruzan $33.000":
        st.caption(
            "Un kit que cruza los \\$33.000 deja de pagar cargo fijo pero "
            "empieza a pagar el envío. Acá la pregunta no es cuánto ahorra "
            "—no ahorra— sino **cuánto cuesta**, y si el volumen extra lo "
            "justifica.")

        @st.cache_data(ttl=1800, show_spinner="Calculando rentabilidad...")
        def _cruce(sello, dias):
            cs = kits_mod.canastas(ml, dias=int(dias))
            todos = kits_mod.kits_de_varios(cs, cargos=cargos_k, pubs=pubs)
            return kits_mod.rentabilidad_del_kit(
                todos[todos["cruza_umbral"]], cargos_k)

        try:
            cru = _cruce(st.session_state.get("sello_catalogo", 0), dias_k)
        except Exception as e:
            st.error(f"No pude calcular: {e}")
            st.stop()
        if not len(cru):
            st.info("Ningún kit cruza el umbral.")
            st.stop()

        con_dato = cru[cru["veredicto"] != "sin costo"]
        u1, u2, u3 = st.columns(3)
        u1.metric("Ganan más por venta",
                  int((cru["veredicto"] == "conviene").sum()))
        u2.metric("Ganan menos, pero ganan",
                  int((cru["veredicto"] == "probar").sum()))
        u3.metric("Pierden plata", int((cru["veredicto"] == "NO").sum()))

        if len(con_dato):
            sanos = int((con_dato["veredicto"] != "NO").sum())
            malos = int((con_dato["veredicto"] == "NO").sum())
            st.success(
                f"**{sanos} de {len(con_dato)} dejan margen positivo.** Que "
                f"un kit gane menos por venta que los productos sueltos no lo "
                f"descalifica: el precio del pack empuja volumen y ese margen "
                f"se cobra más veces. **Lo que descalifica es perder plata**, "
                f"y eso pasa en {malos}.", icon="✅")
        if int((cru["veredicto"] == "sin costo").sum()):
            st.info(f"{int((cru['veredicto'] == 'sin costo').sum())} no se "
                    f"pueden evaluar porque falta el costo de algún "
                    f"componente. Cargalos en **Rentabilidad**.")

        st.dataframe(
            cru, use_container_width=True, height=380, hide_index=True,
            column_config={
                "detalle": "Kit", "veredicto": "¿Conviene?", "motivo": "Por qué",
                "precio_suelto": st.column_config.NumberColumn(
                    "Sueltos", format="$%.0f"),
                "costo": st.column_config.NumberColumn("Costo", format="$%.0f"),
                "margen_suelto": st.column_config.NumberColumn(
                    "Margen suelto", format="$%.0f"),
                "margen_kit": st.column_config.NumberColumn(
                    "Margen kit", format="$%.0f"),
                "diferencia": st.column_config.NumberColumn(
                    "Diferencia", format="$%.0f"),
                "margen_kit_pct": st.column_config.NumberColumn(
                    "Margen kit", format="percent"),
                "cruza_umbral": None, "productos": None})
        st.download_button("Descargar", cru.to_csv(index=False).encode("utf-8"),
                           f"kits_umbral_{datetime.now():%Y%m%d}.csv",
                           "text/csv", key="dl_cr")
        st.session_state["kits_para_registrar"] = cru[
            cru["veredicto"] == "conviene"]

    # -------------------------------------------------------- dejar registro
    st.divider()
    st.markdown("##### Dejar constancia")
    st.caption(
        "Guarda en la planilla qué kits se propusieron y cuáles se armaron, "
        "con fecha y quién. **No crea nada en MercadoLibre** — eso es del "
        "panel — pero deja el registro que hoy no existe en ningún lado.")

    para_reg = st.session_state.get("kits_para_registrar")
    if para_reg is not None and len(para_reg):
        g1, g2, g3 = st.columns([2, 2, 2])
        op_k = g1.text_input("Tu nombre", key="op_k")
        est_k = g2.selectbox("Estado", ["propuesto", "armado", "descartado"],
                             key="est_k")
        with g3:
            st.write("")
            if st.button(f"Registrar {len(para_reg)}", key="go_k",
                         disabled=not op_k.strip()):
                ok, det = kits_mod.registrar(para_reg, operador=op_k.strip(),
                                             estado=est_k)
                (st.success if ok else st.error)(det)

    hechos = kits_mod.registrados()
    if len(hechos):
        with st.expander(f"Ver el registro ({len(hechos)})"):
            st.dataframe(hechos.tail(200), use_container_width=True,
                         hide_index=True, height=280)

    st.caption(
        "**Publicar el kit no se puede por API**: el botón *armar* abre el "
        "panel de MercadoLibre con el producto principal ya cargado.")
