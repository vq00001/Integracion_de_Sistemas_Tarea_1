"""Utilidades compartidas por los scripts de experimento."""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "dev-matriculas-key-123")
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
