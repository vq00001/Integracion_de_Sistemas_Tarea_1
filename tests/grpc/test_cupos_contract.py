"""
Test suite para el contrato gRPC de cupos.
Verifica que la API gRPC de cupos cumpla con el contrato esperado, incluyendo:
- la existencia de los métodos y sus mensajes
- la semántica de los métodos (ocupación, liberación, idempotencia)
No se prueba la API REST ni la integración con el resto del sistema.
El contrato gRPC de cupos es el siguiente:
- obtenerCupo(curso_id) -> cupos_libres
- listarCursos() -> cursos[]
- listarCursosDisponibles() -> cursos[] (excluye cursos llenos)
- ocuparCupo(curso_id, idempotency_key) -> estado (CUPO_OCUPADO | SIN_CUPOS)
- liberarCupo(curso_id, idempotency_key) -> estado (CUPO_LIBERADO | FAILED_PRECONDITION).
  Solo libera lo que ESA clave ocupó (invariante de capacidad protegido por Cupos).
- consultarOperacion(idempotency_key) -> qué ocupó / liberó esa clave (solo lectura)
- idempotencia:
  - ocuparCupo con la misma idempotency_key no debe descontar más de un cupo
  - liberarCupo con la misma idempotency_key no debe devolver error ni descontar más de un cupo
  - ocuparCupo y liberarCupo con la misma idempotency_key no deben interferir entre sí.
"""
import uuid

import pytest

from tests.conftest import CURSO_CAPACIDAD_BAJA, CURSO_CON_CUPO

pytestmark = pytest.mark.grpc


def test_obtener_cupo(cupos_stub):
    """Verifica que obtenerCupo responde con un número de cupos libres válido."""
    pb2 = cupos_stub.pb2
    r = cupos_stub.stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO))
    assert r.cupos_libres >= 0


def test_obtener_cupo_inexistente_da_not_found(cupos_stub):
    """Confirma que consultar un curso inexistente devuelve NOT_FOUND."""
    with pytest.raises(cupos_stub.grpc.RpcError) as e:
        cupos_stub.stub.obtenerCupo(cupos_stub.pb2.ObtenerRequest(curso_id=999999))
    assert e.value.code() == cupos_stub.grpc.StatusCode.NOT_FOUND


def test_listar_cursos_incluye_la_semilla(cupos_stub):
    """Comprueba que la lista inicial de cursos incluye la semilla esperada."""
    ids = {c.curso_id for c in cupos_stub.stub.listarCursos(cupos_stub.pb2.ListarRequest()).cursos}
    assert {1, 2, 3, 4, 5} <= ids


def test_listar_cursos_disponibles_excluye_un_curso_lleno(cupos_stub, curso_lleno):
    """Valida que listarCursosDisponibles omite los cursos que ya no tienen cupos."""
    disponibles = {c.curso_id for c in cupos_stub.stub.listarCursosDisponibles(cupos_stub.pb2.ListarRequest()).cursos}
    assert curso_lleno not in disponibles


def test_ocupar_curso_lleno_devuelve_sin_cupos(cupos_stub, curso_lleno):
    """Prueba que una reserva sobre un curso sin cupos responde SIN_CUPOS."""
    pb2 = cupos_stub.pb2
    r = cupos_stub.stub.ocuparCupo(pb2.OcuparRequest(curso_id=curso_lleno, idempotency_key=uuid.uuid4().hex))
    assert r.estado == pb2.SIN_CUPOS


def test_ocupar_y_liberar_mueve_cupos_libres(cupos_stub):
    """Verifica que ocupar y liberar un cupo modifica correctamente el total disponible."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    antes = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres

    r1_clave = uuid.uuid4().hex
    r1 = stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=r1_clave))
    assert r1.estado == pb2.CUPO_OCUPADO
    assert stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres == antes - 1

    # se libera con la MISMA clave con la que se ocupó
    r2 = stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=r1_clave))
    assert r2.estado == pb2.CUPO_LIBERADO
    assert stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres == antes


def test_ocupar_es_idempotente(cupos_stub):
    """Comprueba que repetir ocuparCupo con la misma clave no descuenta dos veces el mismo cupo."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    clave = uuid.uuid4().hex
    antes = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres
    r1 = stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    r2 = stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    assert r1.estado == r2.estado == pb2.CUPO_OCUPADO
    despues = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres
    assert despues == antes - 1, "se descontó más de un cupo por una sola idempotency_key"
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))

