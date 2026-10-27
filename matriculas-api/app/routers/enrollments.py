"""
Matrículas: registro, reversión y consulta.

Flujo de POST /v1/enrollments (patrón "intención + confirmación", una saga
de dos pasos con la base local y Cupos):

  1. Se INSERTA la matrícula en estado PENDIENTE con su propia clave de
     idempotencia. Un índice único parcial (student_id, curso_id) sobre los
     estados activos hace que, ante peticiones simultáneas del mismo
     estudiante y curso, solo una inserción gane; las demás reciben 409 SIN
     haber tocado Cupos (antes consumían cupos que quedaban huérfanos).
  2. Se llama a ocuparCupo(curso, clave). Es atómico e idempotente en Cupos.
       - CUPO_OCUPADO  → la matrícula pasa a CONFIRMADA (201).
       - SIN_CUPOS / CURSO_NO_ENCONTRADO → rechazo definitivo: se borra la
         intención y se responde 409/404 (Cupos no retuvo nada).
       - Cupos caído → 503; Cupos lento (timeout) → 504. El resultado es
         INCIERTO (el cupo pudo ocuparse), así que la matrícula queda
         PENDIENTE: el cliente puede reintentar con la misma Idempotency-Key
         y obtiene el resultado real; si no lo hace, el conciliador la anula
         y libera el cupo (ver reconciler.py).
  3. Si falla el guardado local tras ocupar el cupo, se compensa liberando
     el cupo con la misma clave.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..errors import (
    ApiError,
    ConflictError,
    CursoNoEncontradoError,
    DependencyError,
    EnrollmentInProgressError,
    IdempotencyKeyReusedError,
    NotFoundError,
    PersistenceError,
    SinCuposError,
)
from ..grpc_client import cupos_client
from ..security import Principal, exigir_acceso_a_estudiante, require_api_key

log = logging.getLogger("matriculas.enrollments")

router = APIRouter(
    prefix="/v1/enrollments",
    tags=["Matrículas"],
    dependencies=[Depends(require_api_key)],
)

E = models.EstadoMatricula


def _estudiante_o_404(db: Session, student_id: str) -> models.Student:
    estudiante = db.get(models.Student, student_id)
    if estudiante is None:
        raise NotFoundError(f"No existe un estudiante con id {student_id}")
    return estudiante


def _matricula_o_404(db: Session, enrollment_id: str, principal: Principal) -> models.Enrollment:
    """Matrícula por id. Para un estudiante, una matrícula AJENA se trata como inexistente
    (404, no 403): así no se le confirma qué ids de matrícula existen."""
    matricula = db.get(models.Enrollment, enrollment_id)
    if matricula is None or (not principal.es_admin and matricula.student_id != principal.student_id):
        raise NotFoundError(f"No existe una matrícula con id {enrollment_id}")
    return matricula


# ---------------------------------------------------------------------------
# Auxiliares de la saga
# ---------------------------------------------------------------------------

def _borrar_pendiente(db: Session, enrollment_id: str) -> None:
    """Elimina una intención PENDIENTE cuyo rechazo es definitivo."""
    try:
        db.execute(
            delete(models.Enrollment).where(
                models.Enrollment.id == enrollment_id, models.Enrollment.estado == E.PENDIENTE
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.exception("no se pudo borrar la intención %s; la cerrará el conciliador", enrollment_id)


def _compensar(curso_id: int, clave: str) -> None:
    """Devuelve el cupo retenido cuando no se pudo persistir la matrícula."""
    try:
        cupos_client.liberar_cupo(curso_id, clave)
    except ApiError:
        # Queda PENDIENTE en la base: el conciliador liberará el cupo.
        log.exception("falló la compensación del cupo (curso %s); lo resolverá el conciliador", curso_id)


def _confirmar(db: Session, enrollment_id: str, curso_id: int, clave: str) -> models.Enrollment:
    try:
        resultado = db.execute(
            update(models.Enrollment)
            .where(models.Enrollment.id == enrollment_id, models.Enrollment.estado == E.PENDIENTE)
            .values(estado=E.CONFIRMADA, updated_at=datetime.utcnow())
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        log.exception("falló el guardado local de la matrícula %s", enrollment_id)
        _compensar(curso_id, clave)
        raise PersistenceError(
            "No se pudo guardar la matrícula. El cupo fue devuelto; reintenta."
        ) from exc
    matricula = db.get(models.Enrollment, enrollment_id)
    if resultado.rowcount == 0 and (matricula is None or matricula.estado != E.CONFIRMADA):
        raise ConflictError(
            "La matrícula pendiente ya no estaba vigente (caducó y fue anulada). "
            "Inicia un intento nuevo con otra Idempotency-Key.",
            code="ENROLLMENT_EXPIRED",
        )
    return matricula


def _completar(db: Session, matricula: models.Enrollment, *, primer_intento: bool) -> models.Enrollment:
    """Paso 2 de la saga: ocupar el cupo y confirmar (o rechazar/dejar pendiente)."""
    # Se copian los valores antes de llamar a Cupos: tras un commit/rollback
    # el objeto ORM queda expirado y no conviene depender de él.
    enrollment_id, curso_id, clave = matricula.id, matricula.curso_id, matricula.idempotency_key
    try:
        cupos_client.ocupar_cupo(curso_id, clave)
    except (SinCuposError, CursoNoEncontradoError):
        _borrar_pendiente(db, enrollment_id)  # Cupos no retuvo nada
        raise
    except DependencyError as exc:
        if primer_intento and not exc.enviado:
            _borrar_pendiente(db, enrollment_id)  # circuito abierto: no se envió nada
        else:
            # Resultado incierto: queda PENDIENTE y se informa su id para consultarla o reintentar
            exc.details = {**(exc.details or {}), "enrollment_id": enrollment_id, "estado": "PENDIENTE"}
        raise
    return _confirmar(db, enrollment_id, curso_id, clave)


def _retomar(db: Session, previa: models.Enrollment, payload: schemas.EnrollmentCreate,
             response: Response) -> models.Enrollment:
    """Reintento con una Idempotency-Key que ya existe."""
    if previa.student_id != payload.student_id or previa.curso_id != payload.curso_id:
        raise IdempotencyKeyReusedError(
            "Esa Idempotency-Key ya se usó con una solicitud distinta (otro estudiante o curso)."
        )
    response.headers["Idempotent-Replayed"] = "true"
    if previa.estado != E.PENDIENTE:
        return previa  # mismo resultado que la primera vez (CONFIRMADA o ya ANULADA)
    edad = (datetime.utcnow() - previa.updated_at).total_seconds()
    if edad > settings.pending_ttl_seconds / 2:
        # Ventana de reintento agotada: de aquí en adelante solo actúa el conciliador.
        raise ConflictError(
            "La matrícula pendiente caducó y será anulada automáticamente. "
            "Inicia un intento nuevo con otra Idempotency-Key.",
            code="ENROLLMENT_EXPIRED",
            details={"enrollment_id": previa.id},
        )
    return _completar(db, previa, primer_intento=False)


def _resolver_conflicto(db: Session, payload: schemas.EnrollmentCreate, client_key: str | None,
                        response: Response) -> models.Enrollment:
    """La inserción chocó con un índice único: otro request ganó la carrera."""
    if client_key:
        previa = db.execute(
            select(models.Enrollment).where(models.Enrollment.client_key == client_key)
        ).scalar_one_or_none()
        if previa is not None:
            return _retomar(db, previa, payload, response)
    activa = db.execute(
        select(models.Enrollment).where(
            models.Enrollment.student_id == payload.student_id,
            models.Enrollment.curso_id == payload.curso_id,
            models.Enrollment.estado.in_([E.PENDIENTE, E.CONFIRMADA]),
        )
    ).scalar_one_or_none()
    if activa is None:
        raise ConflictError("No se pudo registrar la matrícula por un conflicto; reintenta.", code="CONFLICT")
    if activa.estado == E.CONFIRMADA:
        raise ConflictError(
            "El estudiante ya está matriculado (y con cupo confirmado) en ese curso",
            code="ALREADY_ENROLLED",
        )
    raise EnrollmentInProgressError(
        "Ya hay una matrícula en proceso para este estudiante y curso. Reintenta con la "
        "misma Idempotency-Key o espera a que se resuelva.",
        details={"enrollment_id": activa.id},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=schemas.EnrollmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Matricular a un estudiante en un curso",
    description=(
        "Registra la matrícula solo si el curso tiene cupos disponibles según el servicio de "
        "Cupos (verificado en tiempo real vía gRPC; consulta y reserva son una sola llamada "
        "atómica).\n\n"
        "**Idempotencia.** Envía una cabecera `Idempotency-Key` (p. ej. un UUID) para poder "
        "reintentar con seguridad: repetir la solicitud con la misma clave devuelve la misma "
        "matrícula (cabecera `Idempotent-Replayed: true`) y nunca consume un segundo cupo. "
        "Una clave identifica un intento; reutilizarla con otro estudiante o curso da 422.\n\n"
        "**Resultado incierto (504).** Si Cupos no responde a tiempo, el cupo pudo haberse "
        "ocupado. La matrícula queda `PENDIENTE`: reintenta con la misma clave o consulta "
        "`GET /v1/enrollments/{id}`. Si no se confirma dentro del TTL, se anula "
        "automáticamente y se libera el cupo."
    ),
    responses={
        401: {"model": schemas.ErrorResponse, "description": "Falta la API key o es inválida"},
        403: {"model": schemas.ErrorResponse,
              "description": "Una API key de estudiante solo puede matricular a su propio estudiante"},
        404: {"model": schemas.ErrorResponse, "description": "Estudiante o curso no existen"},
        409: {"model": schemas.ErrorResponse,
              "description": "Ya matriculado, matrícula en proceso o sin cupos"},
        422: {"model": schemas.ErrorResponse,
              "description": "Datos inválidos o Idempotency-Key reutilizada con otra solicitud"},
        502: {"model": schemas.ErrorResponse, "description": "Cupos devolvió un error interno"},
        503: {"model": schemas.ErrorResponse, "description": "Cupos no disponible (caído o circuito abierto)"},
        504: {"model": schemas.ErrorResponse,
              "description": "Cupos no respondió a tiempo: resultado incierto, matrícula PENDIENTE"},
    },
)
def matricular(
    payload: schemas.EnrollmentCreate,
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=128,
        description="Identifica este intento para poder reintentarlo sin duplicar el cupo.",
    ),
):
    # Autorización ANTES de consultar la base: un estudiante no debe poder usar esta
    # ruta para averiguar si existe otro estudiante.
    exigir_acceso_a_estudiante(principal, payload.student_id)
    _estudiante_o_404(db, payload.student_id)

    # 1) ¿Es el reintento de un intento anterior?
    if idempotency_key:
        previa = db.execute(
            select(models.Enrollment).where(models.Enrollment.client_key == idempotency_key)
        ).scalar_one_or_none()
        if previa is not None:
            return _retomar(db, previa, payload, response)

    # 2) Registrar la intención. El índice único parcial resuelve las carreras.
    matricula = models.Enrollment(
        student_id=payload.student_id,
        curso_id=payload.curso_id,
        estado=E.PENDIENTE,
        client_key=idempotency_key,
    )
    db.add(matricula)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _resolver_conflicto(db, payload, idempotency_key, response)

    # 3) Ocupar el cupo en Cupos y confirmar.
    return _completar(db, matricula, primer_intento=True)


@router.post(
    "/{enrollment_id}/revert",
    response_model=schemas.EnrollmentOut,
    summary="Revertir (anular) una matrícula y liberar el cupo",
    responses={
        401: {"model": schemas.ErrorResponse},
        404: {"model": schemas.ErrorResponse},
        409: {"model": schemas.ErrorResponse,
              "description": "La matrícula ya estaba anulada o aún está pendiente"},
        502: {"model": schemas.ErrorResponse},
        503: {"model": schemas.ErrorResponse, "description": "Cupos no disponible"},
        504: {"model": schemas.ErrorResponse, "description": "Cupos no respondió a tiempo"},
    },
)
def revertir_matricula(
    enrollment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
):
    matricula = _matricula_o_404(db, enrollment_id, principal)
    if matricula.estado == E.ANULADA:
        raise ConflictError("La matrícula ya estaba anulada", code="ALREADY_REVERTED")
    if matricula.estado != E.CONFIRMADA:
        raise ConflictError(
            "La matrícula aún no está confirmada; no se puede revertir.",
            code="ENROLLMENT_NOT_CONFIRMED",
        )
    curso_id, clave = matricula.curso_id, matricula.idempotency_key

    # Libera con la MISMA clave con la que se ocupó. Es idempotente en Cupos:
    # si este endpoint se reintenta o se invoca en paralelo, el cupo se libera una sola vez.
    cupos_client.liberar_cupo(curso_id, clave)

    # Transición atómica CONFIRMADA → ANULADA: solo una petición "gana"; las demás
    # reciben 409 (el cupo ya fue liberado una única vez).
    resultado = db.execute(
        update(models.Enrollment)
        .where(models.Enrollment.id == enrollment_id, models.Enrollment.estado == E.CONFIRMADA)
        .values(estado=E.ANULADA, updated_at=datetime.utcnow())
    )
    db.commit()
    if resultado.rowcount == 0:
        raise ConflictError("La matrícula ya estaba anulada", code="ALREADY_REVERTED")
    return db.get(models.Enrollment, enrollment_id)


@router.get(
    "/{enrollment_id}",
    response_model=schemas.EnrollmentOut,
    summary="Consultar una matrícula por id",
    responses={401: {"model": schemas.ErrorResponse}, 404: {"model": schemas.ErrorResponse}},
)
def obtener_matricula(
    enrollment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
):
    return _matricula_o_404(db, enrollment_id, principal)


@router.get(
    "",
    response_model=schemas.EnrollmentPage,
    summary="Listar matrículas",
    description=(
        "Un administrador ve todas (con filtros opcionales). Con una API key de estudiante "
        "solo se listan sus propias matrículas."
    ),
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse,
              "description": "Un estudiante filtró por el student_id de otro"},
    },
)
def listar_matriculas(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    student_id: str | None = Query(None),
    curso_id: int | None = Query(None),
    estado: models.EstadoMatricula | None = Query(None),
):
    if not principal.es_admin:
        if student_id:
            exigir_acceso_a_estudiante(principal, student_id)  # 403 si pide a otro
        student_id = principal.student_id  # siempre acotado a sus propias matrículas
    stmt = select(models.Enrollment)
    if student_id:
        stmt = stmt.where(models.Enrollment.student_id == student_id)
    if curso_id is not None:
        stmt = stmt.where(models.Enrollment.curso_id == curso_id)
    if estado is not None:
        stmt = stmt.where(models.Enrollment.estado == estado)

    total_count = len(db.execute(stmt).all())
    items = (
        db.execute(stmt.order_by(models.Enrollment.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return schemas.EnrollmentPage(total=total_count, limit=limit, offset=offset, items=items)
