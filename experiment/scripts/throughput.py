#!/usr/bin/env python3
"""Experimento: throughput bajo carga (Competencia 6).

Hipótesis: la latencia de POST /v1/enrollments se mantiene estable mientras
la concurrencia es baja, y empieza a degradarse (colas, mayor p95) a partir
de cierto nivel de concurrencia -- ese punto es el cuello de botella del
sistema (candidatos: el pool de conexiones de Postgres, el ThreadPoolExecutor
de gRPC en Cupos con MAX_WORKERS=10 por defecto, o SQLite serializando
escrituras).

Variable independiente: nivel de concurrencia (usuarios simultáneos).
Variable dependiente: throughput logrado (peticiones/segundo) y latencia
(p50/p95).
Variables controladas: mismo endpoint, mismo hardware, sin otros procesos
compitiendo.

Cada petición matricula y de inmediato revierte (para no agotar cupos ni
dejar el sistema sucio); se reparte entre varios cursos para no toparse con
el límite de 2 cupos de "Ciberseguridad Básica".

Uso:
  python experiment/scripts/throughput.py
"""
import csv
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from _common import ROOT, crear_estudiante, matricular, revertir

NIVELES_CONCURRENCIA = [1, 5, 10, 20, 40]
PETICIONES_POR_NIVEL = 60
CURSOS = [1, 2, 3, 4]  # se evita el curso 5 (solo 2 cupos) para no toparse con SIN_CUPOS bajo carga


def una_peticion(i: int):
    estudiante = crear_estudiante()
    curso = CURSOS[i % len(CURSOS)]
    status, ms, body = matricular(estudiante, curso)
    if status == 201:
        revertir(body["id"])
    return status, ms


def correr_nivel(concurrencia: int):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrencia) as ex:
        resultados = list(ex.map(una_peticion, range(PETICIONES_POR_NIVEL)))
    total_s = time.perf_counter() - t0
    latencias = [ms for _, ms in resultados]
    exitos = sum(1 for status, _ in resultados if status == 201)
    return {
        "concurrencia": concurrencia,
        "rps": PETICIONES_POR_NIVEL / total_s,
        "p50_ms": statistics.median(latencias),
        "p95_ms": statistics.quantiles(latencias, n=20)[18],
        "tasa_exito": exitos / PETICIONES_POR_NIVEL,
    }


def main():
    out = ROOT / "experiment" / "data" / "raw" / f"throughput_{datetime.now():%Y%m%d_%H%M%S}.csv"
    filas = [correr_nivel(c) for c in NIVELES_CONCURRENCIA]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["concurrencia", "rps", "p50_ms", "p95_ms", "tasa_exito"])
        w.writeheader()
        w.writerows(filas)

    print(f"{'concurrencia':>12}{'rps':>10}{'p50_ms':>10}{'p95_ms':>10}{'tasa_exito':>12}")
    for r in filas:
        print(f"{r['concurrencia']:>12}{r['rps']:>10.1f}{r['p50_ms']:>10.1f}{r['p95_ms']:>10.1f}{r['tasa_exito']:>12.0%}")
    print("\ndatos:", out)
    print("Busquen el nivel donde rps deja de subir (o p95 se dispara): ese es el cuello de botella.")


if __name__ == "__main__":
    main()
