from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..errors import ConflictError, NotFoundError
from ..grpc_client import cupos_client
from ..security import require_api_key

router = APIRouter(
    prefix="/v1/enrollments",
    tags=["Matrículas"],
    dependencies=[Depends(require_api_key)],
)


def _estudiante_o_404(db: Session, student_id: str) -> models.Student:
    estudiante = db.get(models.Student, student_id)
    if estudiante is None:
        raise NotFoundError(f"No existe un estudiante con id {student_id}")
    return estudiante


def _matricula_o_404(db: Session, enrollment_id: str) -> models.Enrollment:
    matricula = db.get(models.Enrollment, enrollment_id)
    if matricula is None:
        raise NotFoundError(f"No existe una matrícula con id {enrollment_id}")
    return matricula


@router.post(
    "",
    response_model=schemas.EnrollmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Matricular a un estudiante en un curso",
    description=(
        "Registra la matrícula solo si el curso tiene cupos disponibles según "
        "el servicio de Cupos (verificado en tiempo real vía gRPC, de forma "
        "atómica: se consulta y se reserva el cupo en una sola llamada)."
    ),
    responses={
        404: {"model": schemas.ErrorResponse, "description": "Estudiante o curso no existen"},
        409: {"model": schemas.ErrorResponse, "description": "Ya matriculado o sin cupos"},
        503: {"model": schemas.ErrorResponse, "description": "Cupos no disponible"},
    },
)
def matricular(payload: schemas.EnrollmentCreate, db: Session = Depends(get_db)):
    _estudiante_o_404(db, payload.student_id)

    ya_matriculado = db.execute(
        select(models.Enrollment).where(
            models.Enrollment.student_id == payload.student_id,
            models.Enrollment.curso_id == payload.curso_id,
            models.Enrollment.estado == models.EstadoMatricula.CONFIRMADA,
        )
    ).scalar_one_or_none()
    if ya_matriculado is not None:
        raise ConflictError(
            "El estudiante ya está matriculado (y con cupo confirmado) en ese curso",
            code="ALREADY_ENROLLED",
        )

    matricula = models.Enrollment(
        student_id=payload.student_id,
        curso_id=payload.curso_id,
        estado=models.EstadoMatricula.CONFIRMADA,
    )

    # 1) Reservar el cupo en Cupos ANTES de confirmar en la base local.
    #    Si esto falla (sin cupos, curso inexistente, Cupos caído), no se
    #    persiste ninguna matrícula: se cumple el requisito de que la
    #    matrícula "solo puede registrarse si el curso tiene cupos
    #    disponibles según el sistema de Cupos".
    cupos_client.ocupar_cupo(payload.curso_id, idempotency_key=matricula.idempotency_key)

    # 2) El cupo ya quedó reservado en Cupos: persistir la matrícula local.
    db.add(matricula)
    db.commit()
    db.refresh(matricula)
    return matricula


@router.post(
    "/{enrollment_id}/revert",
    response_model=schemas.EnrollmentOut,
    summary="Revertir (anular) una matrícula y liberar el cupo",
    responses={
        404: {"model": schemas.ErrorResponse},
        409: {"model": schemas.ErrorResponse, "description": "La matrícula ya estaba anulada"},
        503: {"model": schemas.ErrorResponse, "description": "Cupos no disponible"},
    },
)
def revertir_matricula(enrollment_id: str, db: Session = Depends(get_db)):
    matricula = _matricula_o_404(db, enrollment_id)
    if matricula.estado == models.EstadoMatricula.ANULADA:
        raise ConflictError("La matrícula ya estaba anulada", code="ALREADY_REVERTED")

    # Libera el cupo en Cupos usando la MISMA idempotency_key con la que se
    # ocupó: si este endpoint se reintenta (ej. el cliente no vio la
    # respuesta por un corte de red), Cupos no libera el cupo dos veces.
    cupos_client.liberar_cupo(matricula.curso_id, idempotency_key=matricula.idempotency_key)

    matricula.estado = models.EstadoMatricula.ANULADA
    db.commit()
    db.refresh(matricula)
    return matricula


@router.get(
    "/{enrollment_id}",
    response_model=schemas.EnrollmentOut,
    summary="Consultar una matrícula por id",
    responses={404: {"model": schemas.ErrorResponse}},
)
def obtener_matricula(enrollment_id: str, db: Session = Depends(get_db)):
    return _matricula_o_404(db, enrollment_id)


@router.get(
    "",
    response_model=schemas.EnrollmentPage,
    summary="Listar matrículas",
)
def listar_matriculas(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    student_id: str | None = Query(None),
    curso_id: int | None = Query(None),
    estado: models.EstadoMatricula | None = Query(None),
):
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
