#!/usr/bin/env python3
"""
Corre una lista de comandos cada N minutos, durante horas, dentro de UNA
corrida de GitHub Actions.

    python bucle_cron.py --cada 15 --minutos 330 -- \
        "python stock_control.py --dias 3" "python preguntas_cron.py"

Por que existe: el cron de GitHub **no es confiable a esta frecuencia**. El
`*/15` de sincronizar_stock.yml deberia disparar 52 veces por dia y desde el
27/08/2026 disparaba entre 1 y 5: GitHub saltea en silencio las corridas
programadas cuando esta cargado, y no avisa. Las preguntas de los compradores
esperaban una mediana de 77 minutos (hasta 6 horas) y una persona contestaba
antes que la IA en casi la mitad.

La salida es no depender de que cada disparo llegue: el que llega se queda
corriendo horas y hace el trabajo cada 15 minutos por su cuenta. Con que
lleguen unos pocos disparos por dia, el dia queda cubierto.

Cada comando corre en su propio proceso: si uno falla (un 429 de ML, una
hoja de Google que no contesta) el resto sigue, y el siguiente turno lo
reintenta. La corrida queda en ROJO al final si algun comando fallo varias
veces seguidas, para que no parezca que anda cuando no.
"""

import argparse
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

AR = timezone(timedelta(hours=-3))

# Un comando que falla una vez es un hipo; tres turnos seguidos (45 minutos)
# ya es algo roto que alguien tiene que mirar.
FALLAS_PARA_ALARMA = 3

# Ningun paso deberia tardar esto: el sync tarda ~40 s y las preguntas menos
# de un minuto. Si se cuelga, se corta para no comerse los turnos siguientes.
TIMEOUT_COMANDO = 10 * 60


def log(msg):
    print(f"[{datetime.now(AR):%d/%m %H:%M:%S}] {msg}", flush=True)


def correr(cmd):
    """Corre un comando y devuelve True si salio bien."""
    try:
        r = subprocess.run(shlex.split(cmd), timeout=TIMEOUT_COMANDO)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"  se colgó más de {TIMEOUT_COMANDO // 60} min y lo corté: {cmd}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cada", type=int, default=15, help="minutos entre turnos")
    ap.add_argument("--minutos", type=int, default=330,
                    help="cuánto dura el bucle en total (0 = un solo turno)")
    ap.add_argument("comandos", nargs="+")
    a = ap.parse_args()

    fin = time.monotonic() + a.minutos * 60
    seguidas = {c: 0 for c in a.comandos}
    alarma = set()
    turno = 0

    while True:
        turno += 1
        inicio = time.monotonic()
        log(f"turno {turno}")
        for c in a.comandos:
            ok = correr(c)
            seguidas[c] = 0 if ok else seguidas[c] + 1
            if not ok:
                log(f"  FALLÓ ({seguidas[c]} seguida/s): {c}")
            if seguidas[c] >= FALLAS_PARA_ALARMA:
                alarma.add(c)

        proximo = inicio + a.cada * 60
        if proximo >= fin:
            break
        time.sleep(max(0.0, proximo - time.monotonic()))

    log(f"fin: {turno} turno/s")
    if alarma:
        for c in sorted(alarma):
            log(f"ERROR: falló {FALLAS_PARA_ALARMA} o más turnos seguidos: {c}")
        return 1
    # Tambien rojo si algo quedo fallando en el ultimo turno: termina la
    # corrida con un comando roto y eso no puede verse verde. (Con un solo
    # turno, como en un repo privado, es el mismo criterio de antes.)
    pendientes = [c for c, n in seguidas.items() if n]
    for c in pendientes:
        log(f"ERROR: el último turno falló: {c}")
    return 1 if pendientes else 0


if __name__ == "__main__":
    sys.exit(main())
