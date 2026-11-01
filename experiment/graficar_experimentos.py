"""
Genera las figuras y tablas del informe a partir de los CSV crudos de experiment/data/raw/.

Uso (desde la raíz del repositorio):
    python experiment/graficar_experimentos.py <csv_timeout> [<csv_consistencia>] [<csv_throughput>]
Los CSV se reconocen por su contenido (columnas), no por el orden. Requiere: pandas, matplotlib.
Figuras y tablas (.csv y .md) quedan en experiment/results/.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = Path(__file__).resolve().parent / "results"
OUT_DIR.mkdir(exist_ok=True)

if len(sys.argv) < 2:
    sys.exit(__doc__)

tablas = {}
for ruta in sys.argv[1:]:
    d = pd.read_csv(ruta)
    if "delay_ms" in d.columns:
        tablas["timeout"] = d
    elif "huerfanos_tras_timeout" in d.columns:
        tablas["consistencia"] = d
    elif "ciclos_por_s" in d.columns or "rps" in d.columns:
        tablas["throughput"] = d


def guardar_tabla(nombre: str, df: pd.DataFrame) -> None:
    df.to_csv(OUT_DIR / f"{nombre}.csv")
    (OUT_DIR / f"{nombre}.md").write_text(df.to_markdown(floatfmt=".1f") if hasattr(df, "to_markdown") and _tiene_tabulate()
                                          else df.round(1).to_string(), encoding="utf-8")


def _tiene_tabulate() -> bool:
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


# =====================================================================
# Experimento 1: timeout y circuit breaker
# =====================================================================
if "timeout" in tablas:
    df = tablas["timeout"].copy()
    if "rep" not in df.columns:
        df["rep"] = 1
    if "fase" not in df.columns:           # CSV de una versión anterior: no había fases separadas
        df["fase"] = "medicion"
    timeout_s = float(df["timeout_s"].iloc[0]) if "timeout_s" in df.columns else 2.0
    timeout_ms = timeout_s * 1000
    reps = df["rep"].nunique()
    med = df[df["fase"] == "medicion"]

    # --- Tabla: códigos por escenario (promedio por repetición) + estados del circuito
    conteo = med.groupby(["delay_ms", "status"]).size().unstack(fill_value=0) / reps
    conteo["Circuit Breaker"] = med.groupby("delay_ms")["circuit_state"].agg(lambda s: " → ".join(dict.fromkeys(s)))
    print(f"\nExperimento 1 — resumen por escenario (promedio de {reps} repetición/es, timeout={timeout_s:g} s)\n")
    print(conteo.round(1).to_string())
    guardar_tabla("exp1_resumen_por_escenario", conteo.round(1))

    # --- Tabla: latencia por escenario y código (media, p50, p95)
    lat = (med.groupby(["delay_ms", "status"])["latency_ms"]
              .agg(n="count", media="mean", p50="median", p95=lambda s: s.quantile(0.95), desv="std").round(1))
    print("\nLatencia (ms) por escenario y código HTTP\n")
    print(lat.to_string())
    guardar_tabla("exp1_latencia_por_escenario", lat)

    # --- Control funcional 404 (fase propia, Cupos sin latencia)
    ctrl = df[df["fase"] == "control_404"]
    if len(ctrl):
        r = ctrl.groupby("status")["latency_ms"].agg(["count", "mean"]).round(1)
        print("\nControl funcional (curso inexistente, Cupos sin latencia)\n")
        print(r.to_string())
        guardar_tabla("exp1_control_404", r)

    # --- Fig. 1: latencia vs delay. Solo 201; se marca el timeout configurado.
    ok = med[med["status"] == 201]
    g = ok.groupby("delay_ms")["latency_ms"].agg(["mean", "std"]).reset_index().fillna(0)
    plt.figure(figsize=(6, 4))
    plt.errorbar(g["delay_ms"], g["mean"], yerr=g["std"], marker="o", capsize=3,
                 label=f"Latencia media ± σ (solo HTTP 201, {reps} rep.)")
    plt.plot([0, med["delay_ms"].max()], [0, med["delay_ms"].max()], ":", color="gray", label="y = delay inyectado")
    plt.axvline(timeout_ms, color="crimson", linestyle="--", linewidth=1, label=f"Timeout configurado ({timeout_ms:.0f} ms)")
    plt.xlabel("Latencia introducida en Cupos (ms)")
    plt.ylabel("Latencia de POST /v1/enrollments (ms)")
    plt.title("Latencia del endpoint según el delay inyectado en Cupos")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "latencia_vs_delay.png", dpi=150)
    plt.close()

    # --- Fig. 2: transición del circuito. Se elige el primer escenario por encima del timeout y
    #     el título se arma con ese valor y con lo OBSERVADO (antes decía "CLOSED → OPEN" fijo,
    #     aunque los datos mostraran el circuito siempre CLOSED).
    sobre = sorted(d for d in med["delay_ms"].unique() if d > timeout_ms)
    if sobre:
        d0 = sobre[0]
        esc = med[(med["delay_ms"] == d0) & (med["rep"] == med["rep"].min())].sort_values("iter")
        observado = " → ".join(dict.fromkeys(esc["circuit_state"]))
        plt.figure(figsize=(6, 4))
        plt.plot(esc["iter"], esc["latency_ms"], color="lightgray", zorder=1)
        for estado, color in [("CLOSED", "tab:blue"), ("OPEN", "tab:red"), ("HALF_OPEN", "tab:orange")]:
            for codigo, marca in [(504, "o"), (503, "^"), (201, "s")]:
                sel = esc[(esc["circuit_state"] == estado) & (esc["status"] == codigo)]
                if len(sel):
                    plt.scatter(sel["iter"], sel["latency_ms"], c=color, marker=marca, zorder=2,
                                label=f"{estado} · HTTP {codigo}")
        plt.xlabel("Número de solicitud (iteración)")
        plt.ylabel("Latencia (ms)")
        plt.title(f"Circuit Breaker observado: {observado} (delay = {d0} ms)")
        plt.legend(fontsize=7)
        plt.tight_layout()
        plt.savefig(OUT_DIR / "circuit_breaker_transition.png", dpi=150)
        plt.close()
        print(f"\nCircuit breaker en delay={d0} ms (rep {int(med['rep'].min())}): {observado}")

    # --- Fig. 3: códigos HTTP por escenario
    solo_codigos = conteo.drop(columns=["Circuit Breaker"])
    colores = {201: "tab:green", 404: "tab:orange", 503: "tab:red", 504: "tab:purple"}
    solo_codigos.plot(kind="bar", stacked=True, figsize=(7, 4), color=[colores.get(c, "gray") for c in solo_codigos.columns])
    plt.xlabel("Delay introducido en Cupos (ms)")
    plt.ylabel("Respuestas por repetición")
    plt.title("Distribución de códigos HTTP por escenario")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "status_por_delay.png", dpi=150)
    plt.close()

# =====================================================================
# Experimento 2: consistencia de cupos
# =====================================================================
if "consistencia" in tablas:
    c = tablas["consistencia"]
    claves = [k for k in ("modo", "n") if k in c.columns]
    m = c.groupby(claves)[["timeouts_504", "huerfanos_tras_timeout", "pendientes_tras_timeout",
                           "huerfanos_final", "pendientes_final", "segundos_hasta_resolver"]].agg(["mean", "std"]).round(1)
    print("\nExperimento 2 — consistencia tras timeout (media y desviación entre repeticiones)\n")
    print(m.to_string())
    guardar_tabla("exp2_consistencia", m)
    base = c.groupby("modo")[["huerfanos_tras_timeout", "huerfanos_final", "pendientes_final"]].mean()
    base.plot(kind="bar", figsize=(7, 4))
    plt.ylabel("Cupos / matrículas (media por corrida)")
    plt.xlabel("")
    cfg = f"TTL={c['ttl_s'].iloc[0]} s" if "ttl_s" in c.columns else ""
    plt.title(f"Huérfanos y pendientes tras un timeout, por estrategia ({cfg})")
    plt.xticks(rotation=0)
    plt.legend(["Huérfanos justo tras el timeout", "Huérfanos al final", "Pendientes al final"], fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "consistencia_cupos.png", dpi=150)
    plt.close()

# =====================================================================
# Experimento 3: throughput
# =====================================================================
if "throughput" in tablas:
    t = tablas["throughput"]
    col_rps = "ciclos_por_s" if "ciclos_por_s" in t.columns else "rps"
    g = t.groupby("concurrencia")[[col_rps, "p50_ms", "p95_ms"]].agg(["mean", "std"]).fillna(0)
    print("\nExperimento 3 — throughput (media ± desviación entre repeticiones)\n")
    print(g.round(1).to_string())
    guardar_tabla("exp3_throughput", g.round(1))
    fig, ax1 = plt.subplots(figsize=(6.5, 4))
    ax1.errorbar(g.index, g[(col_rps, "mean")], yerr=g[(col_rps, "std")], marker="o", capsize=3, color="tab:blue")
    ax1.set_xlabel("Concurrencia (hilos del generador)")
    ax1.set_ylabel("Ciclos por segundo (crear + matricular + revertir)", color="tab:blue")
    ax2 = ax1.twinx()
    ax2.errorbar(g.index, g[("p95_ms", "mean")], yerr=g[("p95_ms", "std")], marker="s", capsize=3, color="tab:red")
    ax2.set_ylabel("p95 de POST /v1/enrollments (ms)", color="tab:red")
    plt.title("Throughput y latencia p95 según la concurrencia")
    fig.tight_layout()
    plt.savefig(OUT_DIR / "throughput.png", dpi=150)
    plt.close()

print(f"\nFiguras y tablas guardadas en: {OUT_DIR}")
