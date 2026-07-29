#!/usr/bin/env python3
"""
Herramientas de MercadoLibre para CRAFTERS.

    streamlit run crafters_app.py

Tres secciones: actualizacion masiva de precios, de stock, y analisis de
rentabilidad por SKU.

Las dos primeras ESCRIBEN en la cuenta real, asi que el flujo siempre es
subir planilla -> simular -> revisar -> confirmar -> aplicar. Nunca se
aplica nada sin pasar por la simulacion.
"""

import json
from datetime import datetime

import pandas as pd
import streamlit as st

import actualizador as act
import almacen
import rentabilidad as rent
from catalogo import CACHE as CACHE_CATALOGO, bajar_catalogo
from meli import Meli, MeliError

st.set_page_config(page_title="MercadoLibre — CRAFTERS",
                   page_icon="🛒", layout="wide",
                   initial_sidebar_state="collapsed")

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
        st.markdown("<h1 style='margin-bottom:0.25rem;'>MercadoLibre — CRAFTERS</h1>",
                    unsafe_allow_html=True)
        st.caption("Herramientas de precios, stock y rentabilidad. "
                   "Acceso restringido.")
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

enc_izq, enc_der = st.columns([3, 1])
with enc_izq:
    st.markdown("### 🛒 MercadoLibre — CRAFTERS")
    st.caption(f"{len(pubs):,} publicaciones · {len(activas):,} activas".replace(",", "."))
with enc_der:
    if st.button("↻ Actualizar catálogo", use_container_width=True):
        st.session_state["sello_catalogo"] += 1
        st.cache_data.clear()
        st.rerun()

seccion = st.segmented_control(
    "Sección", ["Precios", "Stock", "Rentabilidad"],
    default="Precios", label_visibility="collapsed")

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
                "Variación", format="%.1f%%"),
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

if seccion == "Precios":
    bloque_carga("precio")

elif seccion == "Stock":
    bloque_carga("stock")

elif seccion == "Rentabilidad":
    st.markdown("#### Rentabilidad por SKU")
    st.caption(
        "Subí una planilla con el **costo** de cada SKU. La herramienta le suma "
        "el precio de venta actual en MercadoLibre y los cargos reales que cobró "
        "ML en las ventas históricas de ese SKU (comisión, recargo por "
        "financiación, cargo fijo y envío).")

    archivo = st.file_uploader("Planilla de costos (.xlsx o .csv)",
                               type=["xlsx", "xls", "csv"], key="up_rent")

    c1, c2, c3 = st.columns(3)
    with c1:
        dias = st.selectbox("Historia a considerar", [30, 60, 90, 180],
                            index=2, key="dias_rent")
    with c2:
        con_envios = st.checkbox("Incluir costo de envío", value=True,
                                 help="Consulta el costo real de envío de una "
                                      "muestra de ventas por SKU. Tarda más.")
    with c3:
        iva = st.selectbox("IVA a descontar del precio", [0.0, 0.21, 0.105],
                           format_func=lambda x: f"{x:.1%}" if x else "Sin descontar",
                           help="Usalo si tus costos están sin IVA y los precios "
                                "de ML lo incluyen.")

    if archivo and st.button("Calcular rentabilidad"):
        try:
            costos = rent.leer_costos(archivo)
        except Exception as e:
            st.error(f"No pude leer la planilla: {e}")
            st.stop()

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

        cargos = rent.cargos_por_sku(ordenes, envios)
        st.session_state["rent"] = rent.calcular(costos, cargos, pubs, iva=iva)

    df = st.session_state.get("rent")
    if df is not None and len(df):
        con_datos = df[df["margen_pct"].notna()]

        m1, m2, m3 = st.columns(3)
        m1.metric("SKU analizados", len(df))
        m2.metric("Margen promedio",
                  f"{con_datos['margen_pct'].mean():.1%}" if len(con_datos) else "—")
        m3.metric("SKU con margen negativo",
                  int((con_datos["margen_pct"] < 0).sum()) if len(con_datos) else 0)

        negativos = con_datos[con_datos["margen_pct"] < 0]
        if len(negativos):
            st.error(f"**{len(negativos)} SKU se venden a pérdida.** "
                     "Están primeros en la tabla.")

        sin_precio = df[df["precio_ml"].isna()]
        if len(sin_precio):
            st.warning(f"{len(sin_precio)} SKU de la planilla no tienen "
                       "publicación activa en MercadoLibre.")

        st.dataframe(
            df, use_container_width=True, height=420,
            column_config={
                "sku": "SKU",
                "item_id": "Publicación",
                "tipo": "Tipo",
                "precio_ml": st.column_config.NumberColumn("Precio ML", format="%.0f"),
                "costo": st.column_config.NumberColumn("Costo", format="%.0f"),
                "comision_prom": st.column_config.NumberColumn("Comisión", format="%.0f"),
                "envio_prom": st.column_config.NumberColumn("Envío", format="%.0f"),
                "cargos_totales": st.column_config.NumberColumn("Cargos", format="%.0f"),
                "margen": st.column_config.NumberColumn("Margen $", format="%.0f"),
                "margen_pct": st.column_config.NumberColumn("Margen %", format="%.1f%%"),
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
