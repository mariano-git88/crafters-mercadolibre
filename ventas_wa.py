#!/usr/bin/env python3
"""
El cerebro del asistente de WhatsApp: entiende la consulta, arma la respuesta
y, cuando corresponde, cierra con un link de pago.

    python ventas_wa.py "hola, tenes candados de 40mm?"
    python ventas_wa.py --casos     -> corre los casos de prueba

--------------------------------------------------------------------------
Por que no es lo mismo que `preguntas.py`
--------------------------------------------------------------------------

En MercadoLibre la respuesta es publica, de una sola vuelta, y las reglas de
la plataforma prohiben dar contacto o sacar la venta afuera. Aca es al reves:
es una conversacion, se pueden dar precios y links, y el objetivo explicito es
cerrar la venta.

Lo que SI se reusa es lo mas valioso de alla: **el asistente se abstiene**.
Contesta cuando esta seguro y deriva a una persona cuando no. Un precio
inventado por WhatsApp es un compromiso con un cliente, no un comentario en
una publicacion.

--------------------------------------------------------------------------
Las tres reglas que hacen que esto sea seguro
--------------------------------------------------------------------------

1. **El precio y el stock NUNCA los dice el modelo.** Se leen del sistema y se
   le pasan como contexto cerrado. El modelo redacta; los numeros los pone el
   codigo.

2. **El mayorista se detecta por la cantidad, no por lo que diga el cliente.**
   Si pide 50 unidades le corresponde el tramo mayorista aunque no aclare que
   es revendedor, y si dice "soy mayorista" pero pide 2, va precio de lista.

3. **Derivar avisa a alguien.** Un asistente que deriva a un buzon que nadie
   mira deja al cliente esperando, que es peor que no haber contestado.
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

import almacen
from catalogo import cargar_catalogo, sku_del_atributo
from meli import Meli, MeliError

DIR = Path(__file__).resolve().parent

MODELO = "claude-opus-5"
PRODUCTOS_EN_CONTEXTO = 6

HOJA_CONVERSACIONES = "wa_conversaciones"
COLS_CONVERSACIONES = ["fecha", "telefono", "nombre", "mensaje", "respuesta",
                       "respondio", "confianza", "motivo", "skus", "accion",
                       "link"]

TIENDA = "https://tienda.suprabond.com"

# Numero de prueba de Meta. En produccion se cambia por el definitivo.
PHONE_NUMBER_ID = "1296822553514864"
WABA_ID = "1746581663320171"

# A donde va lo que el asistente no puede resolver.
#
# **Derivar sin avisar es peor que no contestar**: el asistente le dice al
# cliente "ya paso tu caso a una persona" y si nadie se entera, esa frase es
# una promesa que el sistema no cumple.
DERIVAR_A = "clientes@crafters.com.ar"


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ catalogo

class Catalogo:
    """
    Busqueda por texto sobre las publicaciones activas.

    BM25 sobre titulo + SKU + marca. Es lo mismo que usa `preguntas.py` para el
    historico y por el mismo motivo: son textos cortos, con vocabulario
    propio ("burlete", "topetina", "criquet"), donde los vectores no aportan y
    la coincidencia de palabra si.
    """

    def __init__(self, pubs):
        self.pubs = [p for p in pubs if p.get("status") == "active"]
        # Una entrada por SKU: las publicaciones espejo repiten el mismo
        # producto y ensucian el ranking.
        vistos, self.items = set(), []
        for p in self.pubs:
            sku = (sku_del_atributo(p) or "").strip().upper()
            if not sku or sku in vistos:
                continue
            vistos.add(sku)
            marca = next((a.get("value_name") for a in (p.get("attributes") or [])
                          if a.get("id") == "BRAND"), "") or ""
            self.items.append({
                "sku": sku, "item_id": p["id"],
                "titulo": p.get("title") or "",
                "marca": marca,
                "precio": float(p.get("price") or 0),
                "stock": int(p.get("available_quantity") or 0),
                "texto": f"{p.get('title')} {sku} {marca}",
            })
        try:
            from rank_bm25 import BM25Okapi
            self.bm25 = BM25Okapi([_norm(i["texto"]).split() for i in self.items])
        except ImportError:
            self.bm25 = None

    def buscar(self, consulta, k=PRODUCTOS_EN_CONTEXTO):
        if not self.items:
            return []
        if not self.bm25:                      # sin BM25, coincidencia simple
            t = _norm(consulta).split()
            puntos = [(sum(1 for w in t if w in _norm(i["texto"])), i)
                      for i in self.items]
        else:
            puntos = list(zip(self.bm25.get_scores(_norm(consulta).split()),
                              self.items))
        vivos = [(p, i) for p, i in puntos if p > 0]
        vivos.sort(key=lambda x: -x[0])
        return [i for _, i in vivos[:k]]


# -------------------------------------------------------------------- precio

def cantidad_pedida(texto):
    """
    Cuantas unidades pide, si lo dice. `None` si no se menciona.

    Se buscan formas naturales ("50 unidades", "x50", "necesito 20"), no un
    numero suelto: en "candado de 40mm" el 40 es la medida, no la cantidad.
    """
    t = _norm(texto)
    patrones = [
        r"(\d+)\s*(?:unidades|unidad|u\b|piezas|pzas)",
        r"\bx\s*(\d+)\b",
        r"(?:necesito|quiero|llevo|compro|serian|precio por)\s+(\d+)\b",
        r"(\d+)\s*(?:cajas|packs|docenas)",
    ]
    for p in patrones:
        m = re.search(p, t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 100_000:
                return n
    return None


class Precios:
    """
    El precio que corresponde a cada cantidad, con los tramos mayoristas que
    ya estan cargados en `mayoristas.py`.

    **La cantidad manda, no lo que diga el cliente.** Que alguien se presente
    como revendedor no cambia el precio si compra dos unidades, y quien pide
    50 tiene el tramo aunque no aclare nada. Es lo unico verificable.

    Las reglas se leen UNA vez: son una llamada a Google Sheets y hacerla por
    consulta seria pagarla en cada mensaje.
    """

    def __init__(self, pubs):
        import mayoristas
        self.m = mayoristas
        try:
            self.regs = mayoristas.reglas()
            self.cats = mayoristas.cargar_categorias()
            self.cods = mayoristas.cargar_codigos_familia()
        except Exception:                              # noqa: BLE001
            self.regs, self.cats, self.cods = [], {}, []
        self.por_sku = {}
        for p in pubs:
            if p.get("status") != "active":
                continue
            s = (sku_del_atributo(p) or "").strip().upper()
            if s and s not in self.por_sku:
                self.por_sku[s] = p

    def para(self, sku, cantidad):
        """
        Devuelve (precio_unitario, etiqueta). La etiqueta dice de donde sale,
        para poder mostrarsela al cliente sin inventar.
        """
        pub = self.por_sku.get(str(sku).strip().upper())
        if not pub:
            return None, ""
        lista = float(pub.get("price") or 0)
        if not cantidad or cantidad < 2 or not self.regs:
            return lista, "precio de lista"
        regla = self.m.regla_para(pub, self.regs, self.cats, self.cods)
        if not regla:
            return lista, "precio de lista"
        # El tramo que aplica es el de mayor cantidad minima que el cliente
        # alcanza; si no llega a ninguno, sigue el de lista.
        mejor, desde = lista, None
        for u, p in self.m.tramos(lista, regla):
            if cantidad >= u and p < mejor:
                mejor, desde = p, u
        if desde is None:
            return lista, "precio de lista"
        return mejor, f"precio por {desde} o más unidades"


# ------------------------------------------------------------------- contexto

def contexto_de(consulta, catalogo, precios=None, cantidad=None):
    """
    Los productos que pueden estar en juego, con precio y stock REALES.

    Si el cliente dijo una cantidad, se calcula ademas el precio que le
    corresponde: asi el modelo lo lee del contexto en vez de estimarlo.
    """
    encontrados = catalogo.buscar(consulta)
    lineas = []
    for i in encontrados:
        linea = (f"- {i['titulo']}\n"
                 f"  SKU {i['sku']} · marca {i['marca'] or 'sin marca'}\n"
                 + f"  precio de lista ${i['precio']:,.0f}".replace(",", ".")
                 + f" · stock {i['stock']} unidades")
        if precios and cantidad and cantidad >= 2:
            pu, etiqueta = precios.para(i["sku"], cantidad)
            if pu and pu < i["precio"]:
                linea += (f"\n  por {cantidad} unidades: "
                          + f"${pu:,.0f} c/u ({etiqueta}), total "
                          .replace(",", ".")
                          + f"${pu * cantidad:,.0f}".replace(",", "."))
                i["precio_cantidad"] = pu
        lineas.append(linea)
    return encontrados, ("\n".join(lineas) if lineas
                         else "(no encontré productos que coincidan)")


ESQUEMA = {
    "type": "object",
    "properties": {
        "responder": {
            "type": "boolean",
            "description": ("true si el contexto alcanza para contestar con "
                            "certeza; false si tiene que verlo una persona"),
        },
        "respuesta": {
            "type": "string",
            "description": "El texto que se le manda al cliente por WhatsApp.",
        },
        "skus": {
            "type": "array", "items": {"type": "string"},
            "description": "Los SKU sobre los que se contesta, si hay alguno.",
        },
        "cantidad": {
            "type": "integer",
            "description": "Unidades que pide el cliente. 0 si no lo dijo.",
        },
        "accion": {
            "type": "string",
            "enum": ["ninguna", "link_de_pago", "link_a_la_tienda", "derivar"],
            "description": ("link_de_pago solo si el cliente YA definio qué "
                            "quiere y cuántas unidades; derivar si hace falta "
                            "una persona."),
        },
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "motivo": {
            "type": "string",
            "description": "Por qué contesta eso, o qué le falta. No se envía.",
        },
    },
    "required": ["responder", "respuesta", "skus", "cantidad", "accion",
                 "confianza", "motivo"],
    "additionalProperties": False,
}

INSTRUCCIONES = """\
Atendés las consultas que llegan por WhatsApp a CRAFTERS / Suprabond \
(Argentina): herramientas, adhesivos, selladores, candados, burletes, zócalos, \
griferia y ferreteria en general.

