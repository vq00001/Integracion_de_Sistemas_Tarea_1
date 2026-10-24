#!/usr/bin/env python3
"""Experimento 2: ¿qué pasa con los CUPOS cuando vence el timeout? (Competencia 6)

Motivación. El experimento 1 mide qué ve el cliente (latencia y código HTTP). Pero cuando
la API corta la espera a los 2 s, Cupos NO se entera: sigue su operación y termina
ocupando el cupo. Si la matrícula no se confirma, ese cupo queda retenido sin ninguna
matrícula asociada ("cupo huérfano"). Este experimento lo mide y evalúa la mitigación.

Hipótesis.
  H1. Cada solicitud que vence por timeout deja un cupo huérfano en Cupos (la ocupación
      continúa aunque el llamador ya se fue).
  H2. Sin conciliación los huérfanos persisten indefinidamente; con el conciliador
      (matrículas PENDIENTES caducadas → ocupar+liberar idempotente → ANULADA) el
      número de huérfanos vuelve a 0 en aproximadamente TTL + intervalo del conciliador.

Variables.
  Independiente: conciliador (apagado: RECONCILE_INTERVAL_SECONDS=0 / encendido: 1 s).
  Dependiente:   cupos huérfanos = cupos ocupados en Cupos (caja blanca) − matrículas
                 CONFIRMADAS nuevas; matrículas PENDIENTES; segundos hasta resolver.
  Controladas:   latencia de Cupos (3000 ms > timeout 2000 ms), N solicitudes, curso 4
                 (15 cupos), TTL = 6 s, umbral del circuit breaker alto (para que el
                 circuito no abra y cada solicitud llegue realmente a Cupos).

Uso:
  python experiment/scripts/consistencia.py --label consistencia --reps 3
Genera experiment/data/raw/<label>_<fecha>.csv
"""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from _common import (ROOT, compose, crear_estudiante, cupos_libres, esperar_api, matricular,
                     total_matriculas)

CURSO = 4
LATENCIA_MS = 3000
TTL_S = 6
ESPERA_MAX_S = 30


def configurar(latencia_ms: int, conciliador_s: float) -> None:
    """Reinicia Cupos (con latencia) y la API (con/sin conciliador, circuito difícil de abrir)."""
    compose("up", "-d", "cupos", env={"SIMULAR_LATENCIA_MS": str(latencia_ms)})
    compose("up", "-d", "--no-deps", "matriculas-api", env={
        "PENDING_TTL_SECONDS": str(TTL_S),
        "RECONCILE_INTERVAL_SECONDS": str(conciliador_s),
        "CUPOS_CB_FAILURE_THRESHOLD": "1000",
        "CUPOS_CB_RECOVERY_SECONDS": "1",
    })
    time.sleep(3)
    esperar_api()


def limpiar() -> None:
    """Deja el curso sin matrículas pendientes (corre el conciliador y espera): cada
    corrida parte de un estado limpio y no arrastra huérfanos de la anterior."""
    configurar(0, 1)
    limite = time.monotonic() + 45
    while time.monotonic() < limite and total_matriculas("PENDIENTE", CURSO) > 0:
        time.sleep(1)


def una_corrida(label: str, rep: int, n: int, concurrencia: int, conciliador_s: float, w, f) -> None:
    modo = "con_conciliador" if conciliador_s > 0 else "sin_conciliador"
    limpiar()
    configurar(0, conciliador_s)                       # línea base con Cupos rápido
    libres0 = cupos_libres(CURSO)
    pend0, conf0 = total_matriculas("PENDIENTE", CURSO), total_matriculas("CONFIRMADA", CURSO)

    configurar(LATENCIA_MS, conciliador_s)             # Cupos lento: supera el timeout
    estudiantes = [crear_estudiante() for _ in range(n)]
    with ThreadPoolExecutor(max_workers=concurrencia) as pool:
        resp = list(pool.map(lambda s: matricular(s, CURSO), estudiantes))
    codigos = [r[0] for r in resp]
    timeouts = codigos.count(504)

    time.sleep(LATENCIA_MS / 1000 + 1)                 # Cupos termina sus operaciones lentas
    libres1 = cupos_libres(CURSO)
    conf1 = total_matriculas("CONFIRMADA", CURSO) - conf0
    pend1 = total_matriculas("PENDIENTE", CURSO) - pend0
    huerfanos1 = (libres0 - libres1) - conf1

    t0 = time.monotonic()                              # el reloj parte al restaurar Cupos
    configurar(0, conciliador_s)                       # Cupos vuelve a la normalidad
    while time.monotonic() - t0 < ESPERA_MAX_S:
        if total_matriculas("PENDIENTE", CURSO) - pend0 == 0:
            break
        time.sleep(1)
    t_resolver = time.monotonic() - t0
    libres2 = cupos_libres(CURSO)
    pend2 = total_matriculas("PENDIENTE", CURSO) - pend0
    huerfanos2 = (libres0 - libres2) - (total_matriculas("CONFIRMADA", CURSO) - conf0)

    w.writerow([label, rep, modo, n, concurrencia, timeouts, huerfanos1, pend1, pend2,
                f"{t_resolver:.1f}", huerfanos2])
    f.flush()
    print(f"rep {rep} {modo:16s} N={n} 504={timeouts} | tras timeout: huérfanos={huerfanos1} "
          f"pendientes={pend1} | tras {t_resolver:4.1f}s: huérfanos={huerfanos2} pendientes={pend2}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--n", type=int, default=8, help="solicitudes por corrida")
    ap.add_argument("--concurrencia", type=int, default=4, help="solicitudes simultáneas")
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()

    out = ROOT / "experiment" / "data" / "raw" / f"{a.label}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label", "rep", "modo", "n", "concurrencia", "timeouts_504",
                        "huerfanos_tras_timeout", "pendientes_tras_timeout", "pendientes_final",
                        "segundos_hasta_resolver", "huerfanos_final"])
            for rep in range(1, a.reps + 1):
                for conciliador_s in (0, 1):
                    una_corrida(a.label, rep, a.n, a.concurrencia, conciliador_s, w, f)
    finally:
        configurar(0, 10)   # deja el sistema con su configuración normal; el conciliador limpia el resto
    print("datos:", out)


if __name__ == "__main__":
    main()
