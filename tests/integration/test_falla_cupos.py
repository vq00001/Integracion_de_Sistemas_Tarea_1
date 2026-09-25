"""T7 / D4: qué hace la API REST cuando Cupos no responde.
Apaga el contenedor `cupos` y lo vuelve a encender al terminar."""
import time

import pytest

from tests.conftest import CURSO_CON_CUPO, compose

pytestmark = pytest.mark.falla


@pytest.fixture
def cupos_caido():
    compose("stop", "cupos")
    yield
    compose("start", "cupos")
    time.sleep(3)  # margen para que gRPC vuelva a aceptar conexiones


def test_cupos_caido_responde_503_json_y_sin_colgarse(api, estudiante, cupos_caido):
    """Verifica que la caída de Cupos devuelve un error JSON 503/504 dentro del timeout."""
    t0 = time.perf_counter()
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO})
    dt = time.perf_counter() - t0

    assert r.status_code in (503, 504), f"se esperaba 503/504 y llegó {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/json")
    assert {"code", "message"} <= r.json()["error"].keys()
    assert dt < 5, f"la API tardó {dt:.1f}s: no hay timeout efectivo"


def test_falla_de_cupos_no_deja_matricula_fantasma(api, estudiante, cupos_caido):
    """Comprueba que una matrícula fallida por indisponibilidad de Cupos no queda registrada."""
    api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO})
    lista = api.get("/v1/enrollments", params={"student_id": estudiante["id"]})
    assert lista.status_code == 200
    assert lista.json()["items"] == []


def test_api_sigue_atendiendo_lo_que_no_depende_de_cupos(api, estudiante, cupos_caido):
    """Verifica que la API sigue consultando estudiantes aunque Cupos esté caído."""
    # degradación parcial: estudiantes no necesita a Cupos
    assert api.get(f"/v1/students/{estudiante['id']}").status_code == 200


def test_recuperacion_tras_reiniciar_cupos(api, estudiante):
    """Comprueba que la API vuelve a matricular correctamente después de reiniciar Cupos."""
    compose("restart", "cupos")
    time.sleep(3)
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO})
    assert r.status_code == 201, r.text
    api.post(f"/v1/enrollments/{r.json()['id']}/revert")
