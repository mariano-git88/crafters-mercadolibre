"""
tutorial_facturacion.py — Contenido del tutorial del control de facturación.

Se pinta dentro de un st.dialog (modal) cuando el usuario hace click en el
botón "Tutorial". Está pensado para quien hace el control todos los meses, no
para quien programa: explica qué mira cada control, por qué las retenciones
tardan y qué hacer con cada hallazgo.

Si hay que actualizar el contenido, editar acá sin tocar `facturacion_app.py`.
"""

import streamlit as st


def render() -> None:
    """Renderiza el tutorial completo dentro del modal."""

    st.markdown(
        """
### ¿Qué hace esta app?

Reemplaza el control que se hacía a mano todos los meses: revisar si
MercadoLibre **percibió o retuvo donde no correspondía**.

Elegís la cuenta, elegís el mes, apretás **Controlar el mes** y te dice qué
está mal y por cuánta plata.

---

### Percepción y retención no son lo mismo

Es la distinción que ordena todo lo demás:

| | Qué es | De dónde sale |
|---|---|---|
| **Percepción** | Un cargo que viene **en la factura** de MercadoLibre | Sale al instante |
| **Retención** | Plata que **te descuentan del pago** antes de acreditarlo | Hay que pedir un reporte y esperar |

Por eso, cuando tildás **Incluir retenciones**, el control tarda uno o dos
minutos: Mercado Pago tiene que generar el reporte. No está colgado.

Si querés ver algo rápido, destildá esa opción: las percepciones salen solas.

---

### Los cuatro controles

**🔴 Cobrado teniendo certificado vigente** — el importante. Compara cada
movimiento contra los certificados cargados y marca lo que no correspondía
cobrar. **Esto es plata para reclamar.**

**🟡 Alícuota fuera de lo habitual** — si en una provincia casi todos los
movimientos van a una alícuota y unos pocos van a otra, los muestra. No
siempre está mal (las alícuotas cambian por padrón), pero vale mirarlos.

**🟡 Dos regímenes sobre el mismo movimiento** — cuando una misma jurisdicción
retiene dos veces sobre la misma base, por dos regímenes distintos. Puede ser
legítimo; es una pregunta para el contador.

**🟠 Certificado por vencer** — avisa 45 días antes. Es el que evita el
problema en lugar de encontrarlo después: si un certificado vence y nadie lo
renueva, vuelven a cobrar y nadie se entera hasta el mes siguiente.

---

### La pestaña Certificados

Es el corazón del control: **si un certificado no está cargado, o tiene mal
las fechas, el control no puede encontrar nada**.

De cada uno hace falta:

- **La jurisdicción** (la provincia).
- **Si cubre percepción, retención o las dos.** No siempre cubre ambas —
  Catamarca, por ejemplo, aplica solo a percepciones.
- **Desde y hasta**, en formato `AAAA-MM-DD`.

⚠️ **Completá siempre la fecha de inicio.** Un certificado sin fecha de inicio
se toma como vigente desde siempre, y ahí el control marca como reclamable
plata que en realidad se cobró bien, antes de que existiera el certificado.

---

### Cómo leer un hallazgo

Cada uno muestra el importe y, abajo, en qué se basa. Ejemplo real:

> **Percepción con certificado vigente — Corrientes** · \\$55.771
> *cobrado entre 2026-04-29 y 2026-04-29, con certificado vigente
> (2026-04-11 a 2026-10-12): no correspondía*

Las fechas son las que importan: si el certificado empezó **a mitad del mes**,
el hallazgo lo aclara, porque parte de las operaciones gravadas son anteriores
y el monto se puede discutir.

---

### Un dato que evita malentendidos

**Una retención no es plata perdida.** Es un crédito contra Ingresos Brutos:
la usás cuando liquidás. El problema aparece cuando te retienen más de lo que
tenés para descontar y se te arma un saldo a favor que no podés usar. Ahí es
cuando conviene pedir un certificado de no retención.

Lo que esta app busca es otra cosa: lo que **no correspondía cobrar**. Eso sí
se reclama.

---

### Preguntas frecuentes

**¿Por qué un mes me da todo en cero?**
Puede ser una buena noticia: si presentaste los certificados, MercadoLibre
deja de percibir en esas provincias. También puede ser que esa cuenta no tenga
certificados cargados — el cero no prueba que esté todo bien, prueba que no
hay nada que cruzar.

**¿Por qué no aparece el mes en curso?**
Porque el período todavía no cerró y los números no están consolidados.

**¿Puedo bajar el detalle?**
Sí. En **Ver el detalle** están las percepciones y las retenciones una por
una, con botón para bajar el CSV y pasárselo al contador.
        """)
