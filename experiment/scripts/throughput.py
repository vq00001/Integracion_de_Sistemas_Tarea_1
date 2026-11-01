#!/usr/bin/env python3
"""Experimento 3: throughput bajo carga (Competencia 6).

Hipótesis: la latencia de POST /v1/enrollments se mantiene estable con concurrencia baja y se
degrada (colas, mayor p95) desde cierto nivel; ese punto es el cuello de botella (candidatos: el
pool de conexiones de Postgres, los 10 hilos del servidor gRPC de Cupos, o SQLite serializando
escrituras en Cupos).

Variable independiente: concurrencia. Dependientes: ciclos por segundo y latencia p50/p95.
Controladas: mismo endpoint, configuración por defecto verificada (contenedores recreados antes de
cada repetición), mismo equipo.

Qué es una "solicitud" (hallazgo 15): cada iteración es un CICLO de 3 llamadas HTTP: crear
estudiante → matricular → revertir (la matrícula se revierte para no agotar cupos). `ciclos_por_s`
cuenta ciclos; `llamadas_http_por_s` = 3 × ciclos. La latencia p50/p95 es SOLO la de
POST /v1/enrollments (la operación que cruza a Cupos por gRPC).

Limitaciones que se registran, no se esconden:
  - El generador de carga corre en el MISMO equipo (y, con `docker compose run`, en el mismo host
    Docker) que la API, Cupos y Postgres: compiten por CPU. Se anota el equipo en el CSV.
  - Se repite `--reps` veces y se reportan media ± desviación; una sola corrida no basta.
  - Se hace un calentamiento descartado por nivel.

Uso:  python experiment/scripts/throughput.py --reps 3 --n 100
"""
import argparse
import csv
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from _common import ROOT, crear_estudiante, entorno, levantar, matricular, revertir

NIVELES = [1, 5, 10, 20, 40]
CURSOS = [1, 2, 3, 4]  # se evita el curso 5 (2 cupos) para no toparse con SIN_CUPOS bajo carga
CAMPOS = ["rep", "concurrencia", "n_ciclos", "ciclos_por_s", "llamadas_http_por_s", "p50_ms", "p95_ms",
          "tasa_exito", "os", "cpus", "python"]


def ciclo(i: int):
    estudiante = crear_estudiante()
    status, ms, body = matricular(estudiante, CURSOS[i % len(CURSOS)])
    if status == 201:
        revertir(body["id"])
    return status, ms


def nivel(concurrencia: int, n: int):
    with ThreadPoolExecutor(max_workers=concurrencia) as ex:           # calentamiento (se descarta)
        list(ex.map(ciclo, range(max(10, concurrencia))))
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrencia) as ex:
        res = list(ex.map(ciclo, range(n)))
    total = time.perf_counter() - t0
    lat = [ms for _, ms in res]
    return {
        "concurrencia": concurrencia, "n_ciclos": n, "ciclos_por_s": n / total,
        "llamadas_http_por_s": 3 * n / total, "p50_ms": statistics.median(lat),
        "p95_ms": statistics.quantiles(lat, n=20)[18], "tasa_exito": sum(s == 201 for s, _ in res) / n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--n", type=int, default=100, help="ciclos por nivel y repetición")
    a = ap.parse_args()
    out = ROOT / "experiment" / "data" / "raw" / f"throughput_{datetime.now():%Y%m%d_%H%M%S}.csv"
    filas = []
    for rep in range(1, a.reps + 1):
        levantar()   # estado y configuración conocidos al inicio de cada repetición
        for c in NIVELES:
            fila = {"rep": rep, **nivel(c, a.n), **entorno()}
            filas.append(fila)
            print(f"rep {rep} c={c:>2}: {fila['ciclos_por_s']:6.1f} ciclos/s  p50={fila['p50_ms']:6.1f}  "
                  f"p95={fila['p95_ms']:6.1f}  éxito={fila['tasa_exito']:.0%}")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)
    print("\nMedia ± desviación entre repeticiones:")
    for c in NIVELES:
        g = [r for r in filas if r["concurrencia"] == c]
        sd = lambda k: statistics.stdev([r[k] for r in g]) if len(g) > 1 else 0.0
        print(f"  c={c:>2}: {statistics.fmean(r['ciclos_por_s'] for r in g):6.1f} ± {sd('ciclos_por_s'):4.1f} ciclos/s | "
              f"p95 {statistics.fmean(r['p95_ms'] for r in g):6.1f} ± {sd('p95_ms'):5.1f} ms")
    print("datos:", out)


if __name__ == "__main__":
    main()
