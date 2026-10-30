#!/usr/bin/env python3
"""Experimento 2: ¿qué pasa con los CUPOS cuando vence el timeout? (Competencia 6)

Motivación. El experimento 1 mide qué ve el cliente. Pero cuando la API corta la espera a los 2 s,
el resultado en Cupos puede quedar INCIERTO: si Cupos ya confirmó la ocupación y solo la respuesta
llegó tarde, el cupo queda retenido sin matrícula ("cupo huérfano").

Qué cambió respecto de la versión anterior (revisión 3), y por qué los datos viejos ya no sirven:
  - Cupos ahora VERIFICA EL DEADLINE antes de confirmar: si el cliente ya se fue, hace ROLLBACK. Con
    "Cupos lento ANTES de procesar" (la simulación de latencia anterior) ya no se generan huérfanos:
    la mitigación en el origen funciona. Es el modo `deadline_en_origen` de este experimento.
  - Lo que NO se puede evitar es el caso en que la operación se confirma y la respuesta llega tarde
    (SIMULAR_LATENCIA_MODO=despues): ahí el resultado es genuinamente incierto y hace falta el
    conciliador. Son los modos `sin_conciliador` y `con_conciliador`.
  - El conciliador ya no hace ocupar+liberar: consulta (solo lectura) y libera si corresponde.
  - Se usa la configuración REAL (TTL 30 s, conciliador cada 10 s) en vez de TTL 6 s / 1 s; con
    `--rapido` se usa TTL 6 / 1 s y queda anotado en el CSV (no mezclar ambas en un análisis).
  - Se varía N (solicitudes) para ver si los huérfanos escalan con N, y la configuración efectiva
    se verifica y se registra.

Hipótesis.
  H1. Con latencia DESPUÉS de confirmar, cada timeout deja un cupo huérfano.
  H2. Sin conciliador persisten; con conciliador vuelven a 0 en ≈ TTL + intervalo.
  H3. Con Cupos lento ANTES de procesar (deadline verificado en origen) los huérfanos son 0 aunque
      haya N timeouts; solo quedan matrículas PENDIENTES (sin cupo retenido) hasta que el
      conciliador las cierre.

Mediciones. huérfanos = cupos ocupados en Cupos (caja blanca) − matrículas CONFIRMADAS nuevas.

Uso:  python experiment/scripts/consistencia.py --label consistencia --reps 3 [--niveles-n 4,8,12] [--rapido]
"""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from _common import (ROOT, compose, crear_estudiante, cupos_libres, levantar, matricular,
                     revertir, total_matriculas)

CURSO = 4                      # 15 cupos
LATENCIA_MS = 3000             # > timeout de 2000 ms
# (modo, SIMULAR_LATENCIA_MODO, ¿conciliador encendido?)
MODOS = [("sin_conciliador", "despues", False),
         ("con_conciliador", "despues", True),
         ("deadline_en_origen", "antes", False)]
CAMPOS = ["label", "rep", "modo", "latencia_modo", "conciliador_s", "ttl_s", "timeout_s", "cb_threshold",
          "n", "concurrencia", "timeouts_504", "huerfanos_tras_timeout", "pendientes_tras_timeout",
          "pendientes_final", "segundos_hasta_resolver", "huerfanos_final"]


def cupos_totales(curso_id: int) -> int:
    codigo = ("import os, sqlite3; print(sqlite3.connect(os.environ['DB_PATH']).execute("
              f"'SELECT cupos_totales FROM cursos WHERE curso_id={curso_id}').fetchone()[0])")
    return int(compose("exec", "-T", "cupos", "python", "-c", codigo).stdout.strip())


def cfg_api(conciliador_s: float, ttl: int) -> dict:
    # Umbral alto SOLO para que el circuito no abra y cada solicitud llegue de verdad a Cupos; es
    # una variable controlada y queda registrada (cb_threshold) en el CSV.
    return {"PENDING_TTL_SECONDS": str(ttl), "RECONCILE_INTERVAL_SECONDS": str(conciliador_s),
            "CUPOS_CB_FAILURE_THRESHOLD": "1000", "CUPOS_CB_RECOVERY_SECONDS": "1"}


