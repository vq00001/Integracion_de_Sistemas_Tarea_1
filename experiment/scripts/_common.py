"""Utilidades compartidas por los scripts de experimento."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


def _clave_admin() -> str:
    """API_KEY / MATRICULAS_API_KEYS del entorno o del .env (python scripts/generar_env.py)."""
    for nombre in ("API_KEY", "MATRICULAS_API_KEYS"):
        valor = os.getenv(nombre, "").split(",")[0].strip()
        if valor:
            return valor
    env = ROOT / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.startswith("MATRICULAS_API_KEYS="):
                return linea.split("=", 1)[1].split(",")[0].strip()
    raise SystemExit("Falta la clave admin: ejecuta `python scripts/generar_env.py` o define API_KEY.")


API_KEY = _clave_admin()
HEAD = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
COMPOSE_ARGS = [a for a in os.getenv("COMPOSE_ARGS", "").split() if a]


def compose(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    """`docker compose ...`. `env` agrega variables que compose sustituye en el YAML."""
    return subprocess.run(["docker", "compose", *COMPOSE_ARGS, *args], cwd=ROOT,
                          check=check, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def rut_valido() -> str:
    """RUT con dígito verificador correcto (la API valida el módulo 11)."""
    cuerpo = str(10_000_000 + uuid.uuid4().int % 80_000_000)
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - suma % 11
    return f"{cuerpo}-{'0' if resto == 11 else 'K' if resto == 10 else resto}"


def crear_estudiante() -> str:
    s = uuid.uuid4().hex[:10]
    r = requests.post(f"{BASE_URL}/v1/students", headers=HEAD, timeout=30, json={
        "rut": rut_valido(), "nombre": "Bench", "apellido": s, "email": f"bench-{s}@example.com",
    })
    r.raise_for_status()
    return r.json()["id"]


def matricular(student_id: str, curso_id: int, idempotency_key: str | None = None):
    """POST /v1/enrollments. Devuelve (status_code, latencia_ms, body|None)."""
    headers = dict(HEAD)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{BASE_URL}/v1/enrollments", headers=headers, timeout=30,
                          json={"student_id": student_id, "curso_id": curso_id})
        ms = (time.perf_counter() - t0) * 1000
        return r.status_code, ms, (r.json() if r.content else None)
    except requests.RequestException:
        return 0, (time.perf_counter() - t0) * 1000, None


def revertir(enrollment_id: str):
    try:
        requests.post(f"{BASE_URL}/v1/enrollments/{enrollment_id}/revert", headers=HEAD, timeout=30)
    except requests.RequestException:
        pass


def health() -> dict:
    try:
        return requests.get(f"{BASE_URL}/v1/health", timeout=5).json()
    except requests.RequestException:
        return {"status": "sin respuesta"}


def esperar_api(segundos: float = 40) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if health().get("status") == "ok":
            return
        time.sleep(0.5)
    raise RuntimeError("la API no respondió a tiempo")


def total_matriculas(estado: str, curso_id: int) -> int:
    """Cantidad de matrículas de un curso en un estado (vía la API pública)."""
    r = requests.get(f"{BASE_URL}/v1/enrollments", headers=HEAD, timeout=30,
                     params={"estado": estado, "curso_id": curso_id, "limit": 1})
    r.raise_for_status()
    return r.json()["total"]


def cupos_libres(curso_id: int) -> int:
    """Cupos libres de un curso leídos directamente de la base de Cupos (medición
    "caja blanca" dentro del contenedor: el experimento necesita la verdad de Cupos,
    no lo que cree la API)."""
    codigo = ("import os, sqlite3; "
              f"print(sqlite3.connect(os.environ['DB_PATH']).execute("
              f"'SELECT cupos_libres FROM cursos WHERE curso_id={int(curso_id)}').fetchone()[0])")
    r = compose("exec", "-T", "cupos", "python", "-c", codigo)
    return int(r.stdout.strip())


# ---------------------------------------------------------------------------
# Configuración CONTROLADA de cada corrida (hallazgos 3, 13 y 15 de la revisión 3)
# ---------------------------------------------------------------------------
# Antes los scripts reiniciaban solo parte del sistema y dejaban valores de la corrida anterior
# (p. ej. umbral del circuit breaker en 1000), de modo que el circuito nunca abría y la figura
# "CLOSED → OPEN" no mostraba ninguna transición. Ahora cada escenario recrea Cupos y la API
# con una configuración EXPLÍCITA, LEE de vuelta la configuración efectiva de los contenedores y
# aborta si no coincide; esa configuración se escribe en cada fila del CSV.

CONFIG_API_DEFECTO = {
    "CUPOS_GRPC_TIMEOUT_SECONDS": "2.0",
    "CUPOS_CB_FAILURE_THRESHOLD": "5",
    "CUPOS_CB_RECOVERY_SECONDS": "15.0",
    "PENDING_TTL_SECONDS": "30",
    "RECONCILE_INTERVAL_SECONDS": "10",
}
CONFIG_CUPOS_DEFECTO = {"SIMULAR_LATENCIA_MS": "0", "SIMULAR_LATENCIA_MODO": "antes"}

_SETTINGS = {  # variable de entorno de la API -> atributo de app.config.settings
    "CUPOS_GRPC_TIMEOUT_SECONDS": "cupos_grpc_timeout_seconds",
    "CUPOS_CB_FAILURE_THRESHOLD": "cupos_cb_failure_threshold",
    "CUPOS_CB_RECOVERY_SECONDS": "cupos_cb_recovery_seconds",
    "PENDING_TTL_SECONDS": "pending_ttl_seconds",
    "RECONCILE_INTERVAL_SECONDS": "reconcile_interval_seconds",
}


def config_vigente() -> dict:
    """Configuración EFECTIVA leída dentro de los contenedores (no la que se intentó pasar)."""
    codigo_api = ("import json; from app.config import settings as s; "
                  f"print(json.dumps({{k: getattr(s, v) for k, v in {_SETTINGS!r}.items()}}))")
    api = json.loads(compose("exec", "-T", "matriculas-api", "python", "-c", codigo_api).stdout)
    codigo_cupos = ("import json, os; print(json.dumps({k: os.environ.get(k, '') "
                    f"for k in {list(CONFIG_CUPOS_DEFECTO)!r}}}))")
    cupos = json.loads(compose("exec", "-T", "cupos", "python", "-c", codigo_cupos).stdout)
    return {**{k: str(v) for k, v in api.items()}, **cupos}


def _igual(a: str, b: str) -> bool:
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def levantar(cupos: dict | None = None, api: dict | None = None) -> dict:
    """Recrea Cupos y la API con configuración explícita, espera a que estén sanas con el circuito
    CERRADO, verifica la configuración efectiva y la devuelve (para escribirla en el CSV)."""
    cfg_cupos = {**CONFIG_CUPOS_DEFECTO, **(cupos or {})}
    cfg_api = {**CONFIG_API_DEFECTO, **(api or {})}
    compose("up", "-d", "--force-recreate", "--no-deps", "cupos", env={**cfg_cupos})
    compose("up", "-d", "--force-recreate", "--no-deps", "matriculas-api", env={**cfg_api})
    esperar_api(60)
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        if health().get("dependencies", {}).get("cupos_grpc", {}).get("circuit_state") == "CLOSED":
            break
        time.sleep(0.5)
    efectiva = config_vigente()
    esperada = {**cfg_api, **cfg_cupos}
    for k, v in esperada.items():
        if not _igual(efectiva.get(k, ""), v):
            raise RuntimeError(f"La configuración efectiva no coincide: {k} esperado={v} efectivo={efectiva.get(k)}")
    return efectiva


def entorno() -> dict:
    """Datos del equipo para interpretar los resultados (se escriben en los CSV de rendimiento)."""
    return {"os": platform.platform(), "cpus": os.cpu_count(), "python": platform.python_version()}


def calibrar_reloj() -> float:
    """Factor reloj_contenedor / reloj_anfitrión (1.0 = sin deriva).

    En el experimento 1 las latencias medidas fueron ~92-94 % del retraso inyectado y el timeout de 2 s
    se observó en ~1830 ms. Una deriva entre el reloj del contenedor (donde corre sleep/el deadline
    gRPC) y el del anfitrión (donde se mide) lo explicaría. Se mide con DOS ventanas (2 s y 8 s) y la
    pendiente, que elimina el costo fijo de `docker compose exec`:
        factor = (c8 - c2) / (h8 - h2)     c = segundos medidos DENTRO, h = segundos medidos FUERA.
    """
    def ventana(seg: int) -> tuple[float, float]:
        codigo = f"import time; t=time.monotonic(); time.sleep({seg}); print(time.monotonic()-t)"
        t0 = time.perf_counter()
        r = compose("exec", "-T", "cupos", "python", "-c", codigo)
        return float(r.stdout.strip()), time.perf_counter() - t0

    c2, h2 = ventana(2)
    c8, h8 = ventana(8)
    return (c8 - c2) / (h8 - h2)
