"""Casos límite de consistencia entre Matrículas y Cupos (concurrencia, reversiones
repetidas, idempotencia). Cada prueba verifica también cuántos cupos se consumieron
realmente en Cupos, no solo lo que responde la API."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.conftest import CURSO_CON_CUPO

pytestmark = pytest.mark.grpc

CURSO = CURSO_CON_CUPO


def _libres(cupos_stub):
    pb2 = cupos_stub.pb2
    return cupos_stub.stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO)).cupos_libres


def _paralelo(n, fn):
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda _: fn(), range(n)))


def test_matricular_revertir_dos_veces_el_mismo_curso(api, estudiante, cupos_stub):
    """Matricular → revertir → matricular → revertir no falla ni deja cupos inconsistentes
    (antes la restricción única impedía una segunda fila ANULADA y devolvía 500)."""
    antes = _libres(cupos_stub)
    ids = []
    for _ in range(2):
        m = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO})
        assert m.status_code == 201, m.text
        r = api.post(f"/v1/enrollments/{m.json()['id']}/revert")
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "ANULADA"
        ids.append(m.json()["id"])
    assert len(set(ids)) == 2
    assert _libres(cupos_stub) == antes
    lista = api.get("/v1/enrollments", params={"student_id": estudiante["id"]}).json()
    assert sorted(i["estado"] for i in lista["items"]) == ["ANULADA", "ANULADA"]


def test_matriculas_simultaneas_del_mismo_estudiante_consumen_un_solo_cupo(api, estudiante, cupos_stub):
    """6 peticiones simultáneas del mismo estudiante y curso: una gana (201), el resto 409,
    y Cupos ocupa exactamente un cupo (antes quedaban cupos huérfanos y 500)."""
    antes = _libres(cupos_stub)
    cuerpo = {"student_id": estudiante["id"], "curso_id": CURSO}
    respuestas = _paralelo(6, lambda: api.post("/v1/enrollments", json=cuerpo))
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [201, 409, 409, 409, 409, 409], codigos
    assert _libres(cupos_stub) == antes - 1
    ganadora = next(r for r in respuestas if r.status_code == 201).json()
    api.post(f"/v1/enrollments/{ganadora['id']}/revert")
    assert _libres(cupos_stub) == antes


def test_reversiones_simultaneas_liberan_un_solo_cupo(api, estudiante, cupos_stub):
    """6 reversiones simultáneas de la misma matrícula: una 200, el resto 409, sin 5xx,
    y se libera un solo cupo."""
    antes = _libres(cupos_stub)
    m = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO}).json()
    assert _libres(cupos_stub) == antes - 1
    respuestas = _paralelo(6, lambda: api.post(f"/v1/enrollments/{m['id']}/revert"))
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [200, 409, 409, 409, 409, 409], codigos
    assert _libres(cupos_stub) == antes


def test_idempotency_key_repetida_devuelve_la_misma_matricula(api, estudiante, cupos_stub):
    """Reintentar con la misma Idempotency-Key no crea otra matrícula ni consume otro cupo."""
    antes = _libres(cupos_stub)
    clave = {"Idempotency-Key": str(uuid.uuid4())}
    cuerpo = {"student_id": estudiante["id"], "curso_id": CURSO}
    primera = api.post("/v1/enrollments", json=cuerpo, headers=clave)
    assert primera.status_code == 201
    assert "idempotent-replayed" not in primera.headers
    for _ in range(3):
        r = api.post("/v1/enrollments", json=cuerpo, headers=clave)
        assert r.status_code == 201
        assert r.json()["id"] == primera.json()["id"]
        assert r.headers["idempotent-replayed"] == "true"
    assert _libres(cupos_stub) == antes - 1
    api.post(f"/v1/enrollments/{primera.json()['id']}/revert")


def test_idempotency_key_reutilizada_con_otro_curso_da_422(api, estudiante, cupos_stub):
    clave = {"Idempotency-Key": str(uuid.uuid4())}
    primera = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": CURSO}, headers=clave)
    assert primera.status_code == 201
    r = api.post("/v1/enrollments", json={"student_id": estudiante["id"], "curso_id": 2}, headers=clave)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    api.post(f"/v1/enrollments/{primera.json()['id']}/revert")


def test_mismo_idempotency_key_simultanea_consume_un_cupo(api, estudiante, cupos_stub):
    """El mismo intento enviado en paralelo (doble clic) con la misma clave: un solo cupo."""
    antes = _libres(cupos_stub)
    clave = {"Idempotency-Key": str(uuid.uuid4())}
    cuerpo = {"student_id": estudiante["id"], "curso_id": CURSO}
    respuestas = _paralelo(6, lambda: api.post("/v1/enrollments", json=cuerpo, headers=clave))
    assert all(r.status_code in (201, 409) for r in respuestas), [r.status_code for r in respuestas]
    assert any(r.status_code == 201 for r in respuestas)
    assert _libres(cupos_stub) == antes - 1
    ganadora = next(r for r in respuestas if r.status_code == 201).json()
    api.post(f"/v1/enrollments/{ganadora['id']}/revert")


def _cupos_libres_actuales(cupos_stub, curso_id: int) -> int:
    """Capacidad de partida de la prueba = los cupos libres que tiene el curso ahora.

    Antes la prueba "restablecía" el curso liberando con claves inventadas. Eso ya no es posible
    (a propósito): Cupos solo libera lo que una clave ocupó, que es justo lo que impide la
    sobreventa. La prueba parte de lo que haya y exige que no se supere ESA capacidad."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    return stub.obtenerCupo(pb2.ObtenerRequest(curso_id=curso_id)).cupos_libres


def test_concurrencia_estudiantes_distintos_no_sobrepasa_el_cupo(api, cupos_stub):
    """6 estudiantes distintos compiten por el curso de capacidad baja: solo obtienen
    matrícula tantos como cupos tiene el curso, el resto recibe 409."""
    from tests.conftest import CURSO_CAPACIDAD_BAJA, rut_valido
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    capacidad = _cupos_libres_actuales(cupos_stub, CURSO_CAPACIDAD_BAJA)
    assert capacidad >= 1
    libres = lambda: stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CAPACIDAD_BAJA)).cupos_libres

    estudiantes = []
    for _ in range(capacidad + 4):
        sufijo = uuid.uuid4().hex[:8]
        r = api.post("/v1/students", json={"rut": rut_valido(), "nombre": "C", "apellido": "T",
                                           "email": f"c{sufijo}@example.com"})
        estudiantes.append(r.json()["id"])
    respuestas = []
    try:
        with ThreadPoolExecutor(max_workers=len(estudiantes)) as pool:
            futuros = [pool.submit(api.post, "/v1/enrollments",
                                   json={"student_id": s, "curso_id": CURSO_CAPACIDAD_BAJA}) for s in estudiantes]
            respuestas = [f.result() for f in futuros]
        codigos = [r.status_code for r in respuestas]
        assert codigos.count(201) == capacidad and codigos.count(409) == 4, codigos
        assert libres() == 0
    finally:  # aunque falle una aserción, no se deja el curso lleno para la siguiente corrida
        for r in respuestas:
            if r.status_code == 201:
                api.post(f"/v1/enrollments/{r.json()['id']}/revert")
    assert libres() == capacidad