def limpiar(ttl: int, intervalo: int) -> None:
    """Parte de un estado limpio: con el conciliador encendido se cierran las PENDIENTES de corridas
    anteriores y sus cupos retenidos se liberan (Cupos solo libera por clave, así que no hay atajo)."""
    levantar(cupos={"SIMULAR_LATENCIA_MS": "0"}, api=cfg_api(intervalo, ttl))
    limite = time.monotonic() + ttl + 3 * intervalo + 30
    total = cupos_totales(CURSO)
    while time.monotonic() < limite:
        if total_matriculas("PENDIENTE", CURSO) == 0 and cupos_libres(CURSO) == total:
            return
        time.sleep(1)
    pendientes = total_matriculas("PENDIENTE", CURSO)
    libres = cupos_libres(CURSO)
    raise RuntimeError(
        f"No se pudo dejar el curso {CURSO} limpio: "
        f"pendientes={pendientes}, cupos_libres={libres}/{total}. "
        "Revisa y revierte manualmente las matrículas CONFIRMADAS ajenas al experimento."
    )


def una_corrida(label, rep, modo, lat_modo, concilia, n, conc, ttl, intervalo, w, f):
    conciliador_s = intervalo if concilia else 0
    limpiar(ttl, intervalo)
    libres0 = cupos_libres(CURSO)
    pend0, conf0 = total_matriculas("PENDIENTE", CURSO), total_matriculas("CONFIRMADA", CURSO)

    cfg = levantar(cupos={"SIMULAR_LATENCIA_MS": str(LATENCIA_MS), "SIMULAR_LATENCIA_MODO": lat_modo},
                   api=cfg_api(conciliador_s, ttl))
    estudiantes = [crear_estudiante() for _ in range(n)]
    confirmadas = []
    try:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            resp = list(pool.map(lambda s: matricular(s, CURSO), estudiantes))
        confirmadas = [
            r[2]["id"] for r in resp
            if r[0] == 201 and r[2] and r[2].get("estado") == "CONFIRMADA"
        ]
        timeouts = [r[0] for r in resp].count(504)

        time.sleep(LATENCIA_MS / 1000 + 1)                 # Cupos termina sus operaciones lentas
        libres1 = cupos_libres(CURSO)
        conf1 = total_matriculas("CONFIRMADA", CURSO) - conf0
        pend1 = total_matriculas("PENDIENTE", CURSO) - pend0
        huerfanos1 = (libres0 - libres1) - conf1

        t0 = time.monotonic()                              # el reloj parte al restaurar Cupos
        levantar(cupos={"SIMULAR_LATENCIA_MS": "0"}, api=cfg_api(conciliador_s, ttl))
        espera = ttl + 2 * intervalo + 5                   # sin conciliador se espera lo mismo: persiste
        while time.monotonic() - t0 < espera:
            if total_matriculas("PENDIENTE", CURSO) - pend0 == 0:
                break
            time.sleep(1)
        t_resolver = time.monotonic() - t0
        pend2 = total_matriculas("PENDIENTE", CURSO) - pend0
        huerfanos2 = (libres0 - cupos_libres(CURSO)) - (total_matriculas("CONFIRMADA", CURSO) - conf0)

        w.writerow([label, rep, modo, lat_modo, conciliador_s, ttl, cfg["CUPOS_GRPC_TIMEOUT_SECONDS"],
                    cfg["CUPOS_CB_FAILURE_THRESHOLD"], n, conc, timeouts, huerfanos1, pend1, pend2,
                    f"{t_resolver:.1f}", huerfanos2])
        f.flush()
        print(f"rep {rep} {modo:19s} N={n:>2} 504={timeouts:>2} | tras timeout: huérfanos={huerfanos1:>2} "
              f"pendientes={pend1:>2} | tras {t_resolver:5.1f}s: huérfanos={huerfanos2:>2} pendientes={pend2:>2}")
    finally:
        for enrollment_id in confirmadas:
            revertir(enrollment_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--niveles-n", default="8", help="valores de N separados por coma (máx. 12: el curso tiene 15 cupos)")
    ap.add_argument("--concurrencia", type=int, default=4)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--rapido", action="store_true", help="TTL 6 s / conciliador 1 s (queda anotado en el CSV)")
    a = ap.parse_args()
    ttl, intervalo = (6, 1) if a.rapido else (30, 10)
    niveles = [int(x) for x in a.niveles_n.split(",")]

    out = ROOT / "experiment" / "data" / "raw" / f"{a.label}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CAMPOS)
            for rep in range(1, a.reps + 1):
                for n in niveles:
                    for modo, lat_modo, concilia in MODOS:
                        una_corrida(a.label, rep, modo, lat_modo, concilia, n, a.concurrencia,
                                    ttl, intervalo, w, f)
    finally:
        levantar()   # configuración normal; el conciliador limpia lo que quede
    print("datos:", out)


if __name__ == "__main__":
    main()
