#!/usr/bin/env python3
"""
Redondeo de precios: **sin decimales, nunca**.

Politica de la casa (Mariano, 21/08/2026): en MercadoLibre no se publican
centavos. Medido ese dia: 467 de 2.109 publicaciones activas los tenian.

**La direccion no es siempre la misma**, y por eso esto es una funcion y no un
`round()` suelto en cada modulo:

  - `piso()` sube al entero siguiente. Va donde el numero es un **minimo** que
    no se puede perforar: el precio que empata el neto de la Clasica, el
    precio minimo por margen, el piso de marca. Redondear para abajo deja la
    publicacion un peso del lado equivocado y el problema sin cerrar.
  - `techo()` baja al entero anterior. Va donde el numero es un **maximo**:
    el precio para ganar el Buy Box (un peso de mas y lo perdes), o quedar
    por debajo de un umbral de comision.
  - `cerca()` redondea al mas proximo. Para precios que son una sugerencia y
    no un limite.

Elegir mal la direccion cuesta poco por unidad y mucho en volumen: son 2.109
publicaciones.
"""

import math


def piso(precio):
    """Al entero de arriba. Para minimos que no se pueden perforar."""
    return float(math.ceil(float(precio))) if precio else precio


def techo(precio):
    """Al entero de abajo. Para maximos que no se pueden pasar."""
    return float(math.floor(float(precio))) if precio else precio


def cerca(precio):
    """Al entero mas proximo. Para sugerencias sin limite duro."""
    return float(round(float(precio))) if precio else precio
