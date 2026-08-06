#!/usr/bin/env python3
"""
Control mensual de facturacion — percepciones y retenciones.

    streamlit run facturacion_app.py

App aparte de la de CRAFTERS, con selector de cuenta: el control es el mismo
para las dos, lo que cambia son los certificados vigentes.

Comparte `meli.py` y `almacen.py` a proposito: los tokens viven en la misma
hoja indexados por cuenta, asi que no hay que autorizar dos veces ni mantener
dos copias del cliente.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import almacen
import facturacion as F
from meli import Meli, MeliError, es_error_de_api

_ASSETS = Path(__file__).resolve().parent / "_assets"
LOGO = _ASSETS / "logo_suprabond.png"         # horizontal, para el encabezado
ICONO = _ASSETS / "icono_suprabond.png"       # cuadrado, para la pestaña

st.set_page_config(page_title="Control de facturación — Suprabond",
                   page_icon=str(ICONO) if ICONO.exists() else "🧾",
                   layout="wide", initial_sidebar_state="collapsed")


def autenticado():
    # Sin secrets.toml (uso local) st.secrets revienta, no devuelve vacio.
    try:
        clave = st.secrets.get("crafters_password")
    except Exception:
        clave = None
    if not clave or st.session_state.get("auth_crafters"):
        return True
    izq, centro, der = st.columns([1, 2, 1])
    with centro:
        if LOGO.exists():
            st.image(str(LOGO), width=260)
        st.markdown("### Control de facturación")
        st.caption("Percepciones y retenciones. Acceso restringido.")
        with st.form("login"):
            ingresada = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar", width="stretch"):
                if ingresada == clave:
                    st.session_state["auth_crafters"] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
    return False


if not autenticado():
    st.stop()


def pesos(v):
    return f"${v:,.0f}".replace(",", ".")


def pesos_md(v):
    """En markdown el $ abre LaTeX y se come el texto hasta el siguiente $."""
    return pesos(v).replace("$", r"\$")


# ==================================================================== cabecera

cuentas = almacen.cuentas_con_token() or [almacen.CUENTA_POR_DEFECTO]
ETIQUETAS = {"crafters": "CRAFTERS", "erpa": "ERPA SACIF"}

izq, der = st.columns([3, 2])
with izq:
    if LOGO.exists():
        st.image(str(LOGO), width=200)
    st.markdown("## Control de facturación")
    st.caption("Percepciones y retenciones de MercadoLibre, mes a mes.")
with der:
    cuenta = st.segmented_control(
        "Cuenta", cuentas, default=cuentas[0], key="cuenta",
        format_func=lambda c: ETIQUETAS.get(c, c.upper()),
        label_visibility="collapsed")
cuenta = cuenta or cuentas[0]


@st.cache_resource(show_spinner=False)
def conectar(cual):
    return Meli(verbose=False, cuenta=cual)


try:
    ml = conectar(cuenta)
except Exception as e:
    st.error(f"No hay conexión con MercadoLibre ({cuenta}): {e}")
    st.info(f"Corré `python autorizar.py --cuenta {cuenta}` en la carpeta "
            f"del proyecto.")
    st.stop()

if not almacen.hay_sheet():
    st.warning(
        "**Sin Google Sheet configurada.** Los certificados y el token se "
        "guardan en archivos locales; en Streamlit Cloud se borran en cada "
        "reinicio.", icon="⚠️")

# Ojo con el `or`: al hacer click en la opcion ya elegida, segmented_control
# la deselecciona y devuelve None. Sin respaldo la pagina queda en blanco.
seccion = st.segmented_control(
    "Sección", ["Control del mes", "Certificados"],
    default="Control del mes", label_visibility="collapsed",
    key="seccion_activa") or "Control del mes"
st.divider()


# ============================================================ control del mes

if seccion == "Control del mes":

    @st.cache_data(ttl=1800, show_spinner=False)
    def _meses(_ml, cual):
        return F.meses_disponibles(_ml, 12)

    try:
        meses = _meses(ml, cuenta)
    except Exception as e:
        st.error(f"No se pudieron leer los períodos: {e}")
        st.stop()

    if not meses:
        st.info("No hay períodos de facturación cerrados todavía.")
        st.stop()

    c1, c2, c3 = st.columns([3, 2, 2])
    with c1:
        etiquetas = [m["etiqueta"] for m in meses]
        mes = meses[etiquetas.index(st.selectbox("Mes", etiquetas, index=0))]
    with c2:
        con_ret = st.checkbox(
            "Incluir retenciones", value=True,
            help="Las retenciones salen de un reporte de Mercado Pago que "
                 "hay que pedir y esperar: suma alrededor de un minuto.")
    with c3:
        st.write("")
        correr = st.button("Controlar el mes", type="primary",
                           width="stretch")

    clave_estado = f"ctrl::{cuenta}::{mes['anio']}-{mes['mes']}::{con_ret}"

    if correr:
        # Sin session_state el resultado se pierde en el primer rerun (y este
        # tarda un minuto: rehacerlo por cada click seria insoportable).
        try:
            with st.status("Controlando...", expanded=True) as caja:
                def paso(m):
                    caja.write(m)

                hall, per, ret = F.controlar(
                    ml, cuenta, mes["anio"], mes["mes"],
                    con_retenciones=con_ret, callback=paso)
                caja.update(label="Control terminado", state="complete",
                            expanded=False)
            st.session_state[clave_estado] = (hall, per, ret)
        except Exception as e:
            if es_error_de_api(e):
                st.error(f"MercadoLibre no respondió: {e}")
            else:
                st.error(f"Falló el control: {e}")

    if clave_estado not in st.session_state:
        st.info("Elegí el mes y apretá **Controlar el mes**.")
        st.stop()

    hall, per, ret = st.session_state[clave_estado]

    tot_per = float(per["monto"].sum()) if len(per) else 0.0
    tot_ret = float(ret["monto"].sum()) if len(ret) else 0.0
    reclam = float(hall[hall["gravedad"] == "reclamable"]["monto"].sum()) \
        if len(hall) else 0.0

    a, b = st.columns(2)
    a.metric("Percepciones del mes", pesos(tot_per))
    b.metric("Retenciones del mes",
             pesos(tot_ret) if con_ret else "no consultadas")
    st.metric("Cobrado teniendo certificado", pesos(reclam),
              help="Lo que no correspondía cobrar según los certificados "
                   "vigentes a la fecha de cada movimiento.")

    st.divider()

    if not len(hall):
        st.success("Sin observaciones: no hay nada cobrado de más en este "
                   "período.")
    else:
        COLOR = {"reclamable": "🔴", "vencido": "🟠", "revisar": "🟡"}
        for _, f in hall.iterrows():
            with st.container(border=True):
                izq, der = st.columns([5, 2])
                izq.markdown(
                    f"{COLOR.get(f['gravedad'], '⚪')} **{f['control']}** — "
                    f"{f['jurisdiccion']}")
                izq.caption(f["detalle"])
                if f["monto"]:
                    der.markdown(f"### {pesos_md(f['monto'])}")
                    der.caption(f"{int(f['casos'])} movimiento(s)")

    with st.expander("Ver el detalle"):
        v = st.segmented_control("Detalle", ["Percepciones", "Retenciones"],
                                 default="Percepciones",
                                 label_visibility="collapsed") or "Percepciones"
        df = per if v == "Percepciones" else ret
        if not len(df):
            st.caption("Sin datos para este período.")
        else:
            st.dataframe(
                df.sort_values("monto", ascending=False),
                width="stretch", hide_index=True,
                column_config={
                    "monto": st.column_config.NumberColumn(
                        "monto", format="$%.2f"),
                    "base": st.column_config.NumberColumn(
                        "base", format="$%.2f"),
                    "cobrado": st.column_config.NumberColumn(
                        "cobrado", format="$%.2f"),
                    # `alicuota` ya viene en porcentaje: con format="percent"
                    # Streamlit la multiplica por 100 y muestra 40% en vez
                    # de 0,4%.
                    "alicuota": st.column_config.NumberColumn(
                        "alícuota", format="%.3f %%"),
                })
            st.download_button(
                "Bajar en CSV", df.to_csv(index=False).encode("utf-8"),
                file_name=f"{v.lower()}_{cuenta}_{mes['anio']}-"
                          f"{mes['mes']:02d}.csv",
                mime="text/csv")


# ============================================================== certificados

if seccion == "Certificados":
    st.markdown("#### Certificados de no percepción / no retención")
    st.caption("El control cruza cada movimiento contra estas fechas. Un "
               "certificado sin fecha de inicio se toma como vigente desde "
               "siempre, así que conviene completarla.")

    certs = pd.DataFrame(almacen.leer_hoja(F.HOJA_CERT, F.COLS_CERT))
    if not len(certs):
        certs = pd.DataFrame(F.SEMILLA)
        st.info("Todavía no hay certificados guardados: abajo están los de "
                "ERPA como punto de partida. Revisalos y guardá.")
    for c in F.COLS_CERT:
        if c not in certs:
            certs[c] = ""

    editado = st.data_editor(
        certs[F.COLS_CERT], num_rows="dynamic", width="stretch",
        hide_index=True,
        column_config={
            "cuenta": st.column_config.SelectboxColumn(
                "cuenta", options=cuentas, required=True),
            "jurisdiccion": st.column_config.TextColumn(
                "jurisdicción", required=True),
            "percepcion": st.column_config.SelectboxColumn(
                "¿cubre percepción?", options=["si", "no"], required=True),
            "retencion": st.column_config.SelectboxColumn(
                "¿cubre retención?", options=["si", "no"], required=True),
            "desde": st.column_config.TextColumn("desde (AAAA-MM-DD)"),
            "hasta": st.column_config.TextColumn("hasta (AAAA-MM-DD)"),
            "numero": st.column_config.TextColumn("número"),
        })

    if st.button("Guardar certificados", type="primary"):
        filas = editado.fillna("").astype(str).to_dict("records")
        malas = [f["jurisdiccion"] for f in filas
                 if f.get("hasta") and not F._fecha(f["hasta"])]
        if malas:
            st.error(f"Revisá las fechas de: {', '.join(malas)}. "
                     f"Van como AAAA-MM-DD.")
        else:
            try:
                F.guardar_certificados(filas)
                st.cache_data.clear()
                st.success("Certificados guardados.")
            except Exception as e:
                st.error(f"No se pudieron guardar: {e}")

    porvencer = F.vencimientos(F.certificados(cuenta))
    if porvencer:
        st.divider()
        for v in porvencer:
            st.warning(f"**{v['jurisdiccion']}** — {v['detalle']}. "
                       f"Si vence y nadie renueva, vuelven a cobrar.",
                       icon="⚠️")
