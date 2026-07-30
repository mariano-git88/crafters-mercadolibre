#!/usr/bin/env python3
"""
Corrida diaria del monitor de competencia (la usa GitHub Actions).

Va en un archivo aparte y no inline en el YAML: un script de Python con
comillas y dos puntos adentro de un `run:` rompe el parseo del workflow.
"""
import sys

from meli import Meli
import competencia as comp


def main():
    r = comp.monitorear(Meli(verbose=False))
    if r.get("error"):
        print(r["error"])
        return 0
    print(f"vigilados: {r['vigilados']} | alertas nuevas: {len(r['alertas'])}")
    for a in r["alertas"]:
        print(f"  [{a['tipo']}] {a['producto'][:50]} — {a['detalle']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
