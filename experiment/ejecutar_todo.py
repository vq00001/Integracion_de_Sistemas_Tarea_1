#!/usr/bin/env python3
"""Ejecuta TODOS los experimentos y genera las figuras y tablas, sin intervención.

Orden:
  1. Experimento 1 (timeout y circuit breaker):  experiment/scripts/timeout.py
  2. Experimento 2 (consistencia de cupos):      experiment/scripts/consistencia.py
  3. Gráficos y tablas resumen:                  experiment/graficar_experimentos.py
  4. Deja el sistema en su configuración normal (Cupos sin latencia, API con valores por defecto).

Resultados:
  experiment/data/raw/timeout2s_<fecha>.csv        datos crudos del experimento 1
  experiment/data/raw/consistencia_<fecha>.csv     datos crudos del experimento 2
  experiment/results/*.png                         figuras para el informe
  experiment/results/resumen_<fecha>.txt           tablas resumen (para el Cuadro 1 del informe)

Duración aproximada con los valores por defecto (3 repeticiones): 20 a 30 minutos.

Uso:
  python experiment/ejecutar_todo.py                    # corrida completa
  python experiment/ejecutar_todo.py --rapido           # prueba de humo (1 repetición, pocas solicitudes)
  python experiment/ejecutar_todo.py --reps 5           # más repeticiones
  python experiment/ejecutar_todo.py --solo-graficar    # regenera figuras con los últimos CSV

En contenedor:
  docker compose run --rm --build experimentos [opciones]
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiment" / "scripts"))
from _common import compose, esperar_api  # noqa: E402

RAW = ROOT / "experiment" / "data" / "raw"
RESULTS = ROOT / "experiment" / "results"


def correr(cmd: list[str]) -> list[str]:
    """Ejecuta un comando mostrando su salida en vivo. Devuelve las líneas de salida."""
    print("\n$", " ".join(cmd), flush=True)
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    lineas = []
    for linea in p.stdout:
        print(linea, end="", flush=True)
        lineas.append(linea.rstrip("\n"))
    if p.wait() != 0:
        sys.exit(f"Falló: {' '.join(cmd)}")
    return lineas


def csv_generado(salida: list[str], prefijo: str) -> Path:
    """Ruta del CSV que imprimió el script ('datos: <ruta>'); si no, el más reciente."""
    for linea in reversed(salida):
        if linea.startswith("datos:"):
            return Path(linea.split(":", 1)[1].strip())
    return ultimo_csv(prefijo)


def ultimo_csv(prefijo: str) -> Path:
    candidatos = sorted(RAW.glob(f"{prefijo}_*.csv"), key=lambda p: p.stat().st_mtime)
    if not candidatos:
        sys.exit(f"No hay CSV '{prefijo}_*.csv' en {RAW}. Ejecuta primero el experimento.")
    return candidatos[-1]


def restaurar_sistema() -> None:
    """Cupos sin latencia y API con su configuración por defecto."""
    print("\nRestaurando la configuración normal del sistema ...", flush=True)
    compose("up", "-d", "cupos")
    compose("up", "-d", "--no-deps", "matriculas-api")
    esperar_api()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3, help="repeticiones de cada experimento (por defecto 3)")
    ap.add_argument("--n", type=int, default=25, help="solicitudes por escenario en el experimento 1")
    ap.add_argument("--n-consistencia", type=int, default=8, help="solicitudes por corrida en el experimento 2")
    ap.add_argument("--rapido", action="store_true", help="prueba de humo: 1 repetición y pocas solicitudes")
    ap.add_argument("--omitir-timeout", action="store_true")
    ap.add_argument("--omitir-consistencia", action="store_true")
    ap.add_argument("--solo-graficar", action="store_true", help="no mide: grafica los últimos CSV")
    a = ap.parse_args()
    if a.rapido:
        a.reps, a.n, a.n_consistencia = 1, 10, 4

    RAW.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    if not a.solo_graficar:
        esperar_api()
        try:
            if not a.omitir_timeout:
                salida = correr([py, "experiment/scripts/timeout.py", "--label", "timeout2s",
                                 "--reps", str(a.reps), "--n", str(a.n)])
                csv_timeout = csv_generado(salida, "timeout2s")
            if not a.omitir_consistencia:
                salida = correr([py, "experiment/scripts/consistencia.py", "--label", "consistencia",
                                 "--reps", str(a.reps), "--n", str(a.n_consistencia)])
        finally:
            restaurar_sistema()

    csvs = [ultimo_csv("timeout2s")]
    if (a.solo_graficar or not a.omitir_consistencia) and list(RAW.glob("consistencia_*.csv")):
        csvs.append(ultimo_csv("consistencia"))
    salida = correr([py, "experiment/graficar_experimentos.py", *map(str, csvs)])

    resumen = RESULTS / f"resumen_{datetime.now():%Y%m%d_%H%M%S}.txt"
    resumen.write_text("\n".join(salida) + "\n", encoding="utf-8")
    print("\nListo.")
    print("  Datos crudos:", ", ".join(str(c.relative_to(ROOT)) for c in csvs))
    print("  Figuras:     experiment/results/")
    print("  Resumen:    ", resumen.relative_to(ROOT))


if __name__ == "__main__":
    main()
