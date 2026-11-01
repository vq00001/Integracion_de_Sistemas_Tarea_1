#!/usr/bin/env python3
"""Experimento 1: efecto del timeout ante una dependencia lenta (Competencia 6).

Variable independiente: SIMULAR_LATENCIA_MS en Cupos (barrido relativo al timeout).
Variable dependiente: latencia y status code de POST /v1/enrollments, y estado del circuito.
Variables controladas: TODA la configuración de la API y de Cupos, fijada de forma EXPLÍCITA en
cada escenario (los contenedores se RECREAN y la configuración efectiva se lee de vuelta y se
verifica; ver _common.levantar) y registrada en cada fila del CSV.

Cambios respecto de la versión anterior (revisión 3):
  - Cada escenario parte de un circuito CLOSED y de la configuración por defecto: el circuit
    breaker abre en el escenario que corresponde (antes quedaba con un umbral de 1000 heredado
    de otro experimento y la transición CLOSED → OPEN no aparecía).
  - El control funcional 404 ya no se mezcla con la medición: se ejecuta en una fase propia
    (`fase = control_404`, con Cupos sin latencia). Las mediciones usan solo cursos reales
    (`fase = medicion`).
  - Se calibra la deriva de reloj contenedor/anfitrión (explica que las latencias medidas
    fueran ~92-94 % del retraso inyectado) y se guarda en data/raw/reloj_<fecha>.csv.

Con timeout T se espera: bajo T → 201 con latencia ≈ delay + base; sobre T → 504 CUPOS_TIMEOUT
(≈ T, resultado incierto) hasta CUPOS_CB_FAILURE_THRESHOLD fallas; luego el circuito OPEN y 503
casi instantáneo (fail-fast).

Uso:
  python experiment/scripts/timeout.py --label timeout2s --reps 3
  python experiment/scripts/timeout.py --label timeout5s --timeout-s 5 --reps 3   # otro timeout
"""
import argparse
import csv
import statistics
from datetime import datetime

from _common import (ROOT, calibrar_reloj, crear_estudiante, entorno, health, levantar, matricular,
                     revertir)

# Fracciones del timeout: con T = 2 s dan 0, 250, 1000, 1500, 1900, 2050, 2500 y 4000 ms.
FRACCIONES = [0, 0.125, 0.5, 0.75, 0.95, 1.025, 1.25, 2.0]
CURSOS = [1, 2, 3, 4, 5]       # solo cursos reales (el 5 tiene 2 cupos: se revierte cada matrícula)
CURSO_INEXISTENTE = 999        # control funcional, en su propia fase
CAMPOS = ["label", "rep", "fase", "delay_ms", "iter", "status", "latency_ms", "error_code",
          "circuit_state", "timeout_s", "cb_threshold", "cb_recovery_s"]


def una_fase(w, f, label, rep, fase, delay, n, cursos, cfg):
    resultados = []
    for i in range(n):
        estudiante = crear_estudiante()
        status, ms, body = matricular(estudiante, cursos[i % len(cursos)])
        error_code = body.get("error", {}).get("code", "") if isinstance(body, dict) else ""
        estado_circuito = health().get("dependencies", {}).get("cupos_grpc", {}).get("circuit_state", "")
        resultados.append((status, ms, estado_circuito))
        w.writerow([label, rep, fase, delay, i, status, f"{ms:.2f}", error_code, estado_circuito,
                    cfg["CUPOS_GRPC_TIMEOUT_SECONDS"], cfg["CUPOS_CB_FAILURE_THRESHOLD"],
                    cfg["CUPOS_CB_RECOVERY_SECONDS"]])
        f.flush()
        if status == 201:
            revertir(body["id"])
    return resultados


def resumen(fase, delay_ms, resultados):
    lat = [ms for _, ms, _ in resultados]
    ok = sum(s == 201 for s, _, _ in resultados)
    p95 = statistics.quantiles(lat, n=20)[18] if len(lat) >= 2 else lat[0]
    cb = " → ".join(dict.fromkeys(c for _, _, c in resultados))
    print(f"{fase:9s} delay={delay_ms:>5}ms | media={statistics.fmean(lat):>7.1f}ms "
          f"p50={statistics.median(lat):>7.1f} p95={p95:>7.1f} | 201={ok:>2}/{len(resultados)} | circuito: {cb}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--n", type=int, default=25, help="solicitudes por escenario (mínimo recomendado: 25)")
    ap.add_argument("--reps", type=int, default=3, help="repeticiones completas del barrido")
    ap.add_argument("--timeout-s", type=float, default=2.0, help="CUPOS_GRPC_TIMEOUT_SECONDS a evaluar")
    a = ap.parse_args()

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    raw = ROOT / "experiment" / "data" / "raw"
    out = raw / f"{a.label}_{stamp}.csv"
    escenarios = [round(a.timeout_s * 1000 * fr) for fr in FRACCIONES]
    api_cfg = {"CUPOS_GRPC_TIMEOUT_SECONDS": str(a.timeout_s)}
    try:
        print("Calibrando la deriva de reloj contenedor/anfitrión ...")
        factor = calibrar_reloj()
        print(f"  reloj_contenedor / reloj_anfitrión = {factor:.4f}"
              f"  (retraso inyectado de 1000 ms ≈ {1000 / factor:.0f} ms medidos en el anfitrión)")
        with open(raw / f"reloj_{stamp}.csv", "w", newline="", encoding="utf-8") as fr:
            wr = csv.writer(fr)
            wr.writerow(["factor_contenedor_sobre_anfitrion", *entorno()])
            wr.writerow([f"{factor:.6f}", *entorno().values()])

        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CAMPOS)
            for rep in range(1, a.reps + 1):
                print(f"--- repetición {rep}/{a.reps} ---")
                cfg = levantar(cupos={"SIMULAR_LATENCIA_MS": "0"}, api=api_cfg)
                resumen("control", 0, una_fase(w, f, a.label, rep, "control_404", 0, a.n,
                                               [CURSO_INEXISTENTE], cfg))
                for delay in escenarios:
                    cfg = levantar(cupos={"SIMULAR_LATENCIA_MS": str(delay)}, api=api_cfg)
                    resumen("medicion", delay, una_fase(w, f, a.label, rep, "medicion", delay, a.n,
                                                        CURSOS, cfg))
    finally:
        levantar()  # configuración normal
    print("datos:", out)


if __name__ == "__main__":
    main()
