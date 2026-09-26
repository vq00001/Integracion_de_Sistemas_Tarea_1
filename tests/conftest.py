import importlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", os.getenv("MATRICULAS_API_KEYS", "dev-matriculas-key-123").split(",")[0].strip())
CURSO_CON_CUPO = int(os.getenv("CURSO_CON_CUPO", "1"))
CURSO_CAPACIDAD_BAJA = int(os.getenv("CURSO_CAPACIDAD_BAJA", "5"))
CURSO_LLENO = CURSO_CAPACIDAD_BAJA
CUPOS_GRPC_ADDR = os.getenv("CUPOS_GRPC_ADDR", "localhost:50051")
COMPOSE_ARGS = os.getenv("COMPOSE_ARGS", "-f docker-compose.yml -f docker-compose.test.yml").split()


class Api:
    """Cliente HTTP mínimo con la API key por defecto."""

    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}

    def request(self, method: str, path: str, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        kw.setdefault("timeout", 10)
        return requests.request(method, f"{self.base_url}{path}", headers=headers, **kw)

    def get(self, path, **kw): return self.request("GET", path, **kw)
    def post(self, path, **kw): return self.request("POST", path, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)


@pytest.fixture(scope="session")
def api() -> Api:
    return Api(BASE_URL, API_KEY)


@pytest.fixture(scope="session")
def api_sin_auth() -> Api:
    return Api(BASE_URL, None)


@pytest.fixture
def estudiante(api) -> dict:
    """Estudiante nuevo y único por prueba (evita colisiones entre tests)."""
    sufijo = uuid.uuid4().hex[:8]
    payload = {
        "rut": f"{sufijo[:8]}-{sufijo[-1]}",
        "nombre": f"Test {sufijo[:4]}",
        "apellido": "Api",
        "email": f"t{sufijo}@example.com",
        "telefono": "+56900000000",
    }
    r = api.post("/v1/students", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="session")
def cupos_stub():
    """Stub gRPC generado al vuelo desde proto/cupos.proto (paquete `cupo`,
    servicio `Cupos`, métodos camelCase, claves de idempotencia en Ocupar/Liberar)."""
    grpc = pytest.importorskip("grpc")
    tools = pytest.importorskip("grpc_tools.protoc")
    out = ROOT / "tests" / ".generated"
    out.mkdir(exist_ok=True)
    proto_dir = ROOT / "proto"
    rc = tools.main(["protoc", f"-I{proto_dir}", f"--python_out={out}", f"--grpc_python_out={out}",
                     str(proto_dir / "cupos.proto")])
    assert rc == 0, "no compiló cupos.proto"
    sys.path.insert(0, str(out))
    pb2 = importlib.import_module("cupos_pb2")
    pb2_grpc = importlib.import_module("cupos_pb2_grpc")
    channel = grpc.insecure_channel(CUPOS_GRPC_ADDR)
    try:
        grpc.channel_ready_future(channel).result(timeout=3)
    except grpc.FutureTimeoutError:
        pytest.skip(f"Cupos gRPC no accesible en {CUPOS_GRPC_ADDR} (¿levantaste el servicio?)")
    stub = pb2_grpc.CuposStub(channel)
    return type("CuposCtx", (), {"pb2": pb2, "stub": stub, "grpc": grpc})


@pytest.fixture
def curso_lleno(cupos_stub):
    """Vacía CURSO_CAPACIDAD_BAJA (id 5, 2 cupos en la semilla real) ocupando sus cupos
    con claves de idempotencia propias de esta prueba, y lo restaura al terminar. La
    semilla real de Cupos no trae ningún curso con cupos_libres=0 de fábrica."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    claves = []
    libres = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CAPACIDAD_BAJA)).cupos_libres
    for _ in range(libres):
        clave = f"setup-lleno-{uuid.uuid4().hex[:8]}"
        stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CAPACIDAD_BAJA, idempotency_key=clave))
        claves.append(clave)
    yield CURSO_CAPACIDAD_BAJA
    for clave in claves:
        stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CAPACIDAD_BAJA, idempotency_key=f"teardown-{clave}"))


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *COMPOSE_ARGS, *args], cwd=ROOT,
                          check=True, capture_output=True, text=True)
