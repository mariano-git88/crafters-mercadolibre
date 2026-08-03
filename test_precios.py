#!/usr/bin/env python3
"""
Las reglas de precio que no se pueden romper.

    python test_precios.py

Correr esto despues de tocar `lista_precios.py`, `rentabilidad.py`,
`precio_minimo.py`, `ventana.py`, `buybox.py` o `competencia.py`.

La regla numero uno es la del descuento del proveedor, y es facil de romper
sin querer: alcanza con que alguien "unifique" el costo en un helper comun.
Si eso pasa, las pantallas que **bajan precios de verdad** empiezan a aprobar
bajas apoyadas en un 20% de descuento que puede no estar el mes que viene, y
la venta pasa a perdida sin que nadie lo note hasta la liquidacion.
"""

import inspect
import json
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent

fallas = []


def chequear(condicion, titulo, detalle=""):
    print(f"  {'OK ' if condicion else 'MAL'} {titulo}")
    if not condicion:
        fallas.append(titulo)
        if detalle:
            print(f"       {detalle}")
    return condicion


def main():
    import buybox
    import lista_precios as lp
    import precio_minimo as pm
    import rentabilidad as rent
    import ventana

    print("=" * 70)
    print("1. El descuento del proveedor solo se usa en Rentabilidad")
    print("=" * 70)
    for mod in (pm, ventana, buybox, lp):
        usos = [l.strip() for l in inspect.getsource(mod).splitlines()
                if ("DESCUENTO_PROVEEDOR" in l or "costo_efectivo" in l)
                and not l.strip().startswith("#")]
        chequear(not usos, f"{mod.__name__} no aplica el descuento",
                 "; ".join(usos[:2]))

    print("\n" + "=" * 70)
    print("2. Los helpers del descuento aguantan datos faltantes")
    print("=" * 70)
    nan = float("nan")
    casos = [
        ((10000, 8500), 0.15, "descuento normal"),
        ((10000, 10500), None, "el objetivo ya esta arriba"),
        ((None, 8500), None, "sin precio de lista"),
        ((nan, 8500), None, "precio de lista NaN"),
        ((10000, nan), None, "objetivo NaN"),
        ((0, 8500), None, "precio de lista cero"),
    ]
    for (sug, obj), esperado, etiqueta in casos:
        got = lp.descuento_necesario(sug, obj)
        bien = (esperado is None and got is None) or (
            esperado is not None and got is not None
            and abs(got - esperado) < 1e-9)
        chequear(bien, f"descuento_necesario: {etiqueta}", f"dio {got}")

    # El caso que rompio la pantalla: (True, 0.0) hacia decir "con 0% de
    # descuento ganás", que suena a que hay algo que hacer cuando no lo hay.
    chequear(lp.alcanza_con_descuento(10000, 10500) == (True, None),
             "sin descuento necesario devuelve (True, None), no 0.0")
    chequear(lp.alcanza_con_descuento(None, 9000) == (None, None),
             "sin lista devuelve (None, None), no (True, ...)")

    print("\n" + "=" * 70)
    print("3. El cruce de codigos contra el catalogo")
    print("=" * 70)
    cat = DIR / "catalogo.json"
    if not cat.exists():
        print("  (sin catalogo.json, se saltea)")
    else:
        pubs = json.loads(cat.read_text(encoding="utf-8"))
        exacta, base, por_ean = lp.indices_del_catalogo(pubs)
        # La regla del padeado a 20: CR + grupo + ceros + codigo.
        chequear(lp.compactar("SBD TR PR 100 E") == "SBDTRPR100E",
                 "compactar saca espacios")
        chequear(lp.cola_de("CR0160000SBDTRPR100E") == "SBDTRPR100E",
                 "cola_de saca prefijo y ceros")
        # Sin sufijos NO se usa para cruzar de entrada: el pack de 3 y la
        # unidad suelta son productos distintos.
        chequear(lp.cola_de("CR0160000000000PBD50 X 3 UNIDADES") !=
                 lp.cola_de("CR0160000000000PBD50"),
                 "el pack no colapsa contra la unidad suelta")

        guardada, _ = lp.guardada()
        if len(guardada):
            dup = guardada["sku"].duplicated().sum()
            chequear(dup == 0, "un solo precio por SKU en la lista guardada",
                     f"{dup} SKU repetidos")
            mult = (guardada["sugerido"] / guardada["costo"]).median()
            chequear(abs(mult - 2.12) < 0.01,
                     f"el sugerido sigue siendo costo x 2,12 (dio {mult:.3f})")

    print("\n" + "=" * 70)
    print("4. Los costos de estructura")
    print("=" * 70)
    total = sum(rent.OTROS_CONCEPTOS.values())
    chequear(abs(total - 0.15) < 1e-9,
             f"impuestos + logistico + general = 15% (dio {total:.0%})")
    chequear(rent.costo_efectivo(1000, True) == 800.0,
             "costo_efectivo con descuento = 80%")
    chequear(rent.costo_efectivo(1000, False) == 1000.0,
             "costo_efectivo sin descuento = costo pleno")

    print("\n" + "=" * 70)
    if fallas:
        print(f"FALLARON {len(fallas)}:")
        for f in fallas:
            print(f"  - {f}")
        return 1
    print("TODO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