Tu mensaje se ENVIA AUTOMATICAMENTE al cliente, sin que nadie lo revise. \
Escribí solo lo que puedas sostener con el contexto que te dan.

Cómo escribir:
- Castellano rioplatense, cercano y breve. Es un chat, no un mail: dos o tres \
oraciones por mensaje.
- Andá al grano. Si preguntan un precio, el precio va en la primera línea.
- Una sola pregunta por mensaje cuando necesites que te aclaren algo.

Los números NO los ponés vos:
- Precio y stock salen del contexto que te paso. Si un producto no está ahí, \
no tiene precio ni disponibilidad para vos.
- Nunca inventes medidas, compatibilidades, plazos de entrega ni descuentos.
- Si el cliente pide cantidad y hay precio mayorista, se lo va a calcular el \
sistema: vos indicá que le pasás el precio por cantidad, no lo estimes.

Poné responder=false y accion=derivar cuando:
- Piden un descuento puntual, una condición de pago especial o cuenta corriente.
- Es un reclamo, una demora, un cambio o algo de una compra ya hecha.
- Preguntan por un producto que no está en el contexto.
- Piden factura A con condiciones particulares, o algo de logística fuera de \
lo habitual.
- Cualquier caso donde equivocarte le costaría plata o confianza a la empresa.

