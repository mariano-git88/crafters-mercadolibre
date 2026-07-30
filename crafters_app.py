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
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import actualizador as act
import almacen
import competencia
import conversion
import rentabilidad as rent
import mayoristas
import preguntas as preg
import stock_control
import tramos
import tutorial_crafters
from catalogo import CACHE as CACHE_CATALOGO, bajar_catalogo
from meli import Meli, MeliError

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
with enc_btn:
    if st.button("📖 Tutorial", use_container_width=True):
        _tutorial_dialog()
    if st.button("↻ Actualizar catálogo", use_container_width=True):
        st.session_state["sello_catalogo"] += 1
        st.cache_data.clear()
        st.rerun()

seccion = st.segmented_control(
    "Sección", ["Precios", "Mayoristas", "Stock ML", "Control de stock",
                "Rentabilidad", "Competencia", "Oportunidades",
                "Preguntas"],
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
            if st.button("Aplicar en MercadoLibre", key="go_may",
                         disabled=not (conf_may and op_may.strip())):
                barra = st.progress(0.0, text="Aplicando...")
                res = mayoristas.aplicar(
                    ml, sim, operador=op_may.strip(),
                    callback=lambda i, t, f: barra.progress(
                        i / t, text=f"Aplicando {i} de {t}..."))
                barra.empty()
                ok = (res["resultado"] == "OK").sum()
                if ok == len(res):
                    st.success(f"{ok} publicaciones con precio mayorista cargado.")
                else:
                    st.error(f"{ok} cargadas, {len(res) - ok} con error.")
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

elif seccion == "Competencia":
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
                    i / t_, text=f"Consultando {i} de {t_}..."))
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
                    i / t, text=f"Consultando {i} de {t} ({e})..."))
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
                f"nosotros {pesos(peor['nuestro_precio'])} contra "
                f"{pesos(peor['mejor_precio'])} de *{peor['mejor_vendedor']}* "
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
                    "Diferencia", format="%.1f%%",
                    help="Cuánto estamos por encima del más barato"),
                "posicion": "Posición",
                "competidores": "Vendedores",
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
    op = st.radio("Vista", ["Visitas vs ventas", "Tramos de comisión"],
                  horizontal=True, label_visibility="collapsed")

    if op == "Tramos de comisión":
        st.caption(
            "MercadoLibre cobra un porcentaje **más un cargo fijo por unidad**, "
            "y ese cargo salta en escalones. Hay precios donde **subir unos "
            "pesos deja más plata neta**, porque cruzar el escalón baja o "
            "elimina el cargo fijo.")

        with st.expander("Los escalones de tu cuenta"):
            st.markdown(
                "| Precio | Cargo fijo por unidad |\n|---|---|\n"
                "| menos de $16.000 | $1.250 |\n"
                "| $16.000 a $23.999 | $2.505 |\n"
                "| $24.000 a $32.999 | $3.005 |\n"
                "| **$33.000 o más** | **$0** |\n\n"
                "Medidos contra la API por búsqueda binaria. El salto de "
                "$33.000 es el más fuerte: ahí el cargo fijo desaparece.")

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
                    "sube_precio": st.column_config.NumberColumn(
                        "Sube", format="%.1f%%"),
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
                "Antes de aplicar: subir un precio puede bajar la conversión. "
                "Conviene empezar por las que **más ganan por unidad y menos "
                "suben** — las que ya están cerca del escalón.", icon="💡")

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
                        "Conversión", format="%.2f%%"),
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
        return preg.metricas(incluir_historial=con_historial)

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
              help="Las que respondió una persona desde la pestaña Pendientes")
    m3.metric("Esperando respuesta", met["derivadas_a_persona"],
              help="Siguen abiertas: miralas en la pestaña Pendientes")
    m4.metric("Se resolvieron solas",
              f"{met['tasa_automatica']:.0%}" if met["respondidas_ia"] +
              met["derivadas_a_persona"] else "—",
              help="Del total que procesó la IA, cuántas pudo cerrar sin ayuda")

    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    c1.metric("Estado", "Activa" if activa else "Apagada")
    c2.metric("Confianza mínima", cfg.get("min_confianza", "media").capitalize())
    c3.caption(f"Firma: **{cfg.get('firma','')}**  \nSe cambia en la hoja "
               f"`{preg.HOJA_CONFIG}` de la planilla.")

    if not activa:
        st.warning("La IA está **apagada**. Poné `ia_activa = si` en la hoja "
                   f"`{preg.HOJA_CONFIG}` para que vuelva a responder.", icon="⏸️")

    vista_p = st.radio("Vista", ["Responder", "Pendientes", "Historial completo",
                                 "Registro de la IA", "Fuentes"],
                       horizontal=True, label_visibility="collapsed")

    if vista_p == "Responder":
        st.caption(
            "Redacta con el historial de respuestas de la cuenta, los datos de "
            "la publicación y las fuentes cargadas. **Si el contexto no alcanza, "
            "no responde**: deja la pregunta para que la vea una persona.")

        pend = preg.pendientes(ml)
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
                            "contexto no alcanzaba. Están en la pestaña "
                            "**Pendientes**: ahí las respondés y se publican.",
                            icon="👤")
                    for _, f in res.iterrows():
                        with st.container(border=True):
                            st.markdown(f"**{f['estado']}** · confianza "
                                        f"{f['confianza']} · `{f['question_id']}`")
                            st.markdown(f"**P:** {f['pregunta']}")
                            st.markdown(f"**R:** {f['respuesta'] or '_(no respondió)_'}")
                            st.caption(f"Motivo: {f['motivo']}")

    elif vista_p == "Pendientes":
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
        st.session_state["rent"] = rent.calcular(costos, cargos, pubs, iva=iva,
                                                 precios_venta=precios_venta)

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
