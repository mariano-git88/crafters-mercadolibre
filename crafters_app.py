#!/usr/bin/env python3
"""
Herramientas de MercadoLibre para CRAFTERS.

    streamlit run crafters_app.py

Cinco secciones: precios, precios mayoristas por reglas, stock de ML,
control de stock propio y rentabilidad por SKU.

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
import rentabilidad as rent
import mayoristas
import stock_control
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
                "Rentabilidad"],
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