def test_liberar_con_la_misma_clave_de_un_ocupar_previo_no_deberia_crashear(cupos_stub):
    """Reproduce el caso en que la misma clave se usa para ocupar y luego liberar, sin que falle el servidor."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    clave = uuid.uuid4().hex
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))

def test_liberar_dos_veces_con_la_misma_clave_es_idempotente(cupos_stub):
    """Verifica que repetir liberarCupo con la misma clave no debe romper ni duplicar la liberación."""
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    clave = uuid.uuid4().hex
    antes = stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    r1 = stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    r2 = stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    assert r1.estado == r2.estado == pb2.CUPO_LIBERADO
    assert stub.obtenerCupo(pb2.ObtenerRequest(curso_id=CURSO_CON_CUPO)).cupos_libres == antes


# --- Invariante de capacidad: Cupos lo protege por sí mismo (hallazgo 2 de la revisión 3) ---

def _libres(cupos_stub, curso_id):
    return cupos_stub.stub.obtenerCupo(cupos_stub.pb2.ObtenerRequest(curso_id=curso_id)).cupos_libres


def test_liberar_con_clave_inventada_se_rechaza_y_no_sube_los_cupos(cupos_stub):
    """El ataque original: 1 matrícula en un curso de 2 cupos y un liberarCupo con clave nueva
    subía los libres a 2 (sobreventa). Ahora Cupos exige que la clave haya ocupado ese curso."""
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    clave = uuid.uuid4().hex
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    antes = _libres(cupos_stub, CURSO_CON_CUPO)
    with pytest.raises(grpc.RpcError) as e:
        stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key="inventada-" + uuid.uuid4().hex))
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _libres(cupos_stub, CURSO_CON_CUPO) == antes, "una clave que nunca ocupó liberó un cupo"
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))


def test_liberar_una_clave_en_otro_curso_se_rechaza(cupos_stub):
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    clave = uuid.uuid4().hex
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    otro = 2 if CURSO_CON_CUPO != 2 else 3
    with pytest.raises(grpc.RpcError) as e:
        stub.liberarCupo(pb2.LiberarRequest(curso_id=otro, idempotency_key=clave))
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))


def test_no_se_libera_dos_veces_lo_ocupado_una_vez(cupos_stub):
    """Una ocupación se libera UNA vez: no hay otra clave con la que 'volver a liberarla'."""
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    clave = uuid.uuid4().hex
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    libres = _libres(cupos_stub, CURSO_CON_CUPO)
    with pytest.raises(grpc.RpcError) as e:
        stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=uuid.uuid4().hex))
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _libres(cupos_stub, CURSO_CON_CUPO) == libres


def test_liberar_la_ocupacion_rechazada_por_sin_cupos_no_devuelve_nada(cupos_stub, curso_lleno):
    """Una clave cuyo ocupar dio SIN_CUPOS no retuvo nada: liberarla no puede sumar un cupo."""
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    clave = uuid.uuid4().hex
    assert stub.ocuparCupo(pb2.OcuparRequest(curso_id=curso_lleno, idempotency_key=clave)).estado == pb2.SIN_CUPOS
    with pytest.raises(grpc.RpcError) as e:
        stub.liberarCupo(pb2.LiberarRequest(curso_id=curso_lleno, idempotency_key=clave))
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _libres(cupos_stub, curso_lleno) == 0


def test_clave_vacia_se_rechaza(cupos_stub):
    """Sin clave la operación no sería idempotente: se exige (antes se aceptaba en silencio)."""
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    for llamada in (
        lambda: stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key="")),
        lambda: stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key="")),
    ):
        with pytest.raises(grpc.RpcError) as e:
            llamada()
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_misma_clave_con_otro_curso_es_argumento_invalido(cupos_stub):
    """Antes chocaba con la clave primaria y llegaba al cliente como UNKNOWN."""
    pb2, stub, grpc = cupos_stub.pb2, cupos_stub.stub, cupos_stub.grpc
    clave = uuid.uuid4().hex
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    otro = 2 if CURSO_CON_CUPO != 2 else 3
    with pytest.raises(grpc.RpcError) as e:
        stub.ocuparCupo(pb2.OcuparRequest(curso_id=otro, idempotency_key=clave))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))


def test_consultar_operacion_es_de_solo_lectura_y_refleja_la_clave(cupos_stub):
    pb2, stub = cupos_stub.pb2, cupos_stub.stub
    clave = uuid.uuid4().hex
    nada = stub.consultarOperacion(pb2.ConsultarOperacionRequest(idempotency_key=clave))
    assert nada.estado_ocupar == pb2.ESTADO_SIN_DEFINIR and nada.estado_liberar == pb2.ESTADO_SIN_DEFINIR
    libres0 = _libres(cupos_stub, CURSO_CON_CUPO)
    stub.ocuparCupo(pb2.OcuparRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    r = stub.consultarOperacion(pb2.ConsultarOperacionRequest(idempotency_key=clave))
    assert r.estado_ocupar == pb2.CUPO_OCUPADO and r.estado_liberar == pb2.ESTADO_SIN_DEFINIR
    assert r.curso_id == CURSO_CON_CUPO
    # consultar no modifica ningún cupo (a diferencia del ocupar+liberar que usaba el conciliador)
    for _ in range(3):
        stub.consultarOperacion(pb2.ConsultarOperacionRequest(idempotency_key=clave))
    assert _libres(cupos_stub, CURSO_CON_CUPO) == libres0 - 1
    stub.liberarCupo(pb2.LiberarRequest(curso_id=CURSO_CON_CUPO, idempotency_key=clave))
    r = stub.consultarOperacion(pb2.ConsultarOperacionRequest(idempotency_key=clave))
    assert r.estado_liberar == pb2.CUPO_LIBERADO
