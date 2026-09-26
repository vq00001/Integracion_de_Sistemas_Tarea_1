"""
Genera las figuras del informe de experimentación (timeout + Circuit Breaker)
a partir del CSV crudo del experimento.

Uso:
    python graficar_experimento.py resultados_experimento.csv

Requiere: pandas, matplotlib
"""

import sys
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else "resultados_experimento.csv"
OUT_DIR = Path("results")
OUT_DIR.mkdir(exist_ok=True)

df = pd.read_csv(CSV_PATH)

# ---------------------------------------------------------------
# Figura 1: latencia promedio vs. delay inyectado (región lineal)
# Se promedian solo las respuestas 201: los 404 son un error funcional
# conocido (curso inexistente) y no deben mezclarse con la medición
# de la dependencia lenta.
# ---------------------------------------------------------------
exitosas = df[df["status"] == 201]
prom = exitosas.groupby("delay_ms")["latency_ms"].mean().reset_index()

plt.figure(figsize=(6, 4))
plt.plot(prom["delay_ms"], prom["latency_ms"], marker="o", label="Latencia promedio (HTTP 201)")
plt.axvline(2000, color="crimson", linestyle="--", linewidth=1, label="Timeout configurado (2000 ms)")
plt.xlabel("Latencia introducida en Cupos (ms)")
plt.ylabel("Latencia de POST /v1/enrollments (ms)")
plt.title("Latencia del endpoint según el delay inyectado en Cupos")
plt.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / "latencia_vs_delay.png", dpi=150)
plt.close()

# ---------------------------------------------------------------
# Figura 2: transición del Circuit Breaker en un escenario que
# supera el timeout (delay = 2050 ms como caso representativo).
# ---------------------------------------------------------------
escenario = df[df["delay_ms"] == 2050].sort_values("iter")
colores = escenario["circuit_state"].map({"CLOSED": "tab:blue", "OPEN": "tab:red"})

plt.figure(figsize=(6, 4))
plt.plot(escenario["iter"], escenario["latency_ms"], color="lightgray", zorder=1)
plt.scatter(escenario["iter"], escenario["latency_ms"], c=colores, zorder=2)
for estado, color in [("CLOSED", "tab:blue"), ("OPEN", "tab:red")]:
    plt.scatter([], [], c=color, label=estado)  # entradas de leyenda
plt.xlabel("Número de solicitud (iteración)")
plt.ylabel("Latencia (ms)")
plt.title("Transición CLOSED → OPEN del Circuit Breaker (delay = 2050 ms)")
plt.legend(title="Estado del circuito")
plt.tight_layout()
plt.savefig(OUT_DIR / "circuit_breaker_transition.png", dpi=150)
plt.close()

# ---------------------------------------------------------------
# Figura 3: distribución de códigos HTTP por escenario.
# Complementa la Tabla de resultados del informe.
# ---------------------------------------------------------------
conteo = df.groupby(["delay_ms", "status"]).size().unstack(fill_value=0)
conteo.plot(kind="bar", stacked=True, figsize=(7, 4),
            color={201: "tab:green", 404: "tab:orange", 503: "tab:red"})
plt.xlabel("Delay introducido en Cupos (ms)")
plt.ylabel("Cantidad de respuestas")
plt.title("Distribución de códigos HTTP por escenario")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig(OUT_DIR / "status_por_delay.png", dpi=150)
plt.close()

print(f"Figuras guardadas en: {OUT_DIR.resolve()}")
