"""
Genera las figuras y tablas del informe a partir de los CSV crudos de experiment/data/raw/.

Uso (desde la raíz del repositorio):
    python experiment/graficar_experimentos.py experiment/data/raw/timeout2s_<fecha>.csv
    python experiment/graficar_experimentos.py <csv_timeout> <csv_consistencia>

Requiere: pandas, matplotlib
Las figuras quedan en experiment/results/.
"""
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = Path(__file__).resolve().parent / "results"
OUT_DIR.mkdir(exist_ok=True)
TIMEOUT_MS = 2000  # CUPOS_GRPC_TIMEOUT_SECONDS por defecto

if len(sys.argv) < 2:
    sys.exit(__doc__)

df = pd.read_csv(sys.argv[1])
if "rep" not in df.columns:      # CSV de una corrida antigua, sin repeticiones
    df["rep"] = 1
reps = df["rep"].nunique()

# ---------------------------------------------------------------
# Tabla resumen por escenario (promedio por repetición) → informe
# ---------------------------------------------------------------
conteo = df.groupby(["delay_ms", "status"]).size().unstack(fill_value=0) / reps
estados_circuito = (df.groupby("delay_ms")["circuit_state"]
                    .agg(lambda s: " → ".join(dict.fromkeys(s))))
resumen = conteo.copy()
resumen["Circuit Breaker"] = estados_circuito
print(f"\nResumen por escenario (promedio de {reps} repetición/es)\n")
print(resumen.round(1).to_string())

# ---------------------------------------------------------------
# Figura 1: latencia vs. delay inyectado, con desviación estándar.
# Solo respuestas 201: los 404 (curso inexistente) son un control funcional
# y no deben mezclarse con la medición de la dependencia lenta.
# ---------------------------------------------------------------
ok = df[df["status"] == 201]
g = ok.groupby("delay_ms")["latency_ms"].agg(["mean", "std"]).reset_index().fillna(0)
plt.figure(figsize=(6, 4))
plt.errorbar(g["delay_ms"], g["mean"], yerr=g["std"], marker="o", capsize=3,
             label=f"Latencia media ± σ (HTTP 201, {reps} rep.)")
plt.axvline(TIMEOUT_MS, color="crimson", linestyle="--", linewidth=1, label="Timeout configurado (2000 ms)")
plt.xlabel("Latencia introducida en Cupos (ms)")
plt.ylabel("Latencia de POST /v1/enrollments (ms)")
plt.title("Latencia del endpoint según el delay inyectado en Cupos")
plt.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / "latencia_vs_delay.png", dpi=150)
plt.close()

# ---------------------------------------------------------------
# Figura 2: transición del Circuit Breaker en el escenario 2050 ms
# (primera repetición). Color = estado del circuito; forma = código HTTP.
# ---------------------------------------------------------------
esc = df[(df["delay_ms"] == 2050) & (df["rep"] == df["rep"].min())].sort_values("iter")
plt.figure(figsize=(6, 4))
plt.plot(esc["iter"], esc["latency_ms"], color="lightgray", zorder=1)
for estado, color in [("CLOSED", "tab:blue"), ("OPEN", "tab:red")]:
    for codigo, marca in [(504, "o"), (503, "^"), (404, "s")]:
        sel = esc[(esc["circuit_state"] == estado) & (esc["status"] == codigo)]
        if len(sel):
            plt.scatter(sel["iter"], sel["latency_ms"], c=color, marker=marca, zorder=2,
                        label=f"{estado} · HTTP {codigo}")
plt.xlabel("Número de solicitud (iteración)")
plt.ylabel("Latencia (ms)")
plt.title("Circuit Breaker: CLOSED → OPEN (delay = 2050 ms)")
plt.legend(fontsize=7)
plt.tight_layout()
plt.savefig(OUT_DIR / "circuit_breaker_transition.png", dpi=150)
plt.close()

# ---------------------------------------------------------------
# Figura 3: distribución de códigos HTTP por escenario (promedio por repetición).
# ---------------------------------------------------------------
colores = {201: "tab:green", 404: "tab:orange", 503: "tab:red", 504: "tab:purple"}
conteo.plot(kind="bar", stacked=True, figsize=(7, 4),
            color=[colores.get(c, "gray") for c in conteo.columns])
plt.xlabel("Delay introducido en Cupos (ms)")
plt.ylabel("Respuestas por repetición")
plt.title("Distribución de códigos HTTP por escenario")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig(OUT_DIR / "status_por_delay.png", dpi=150)
plt.close()

# ---------------------------------------------------------------
# Figura 4 (opcional): consistencia de cupos tras un timeout
# ---------------------------------------------------------------
if len(sys.argv) > 2:
    c = pd.read_csv(sys.argv[2])
    m = c.groupby("modo")[["huerfanos_tras_timeout", "huerfanos_final", "segundos_hasta_resolver"]].mean()
    print("\nConsistencia tras timeout (promedio por repetición)\n")
    print(m.round(1).to_string())
    m[["huerfanos_tras_timeout", "huerfanos_final"]].plot(kind="bar", figsize=(6, 4))
    plt.ylabel("Cupos huérfanos (ocupados sin matrícula confirmada)")
    plt.xlabel("")
    plt.title("Cupos huérfanos tras un timeout, con y sin conciliador")
    plt.xticks(rotation=0)
    plt.legend(["Justo tras el timeout", "Tras recuperar Cupos y esperar"])
    plt.tight_layout()
    plt.savefig(OUT_DIR / "consistencia_cupos.png", dpi=150)
    plt.close()

print(f"\nFiguras guardadas en: {OUT_DIR}")