Sobre cerrar la venta:
- accion=link_de_pago SOLO cuando ya está claro QUÉ producto y CUÁNTAS \
unidades. Si falta alguno de los dos, preguntalo primero.
- accion=link_a_la_tienda cuando quieren ver más opciones o comprar por su \
cuenta.
- Nunca prometas un plazo de entrega: no tenés ese dato.

Preferí derivar antes que arriesgar. Del otro lado hay una persona esperando \
una respuesta que la empresa va a tener que sostener.\
"""


def responder(conversacion, catalogo, precios=None, cliente=None):
    """
    Le pide a Claude la respuesta. `conversacion` es una lista de
    {"de": "cliente"|"nosotros", "texto": ...}, del mas viejo al mas nuevo.
    """
    import anthropic

    ultimo = next((m["texto"] for m in reversed(conversacion)
                   if m["de"] == "cliente"), "")
    cant = cantidad_pedida(ultimo)
    encontrados, ctx = contexto_de(ultimo, catalogo, precios, cant)

    hilo = "\n".join(
        f"{'Cliente' if m['de'] == 'cliente' else 'Nosotros'}: {m['texto']}"
        for m in conversacion[-12:])

    prompt = (
        f"Conversación hasta ahora:\n{hilo}\n\n"
        f"Productos del catálogo que podrían corresponder:\n{ctx}\n\n"
        + (f"El cliente parece pedir {cant} unidades.\n\n" if cant else "")
        + "Contestá el último mensaje del cliente.")

    import preguntas
    cli = cliente or anthropic.Anthropic(api_key=preguntas._api_key())
    r = cli.messages.create(
        model=MODELO, max_tokens=1200,
        system=INSTRUCCIONES,
        tools=[{"name": "responder", "description": "La respuesta al cliente.",
                "input_schema": ESQUEMA}],
        tool_choice={"type": "tool", "name": "responder"},
        messages=[{"role": "user", "content": prompt}])
    salida = next((b.input for b in r.content if b.type == "tool_use"), None)
    if not salida:
        return {"responder": False, "respuesta": "", "skus": [], "cantidad": 0,
                "accion": "derivar", "confianza": "baja",
                "motivo": "el modelo no devolvió una respuesta válida"}
    salida["_productos"] = encontrados
    return salida


# ------------------------------------------------------------- link de pago

def link_de_pago(ml, items, referencia=""):
    """
    Arma un link de Mercado Pago. `items` es [{titulo, precio, cantidad}].

    **Crear la preferencia no cobra nada**: solo genera el link. El cliente
    paga cuando lo abre, y ahi recien existe la venta.
    """
    import requests

    tok = ml.tokens.get("access_token")
    cuerpo = {
        "items": [{"title": i["titulo"][:250], "quantity": int(i["cantidad"]),
                   "unit_price": float(i["precio"]), "currency_id": "ARS"}
                  for i in items],
        "external_reference": referencia or "whatsapp",
    }
    r = requests.post("https://api.mercadopago.com/checkout/preferences",
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json"},
                      json=cuerpo, timeout=30)
    if r.status_code >= 300:
        raise MeliError(f"no pude crear el link: HTTP {r.status_code} "
                        f"{r.text[:200]}")
    return r.json().get("init_point")


def avisar_derivacion(telefono, nombre, conversacion, salida):
    """
    Le avisa por mail a quien atiende. Devuelve (ok, detalle).

    Va con la conversacion entera: quien la reciba tiene que poder contestar
    sin volver a preguntarle todo al cliente.
    """
    import correo

    hilo = "".join(
        f"<p style='margin:4px 0'><b>{'Cliente' if m['de'] == 'cliente' else 'Nosotros'}:</b> "
        f"{m['texto']}</p>" for m in conversacion[-12:])
    html = (
        f"<h3>Consulta de WhatsApp para atender</h3>"
        f"<p><b>De:</b> {nombre or 'sin nombre'} · {telefono}</p>"
        f"<p><b>Por qué se derivó:</b> {salida.get('motivo', '')}</p>"
        f"<hr>{hilo}"
        f"<p style='color:#666;font-size:12px'>Lo mandó el asistente de "
        f"WhatsApp. Respondele al cliente desde el panel o desde el número.</p>")
    return correo.enviar(
        f"WhatsApp · {nombre or telefono} necesita una persona", html,
        para=[DERIVAR_A])


def registrar(telefono, nombre, mensaje, salida, link=""):
    """Deja la conversacion en la planilla. Nunca corta la atencion."""
    try:
        almacen.append_hoja(HOJA_CONVERSACIONES, COLS_CONVERSACIONES, [{
            "fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "telefono": telefono, "nombre": nombre, "mensaje": mensaje,
            "respuesta": salida.get("respuesta", ""),
            "respondio": salida.get("responder"),
            "confianza": salida.get("confianza"),
            "motivo": salida.get("motivo"),
            "skus": ",".join(salida.get("skus") or []),
            "accion": salida.get("accion"), "link": link}])
    except Exception:                                  # noqa: BLE001
        pass


# ------------------------------------------------------------------- pruebas

CASOS = [
    "hola, tenes candados de 40mm?",
    "cuanto sale el burlete para puerta?",
    "necesito 50 unidades de cinta metrica, que precio me haces?",
    "me haces un descuento si llevo 3?",
    "compre la semana pasada y no me llego nada",
    "tenes tornillos autoperforantes de 6 pulgadas?",
    "quiero comprar 2 candados bulit de bronce de 40",
]


def main():
    ml = Meli(verbose=False)
    pubs = cargar_catalogo(ml)
    cat = Catalogo(pubs)
    pre = Precios(pubs)
    print(f"catálogo: {len(cat.items)} productos · "
          f"{len(pre.regs)} reglas de precio mayorista\n")

    consultas = CASOS if "--casos" in sys.argv else [
        " ".join(a for a in sys.argv[1:] if not a.startswith("--"))]
    for c in consultas:
        if not c.strip():
            continue
        print("─" * 66)
        print(f"CLIENTE: {c}")
        s = responder([{"de": "cliente", "texto": c}], cat, pre)
        print(f"  contesta: {s['responder']} · {s['confianza']} · "
              f"acción: {s['accion']}")
        if s["respuesta"]:
            print(f"  → {s['respuesta']}")
        print(f"  (motivo: {s['motivo']})")
        if s["skus"]:
            print(f"  SKU: {', '.join(s['skus'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
