#!/usr/bin/env python3
"""Experimento: efecto del timeout ante una dependencia lenta (Competencia 6).

Variable independiente: SIMULAR_LATENCIA_MS en Cupos.
Variable dependiente: latencia y status code de POST /v1/enrollments.
Variables controladas: CUPOS_GRPC_TIMEOUT_SECONDS de matriculas-api (fijo
durante toda una corrida), mismo curso, mismo hardware.

Uso (una corrida por valor de CUPOS_GRPC_TIMEOUT_SECONDS que se quiera
comparar -- por defecto usa el que ya tenga matriculas-api corriendo):
  python experiment/scripts/timeout.py --label timeout2s
  # cambiar CUPOS_GRPC_TIMEOUT_SECONDS en .env, `docker compose up -d --build matriculas-api`
  python experiment/scripts/timeout.py --label timeout5s
"""
import argparse
import csv
import os
import subprocess
import statistics
import time
from datetime import datetime

from _common import ROOT, compose, crear_estudiante, health, matricular, revertir

ESCENARIOS_MS = [0, 250, 1000, 1500, 1900, 2050, 2500, 4000]
CURSOS = [1, 2, 3, 5, 10, 20]
ESPERA_API_S = 30


def fijar_latencia(delay_ms: int):
    compose("rm", "-fs", "cupos")
    env = {**os.environ, "SIMULAR_LATENCIA_MS": str(delay_ms)}
    subprocess.run(["docker", "compose", "up", "-d", "cupos"], cwd=ROOT, check=True,
                   capture_output=True, env=env)
    compose("restart", "matriculas-api")
    limite = time.monotonic() + ESPERA_API_S
    while time.monotonic() < limite:
        estado = health()
        if estado.get("dependencies", {}).get("cupos_grpc", {}).get("circuit_state") == "CLOSED":
            return
        time.sleep(0.5)
    raise RuntimeError("matriculas-api no quedó lista con el circuito cerrado")


def resumen(delay_ms: int, resultados: list[tuple[int, float, str]]):
    latencias = [ms for _, ms, _ in resultados]
    exitos = sum(status == 201 for status, _, _ in resultados)
    p95 = statistics.quantiles(latencias, n=20)[18]
    estados = ", ".join(
        f"{estado or 'sin_estado'}={sum(item[2] == estado for item in resultados)}"
        for estado in sorted({item[2] for item in resultados})
    )
    print(
        f"delay={delay_ms:>4}ms | promedio={statistics.fmean(latencias):>7.1f}ms "
        f"p50={statistics.median(latencias):>7.1f}ms p95={p95:>7.1f}ms "
        f"éxito={exitos:>2}/{len(resultados)} | circuitos: {estados}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--n", type=int, default=25,
                    help="peticiones por escenario; se recomienda al menos 25")
    a = ap.parse_args()

    out = ROOT / "experiment" / "data" / "raw" / f"{a.label}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label", "delay_ms", "iter", "status", "latency_ms", "error_code", "circuit_state"])
            for delay in ESCENARIOS_MS:
                fijar_latencia(delay)
                resultados = []
                for i in range(a.n):
                    estudiante = crear_estudiante()
                    curso = CURSOS[i % len(CURSOS)]
                    status, ms, body = matricular(estudiante, curso)
                    error_code = body.get("error", {}).get("code", "") if isinstance(body, dict) else ""
                    circuit_state = health().get("dependencies", {}).get("cupos_grpc", {}).get("circuit_state", "")
                    resultados.append((status, ms, circuit_state))
                    w.writerow([a.label, delay, i, status, f"{ms:.2f}", error_code, circuit_state])
                    f.flush()
                    if status == 201:
                        revertir(body["id"])
                resumen(delay, resultados)
    finally:
        fijar_latencia(0)
    print("datos:", out)


if __name__ == "__main__":
    main()
