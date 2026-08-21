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
    print("1b. El descuento solo alcanza a los SKU de la lista")
    print("=" * 70)
    # Un SKU de otro proveedor no tiene el descuento de Suprabond. Aplicarselo
    # le infla el margen 20% contra nada, y encima no se nota: el numero sale
    # lindo. Se prueba con dos SKU inventados, uno dentro y uno fuera.
    import pandas as pd

    costos = pd.DataFrame([{"sku": "EN_LISTA", "costo": 1000.0},
                           {"sku": "FUERA", "costo": 1000.0}])
    cargos = pd.DataFrame(columns=["sku", "unidades_vendidas", "ordenes",
                                   "precio_prom", "comision_prom",
                                   "envio_prom", "cobertura_envio",
                                   "envio_base", "items_sin_comision"])
    # El SKU tiene que ir en el atributo SELLER_SKU: `seller_custom_field`
    # solo no alcanza, `sku_del_atributo()` no lo mira.
    def _pub(item_id, sku, precio):
        return {"id": item_id, "price": precio, "status": "active",
                "title": sku, "seller_custom_field": sku,
                "attributes": [{"id": "SELLER_SKU", "value_name": sku}]}

    pubs_falsos = [_pub("MLA1", "EN_LISTA", 5000.0),
                   _pub("MLA2", "FUERA", 5000.0)]
    salida = rent.calcular(costos, cargos, pubs_falsos, iva=0.21,
                           con_descuento=True,
                           precios_lista={"EN_LISTA": {"sugerido": 2120.0}})
    por_sku = dict(zip(salida["sku"], salida["costo"]))
    chequear(abs(por_sku.get("EN_LISTA", 0) - 800.0) < 0.01,
             "el SKU de la lista recibe el descuento",
             f"dio {por_sku.get('EN_LISTA')}")
    chequear(abs(por_sku.get("FUERA", 0) - 1000.0) < 0.01,
             "el SKU fuera de la lista NO recibe el descuento",
             f"dio {por_sku.get('FUERA')}")

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
    print("3b. El precio de lista es un MINIMO, no un techo")
    print("=" * 70)
    # Estar por encima esta permitido: bajar al piso a algo que hoy esta mas
    # caro seria resignar margen sin que nadie lo pidiera.
    import pandas as pd

    costos2 = pd.DataFrame([{"sku": "CARO", "costo": 1000.0}])
    pubs2 = [_pub("MLA9", "CARO", 9000.0)]
    d = pm.analizar(costos2, cargos, pubs2,
                    precios_lista={"CARO": {"sugerido": 5000.0}})
    if len(d):
        f = d.iloc[0]
        chequear(f["estado_lista"] == "arriba del mínimo",
                 "publicado arriba del minimo se marca como tal",
                 f"dio {f['estado_lista']}")
        chequear(abs(f["precio_objetivo"] - 9000.0) < 0.01,
                 "no propone bajar al minimo lo que ya esta mas caro",
                 f"propuso {f['precio_objetivo']}")
        sel = pm.seleccionar_a_precio_de_lista(d)
        chequear(len(sel) == 0,
                 "lo que esta arriba del minimo no entra en el lote de subas")
    else:
        chequear(False, "el caso 'arriba del minimo' produjo una fila")

    print("\n" + "=" * 70)
    print("4. Los costos de estructura")
    print("=" * 70)
    gen = rent.general_pct()
    chequear(0.15 < gen < 0.35,
             f"el general sale de prorratear ${rent.GENERAL['gasto_mensual']:,.0f} "
             f"sobre la venta (dio {gen:.1%})")
    chequear(rent.COSTO_FLEX["fijo_diario"] == 0,
             "chofer + Kangoo van en cero: se cobran en el general, no acá")
    chequear(rent.costo_logistico_unidad() < 0,
             "sin la flota, Flex deja plata: ML bonifica más de lo que cuesta")
    chequear("logistico" not in rent.OTROS_CONCEPTOS,
             "el logístico NO es porcentual: es monto por unidad")

    # **El logístico no puede depender del precio.** Es lo que cuesta poner el
    # paquete en la puerta: cambia con el volumen de entregas, no con lo que
    # vale el producto. Cuando era un 5% cargaba $131 a un producto de $3.170
    # y $8.264 a uno de $200.000.
    log = rent.costo_logistico_unidad()
    d_barato = rent.otros_conceptos_monto(3170 / 1.21)[0]["logistico"]
    d_caro = rent.otros_conceptos_monto(200000 / 1.21)[0]["logistico"]
    chequear(d_barato == d_caro == log,
             "la entrega cuesta lo mismo en un producto de $3.170 que en uno "
             "de $200.000",
             f"barato {d_barato:.0f} vs caro {d_caro:.0f}")
    # Con la flota en cero el volumen de entregas ya no mueve el costo: eso es
    # lo esperado, y si algún día vuelve a haber fijo el test lo va a marcar.
    chequear(rent.costo_logistico_unidad({"entregas_dia": 30}) == log,
             "sin fijos de flota, el volumen de entregas no cambia el costo")
    chequear(rent.costo_logistico_unidad({"fijo_diario": 165672.0}) > log,
             "y si se vuelve a contar la flota, el costo sube")
    chequear(rent.otros_conceptos_monto(10000, unidades=3)[0]["logistico"]
             == 3 * log, "tres unidades pagan tres entregas")
    chequear(rent.costo_efectivo(1000, True) == 800.0,
             "costo_efectivo con descuento = 80%")
    chequear(rent.costo_efectivo(1000, False) == 1000.0,
             "costo_efectivo sin descuento = costo pleno")

    print("\n" + "=" * 70)
    print("No se publicita lo que pierde plata")
    print("=" * 70)
    # Portado de MercadoLibre UY el 18/08/2026. Antes `tope_acos` metia en el
    # mismo saco "sin margen conocido" y "margen conocido y negativo", y a un
    # producto que pierde plata en cada unidad se le permitia gastar hasta el
    # tope general. Ademas la regla vivia adentro del bloque que exige datos
    # suficientes, asi que uno que perdia pero todavia no habia gastado se
    # quedaba prendido juntando clics pagos.
    import pandas as pd

    import publicidad

    cfg = publicidad.config()
    m = {"PIERDE": -12.5, "GANA": 40.0}

    chequear(publicidad.tope_acos("PIERDE", m, cfg) == (0.0, True),
             "margen negativo -> tope 0, o sea no publicitar")
    chequear(publicidad.tope_acos("GANA", m, cfg)[1] is True,
             "margen positivo -> tope propio del SKU")
    chequear(publicidad.tope_acos("NO_ESTA", m, cfg) == (cfg["acos_max"], False),
             "sin margen conocido -> tope general, que no es lo mismo")

    def _ad(item, sku):
        return {"item_id": item, "sku": sku, "ad_group_id": 1, "titulo": "t",
                "marca": "", "estado_ad": "active", "clicks": 0, "gasto": 0.0,
                "acos": 0.0, "roas": 0.0, "unidades": 0, "impresiones": 0,
                "facturado": 0.0, "campaign_id": 9, "advertiser_id": 1,
                "precio": 100, "catalogo": False, "gana_buybox": False,
                "ctr": 0, "cvr": 0}

    def _pub_sku(item, sku):
        return {"id": item, "status": "active", "available_quantity": 5,
                "attributes": [{"id": "SELLER_SKU", "value_name": sku}]}

    salida = publicidad.analizar(
        pd.DataFrame([_ad("A", "PIERDE"), _ad("B", "GANA")]),
        [_pub_sku("A", "PIERDE"), _pub_sku("B", "GANA")],
        cfg=cfg, estrat={}, margenes=m)
    por_id = dict(zip(salida["item_id"], salida["accion"]))
    chequear(por_id.get("A") == "pausar",
             "se pausa aunque no tenga ni un clic",
             f"quedó en {por_id.get('A')}")
    chequear(por_id.get("B") != "pausar",
             "y al que gana no se lo toca por falta de datos",
             f"quedó en {por_id.get('B')}")

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
