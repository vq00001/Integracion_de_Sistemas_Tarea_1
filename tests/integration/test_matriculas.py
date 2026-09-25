"""Flujo funcional de la API REST (T2, T6) y su integración con Cupos."""
import pytest

from tests.conftest import CURSO_CON_CUPO


# ---------- T2: versionado, semántica HTTP, errores JSON ----------

def test_ruta_sin_version_no_existe(api):
    """Verifica que la API requiere versionado explícito en la ruta."""
    assert api.get("/students").status_code == 404


def test_crear_y_consultar_estudiante(api, estudiante):
    """Comprueba que se puede crear un estudiante y luego consultarlo por su id."""
    r = api.get(f"/v1/students/{estudiante['id']}")
    assert r.status_code == 200
    assert r.json()["email"] == estudiante["email"]


def test_estudiante_inexistente_da_404_json(api):
    """Confirma que consultar un estudiante no existente responde 404 en formato JSON."""
    r = api.get("/v1/students/no-existe-xyz")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert {"code", "message"} <= body["error"].keys()


def test_estudiante_invalido_da_400(api):
    """Valida que una creación con datos incompletos devuelve un error HTTP 422."""
    r = api.post("/v1/students", json={"nombre": "sin email"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/json")


def test_listar_estudiantes_incluye_el_creado(api, estudiante):
    """Comprueba que el listado de estudiantes incluye el recién creado."""
    r = api.get("/v1/students")
    assert r.status_code == 200
    assert estudiante["id"] in {e["id"] for e in r.json()["items"]}


# ---------- T6: autenticación ----------

def test_sin_credenciales_da_401(api_sin_auth):
    """Verifica que la API exige autenticación para acceder a recursos protegidos."""
    assert api_sin_auth.get("/v1/students").status_code == 401


def test_credenciales_invalidas_dan_401(api):
    """Comprueba que una API key inválida es rechazada con 403."""
    r = api.get("/v1/students", headers={"X-API-Key": "clave-incorrecta"})
    assert r.status_code == 403


# ---------- Matrículas ----------

def test_matricula_valida(api, estudiante):
    """Valida el flujo normal: crea una matrícula activa y luego la elimina limpiamente."""
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["estado"] == "CONFIRMADA"
    assert api.get(f"/v1/enrollments/{m['id']}").status_code == 200
    api.post(f"/v1/enrollments/{m['id']}/revert")  # limpieza (libera el cupo)


def test_matricula_sin_cupos_es_rechazada_con_409(api, estudiante, curso_lleno):
    """Verifica que no se crea una matrícula si el curso ya está agotado."""
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": curso_lleno})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SIN_CUPOS"
    # el rechazo no debe dejar una matrícula registrada
    lista = api.get("/v1/enrollments", params={"student_id": estudiante["id"]}).json()
    assert lista["items"] == []


def test_matricula_curso_inexistente_da_404(api, estudiante):
    """Comprueba que una matrícula para un curso inexistente devuelve 404."""
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": 999999})
    assert r.status_code == 404


def test_matricula_estudiante_inexistente_da_404(api):
    """Verifica que no se permite matricular a un estudiante que no existe."""
    r = api.post("/v1/enrollments", json={"student_id": "no-existe", "curso_id": CURSO_CON_CUPO})
    assert r.status_code == 404


def test_matricula_duplicada_da_409(api, estudiante):
    """Comprueba que intentar matricularse dos veces al mismo curso devuelve conflicto."""
    body = {"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO}
    primera = api.post("/v1/enrollments", json=body)
    assert primera.status_code == 201
    segunda = api.post("/v1/enrollments", json=body)
    assert segunda.status_code == 409
    api.post(f"/v1/enrollments/{primera.json()['id']}/revert")


def test_revertir_matricula(api, estudiante):
    """Prueba que una matrícula puede revertirse y pasar a estado ANULADA."""
    m = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO}).json()
    assert api.post(f"/v1/enrollments/{m['id']}/revert").status_code == 200
    despues = api.get(f"/v1/enrollments/{m['id']}")
    assert despues.status_code == 200 and despues.json()["estado"] == "ANULADA"


def test_revertir_inexistente_da_404(api):
    """Confirma que intentar revertir una matrícula inexistente responde 404."""
    assert api.post("/v1/enrollments/no-existe/revert").status_code == 404


# ---------- Integración REST <-> gRPC: el cupo realmente se mueve ----------

@pytest.mark.grpc
def test_matricular_y_revertir_mueve_el_cupo(api, estudiante, cupos_stub):
    """Verifica la integración real: la API debe descontar y devolver el cupo al revertir la matrícula."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    antes = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres

    m = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO}).json()
    durante = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres
    assert durante == antes - 1

    api.post(f"/v1/enrollments/{m['id']}/revert")
    despues = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres
    assert despues == antes
