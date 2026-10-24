"""T7 / D4: qué hace la API REST cuando Cupos no responde, responde lento o se recupera.

Estas pruebas detienen/reinician contenedores (marcador `falla`): correrlas sin paralelismo.
"""
import time
import uuid

import pytest

from tests.conftest import CURSO_CON_CUPO, compose, esperar_api

pytestmark = pytest.mark.falla

CURSO = CURSO_CON_CUPO


@pytest.fixture
def cupos_caido():
    compose("stop", "cupos")
    yield
    compose("start", "cupos")
    time.sleep(3)  # margen para que gRPC vuelva a aceptar conexiones


def test_cupos_caido_responde_503_o_504_json_y_sin_colgarse(api, estudiante, cupos_caido):
    """La caída de Cupos produce un error JSON claro (503 si no hay conexión, 504 si vence
    el plazo) dentro del timeout, nunca una espera indefinida ni un 500."""
    t0 = time.perf_counter()
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO})
    dt = time.perf_counter() - t0

    assert r.status_code in (503, 504), f"se esperaba 503/504 y llegó {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] in ("CUPOS_NO_DISPONIBLE", "CUPOS_TIMEOUT")
    assert r.headers.get("retry-after")
    assert dt < 5, f"la API tardó {dt:.1f}s: no hay timeout efectivo"


def test_falla_de_cupos_no_deja_matricula_confirmada_fantasma(api, estudiante, cupos_caido):
    """Una matrícula fallida por Cupos nunca queda CONFIRMADA. (Puede quedar PENDIENTE
    si el resultado fue incierto; el conciliador la cierra: ver test_cupos_lento...)"""
    api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO})
    lista = api.get("/v1/enrollments", params={"student_id": estudiante["id"], "estado": "CONFIRMADA"})
    assert lista.status_code == 200
    assert lista.json()["items"] == []


def test_api_sigue_atendiendo_lo_que_no_depende_de_cupos(api, estudiante, cupos_caido):
    """Degradación parcial: consultar estudiantes no necesita a Cupos."""
    assert api.get(f"/v1/students/{estudiante['id']}").status_code == 200


def test_recuperacion_tras_reiniciar_cupos(api, estudiante):
    """La API vuelve a matricular después de reiniciar Cupos."""
    compose("restart", "cupos")
    time.sleep(3)
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO})
    assert r.status_code in (201, 503), r.text  # 503 si el circuito aún no se recupera
    if r.status_code == 201:
        api.post(f"/v1/enrollments/{r.json()['id']}/revert")


# ---------------------------------------------------------------------------
# Cupos lento: el timeout deja un resultado INCIERTO que hay que conciliar
# ---------------------------------------------------------------------------

@pytest.fixture
def cupos_lento(api):
    """Cupos tarda 3 s (> timeout de 2 s). La API arranca con TTL largo y
    conciliador rápido para que la prueba sea corta."""
    env_api = {"PENDING_TTL_SECONDS": "40", "RECONCILE_INTERVAL_SECONDS": "2",
               "CUPOS_CB_RECOVERY_SECONDS": "2"}
    compose("up", "-d", "cupos", env={"SIMULAR_LATENCIA_MS": "3000"})
    compose("up", "-d", "--no-deps", "matriculas-api", env=env_api)
    time.sleep(2)
    esperar_api(api)
    yield env_api
    compose("up", "-d", "cupos")  # latencia 0 (valor por defecto)
    compose("up", "-d", "--no-deps", "matriculas-api")  # configuración por defecto
    time.sleep(2)
    esperar_api(api)


def _libres(cupos_stub):
    pb2 = cupos_stub.pb2
    return cupos_stub.stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO)).cupos_libres


def _restaurar_cupos_rapido(api, env_api):
    compose("up", "-d", "cupos")  # sin latencia
    time.sleep(3)


@pytest.mark.grpc
def test_timeout_da_504_y_el_cupo_huerfano_se_concilia(api, estudiante, cupos_stub, cupos_lento):
    """Con Cupos lento: 504 (resultado incierto), la matrícula queda PENDIENTE y, si el
    cliente no reintenta, el conciliador la anula y el cupo vuelve a quedar libre."""
    libres_antes = _libres(cupos_stub)

    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO})
    assert r.status_code == 504, r.text
    assert r.json()["error"]["code"] == "CUPOS_TIMEOUT"
    enrollment_id = r.json()["error"]["details"]["enrollment_id"]
    assert api.get(f"/v1/enrollments/{enrollment_id}").json()["estado"] == "PENDIENTE"

    # Cupos termina su operación lenta: el cupo queda ocupado SIN matrícula confirmada.
    time.sleep(3.5)
    assert _libres(cupos_stub) == libres_antes - 1

    # Cupos vuelve a la normalidad; el conciliador debe cerrar la matrícula y liberar el cupo.
    _restaurar_cupos_rapido(api, cupos_lento)
    # (TTL de 40 s: se reduce reiniciando la API con TTL corto)
    compose("up", "-d", "--no-deps", "matriculas-api", env={"PENDING_TTL_SECONDS": "4",
                                                "RECONCILE_INTERVAL_SECONDS": "1",
                                                "CUPOS_CB_RECOVERY_SECONDS": "2"})
    esperar_api(api)
    limite = time.monotonic() + 40
    estado = "PENDIENTE"
    while time.monotonic() < limite and estado == "PENDIENTE":
        time.sleep(1)
        estado = api.get(f"/v1/enrollments/{enrollment_id}").json()["estado"]
    assert estado == "ANULADA"
    assert "conciliación" in api.get(f"/v1/enrollments/{enrollment_id}").json()["motivo"]
    assert _libres(cupos_stub) == libres_antes, "el cupo huérfano no fue devuelto"


@pytest.mark.grpc
def test_reintentar_tras_timeout_con_la_misma_clave_consume_un_solo_cupo(api, estudiante, cupos_stub, cupos_lento):
    """Si el estudiante reintenta tres veces tras un timeout, se consume UN cupo, y la
    matrícula termina CONFIRMADA con el resultado real."""
    libres_antes = _libres(cupos_stub)
    clave = {"Idempotency-Key": str(uuid.uuid4())}
    cuerpo = {"student_id": estudiante["id"], "curso_id": CURSO}

    r1 = api.post("/v1/enrollments", json=cuerpo, headers=clave)
    assert r1.status_code == 504
    enrollment_id = r1.json()["error"]["details"]["enrollment_id"]

    compose("up", "-d", "cupos")  # Cupos vuelve a ser rápido
    time.sleep(5)  # arranque + recuperación del circuito (2 s)

    ids = set()
    for _ in range(3):
        r = api.post("/v1/enrollments", json=cuerpo, headers=clave)
        assert r.status_code == 201, r.text
        assert r.json()["estado"] == "CONFIRMADA"
        ids.add(r.json()["id"])
    assert ids == {enrollment_id}
    assert _libres(cupos_stub) == libres_antes - 1, "los reintentos consumieron más de un cupo"

    api.post(f"/v1/enrollments/{enrollment_id}/revert")  # limpieza
    assert _libres(cupos_stub) == libres_antes
