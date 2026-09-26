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


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *COMPOSE_ARGS, *args], cwd=ROOT,
                          check=check, capture_output=True, text=True)


def crear_estudiante() -> str:
    s = uuid.uuid4().hex[:10]
    r = requests.post(f"{BASE_URL}/v1/students", headers=HEAD, timeout=30, json={
        "rut": f"{int(s[:8], 16) % 90_000_000 + 10_000_000}-{s[8]}",
        "nombre": "Bench", "apellido": s, "email": f"bench-{s}@example.com",
    })
    r.raise_for_status()
    return r.json()["id"]


def matricular(student_id: str, curso_id: int):
    """POST /v1/enrollments. Devuelve (status_code, latencia_ms, body|None)."""
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{BASE_URL}/v1/enrollments", headers=HEAD, timeout=30,
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
