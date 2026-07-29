"""
tutorial_crafters.py — Contenido del tutorial de las herramientas de
MercadoLibre de CRAFTERS.

Se pinta dentro de un st.dialog (modal) cuando el usuario hace click en
el botón "Tutorial". Está pensado para quien opera la herramienta: explica
qué hace cada sección, cómo armar las planillas y qué significan los avisos.
Sin tecnicismos innecesarios.

Si hay que actualizar el contenido, editar acá sin tocar `crafters_app.py`.
"""

import streamlit as st


def render() -> None:
    """Renderiza el tutorial completo dentro del modal."""

    st.markdown(
        """
### ¿Qué hace esta app?

Tres cosas, cada una en su sección:

| Sección | Para qué sirve |
|---|---|
| **Precios** | Cambiar precios de muchas publicaciones de una, desde una planilla |
| **Stock** | Lo mismo pero con las unidades disponibles |
| **Rentabilidad** | Ver cuánto ganás realmente con cada producto, después de todo lo que se lleva MercadoLibre |

Las dos primeras **modifican la cuenta de verdad**. Por eso nunca aplican
nada sin mostrarte antes, en pantalla, exactamente qué va a pasar.
"""
    )

    st.divider()
    st.markdown(
        """
### La regla más importante

**Lo que no está en la planilla, no se toca.**

Si subís una planilla con 20 productos, se modifican esos 20 y nada más.
El resto del catálogo queda intacto. No hace falta que la planilla tenga
todos los productos.
"""
    )

    st.divider()
    st.markdown(
        """
### Cómo armar la planilla

Un Excel o CSV con **dos columnas**:

| SKU | Precio |
|---|---|
| CR0160000000000PAH4B | 132913 |
| CR01600000LLV7CKIT5R | 93224 |

- La primera columna puede llamarse **SKU**, *Código* o *Artículo*.
- La segunda, **Precio** (o *Valor*, *Importe*) para la sección de precios;
  **Stock** (o *Cantidad*, *Unidades*) para la de stock.
- Si los nombres no coinciden, la app te deja elegir a mano qué columna es
  cuál, así que no es grave.

También podés poner el **código de la publicación** (`MLA123456789`) en vez
del SKU, si querés apuntarle a una publicación puntual.

**Los números se escriben como quieras**: `1234`, `1.234,50` o `$ 1234`.
La app los entiende igual.
"""
    )

    st.divider()
    st.markdown(
        """
### Paso a paso

1. **Subí la planilla.**
2. **Apretá "Simular".** No cambia nada todavía: solo calcula.
3. **Mirá la tabla.** Te muestra publicación por publicación el valor actual,
   el nuevo y por qué la eligió.
4. **Escribí tu nombre** y tildá la confirmación.
5. **Aplicar.**

Podés descargar la simulación en CSV antes de aplicar, para revisarla
tranquila o mandársela a alguien.
"""
    )

    st.divider()
    st.markdown(
        """
### Un SKU puede tener varias publicaciones

Esto es lo que más confunde, así que va con detalle.

En el catálogo de CRAFTERS **el mismo producto suele estar publicado varias
veces** (con títulos distintos, para aparecer más en las búsquedas). Casi la
mitad de las publicaciones son duplicados.

Entonces, cuando ponés un SKU en la planilla, la app tiene que decidir a
cuál de todas aplicarle el cambio:

**Para precios:**

- Si entre las publicaciones de ese SKU **hay algunas con cuotas sin interés
  y otras sin cuotas** → se actualizan **solo las que NO tienen cuotas**
  (las "Clásicas"). Las Premium quedan como están.
- Si son **todas iguales** → se actualizan todas.

> **Por qué:** ofrecer cuotas sin interés te cuesta unos 12 puntos más de
> comisión. No conviene mezclar esos precios con los de las publicaciones
> comunes.

**Para stock:**

- MercadoLibre maneja el stock **compartido** entre las publicaciones del
  mismo producto. Si actualizás una, las demás se mueven solas. La app ya
  cuenta con eso.
- Pero algunos SKU tienen el stock **separado en varios lugares**. Ahí la app
  **no toca nada** y te lo marca como *ambiguo*, porque poner el mismo número
  en cada lugar **duplicaría las unidades** y terminarías vendiendo lo que no
  tenés.
"""
    )

    st.divider()
    st.markdown(
        """
### Qué significa cada aviso de la tabla

| Aviso | Qué pasó | Qué hacer |
|---|---|---|
| **actualizar** | Todo bien, se va a aplicar | Nada |
| **revisar** | El precio cambia más de 50% | Fijate que no sea un cero de más. Si está bien, tildá la casilla para incluirlas |
| **sin_cambio** | El valor de la planilla es igual al que ya tiene | Nada, se saltea |
| **no_encontrado** | Ese SKU no existe entre las publicaciones activas | Revisá que esté bien escrito o que la publicación no esté pausada |
| **ambiguo** | El SKU tiene el stock separado en varios lados | Hay que definir cuál corresponde. Avisá para resolverlo |
| **sin_destino** | Está en Full | El stock de Full lo maneja MercadoLibre según lo que tenga en su depósito |
| **valor_invalido** | El número no se entiende, o es negativo | Corregí la planilla |
| **duplicado_en_planilla** | El SKU aparece dos veces | Se usa el primero. Limpiá la planilla si el valor correcto era el otro |

> Los marcados como **revisar** **no se aplican** salvo que tildes la casilla
> que dice "Incluir también las marcadas para revisar". Es la red de seguridad
> contra un error de tipeo.
"""
    )

    st.divider()
    st.markdown(
        """
### Sección Rentabilidad

Subís una planilla con el **costo** de cada producto:

| SKU | Costo |
|---|---|
| CR0160000000000PAH4B | 48000 |

La app le suma el precio al que se está vendiendo hoy en MercadoLibre y
**los cargos reales que cobró ML en cada venta de ese producto**: comisión,
recargo por cuotas, cargo fijo y envío. No son estimaciones de una tabla:
son los números de tus ventas.

Con eso te muestra cuánto te queda de cada venta, en pesos y en porcentaje.

**Ojo con el IVA.** Si tus costos están **sin IVA** y los precios de
MercadoLibre lo incluyen, elegí *21%* en el selector. Si no, el margen te va
a dar más alto de lo que realmente es.

**Qué mirar primero:** los productos con margen negativo aparecen arriba de
todo. Son los que se están vendiendo a pérdida.
"""
    )

    st.divider()
    st.markdown(
        """
### Si algo falla

**"No hay conexión con MercadoLibre"** → El permiso de la app venció o se
revocó. Hay que volver a autorizarla desde una computadora. Avisá a Mariano.

**El catálogo parece desactualizado** → Apretá **"↻ Actualizar catálogo"**
arriba a la derecha. La app guarda el catálogo un rato para andar más rápido;
ese botón lo vuelve a bajar de MercadoLibre.

**Alguna publicación dio error al aplicar** → Aparece en la tabla de
resultados con el motivo. Las demás **sí se aplicaron**: no se cancela todo
por una que falle.

**Quiero saber qué se cambió y cuándo** → Todo queda registrado con el valor
anterior, el nuevo, quién lo hizo y a qué hora. Está en la planilla de Google
de la herramienta, en la hoja `auditoria`.
"""
    )
