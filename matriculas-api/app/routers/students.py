from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..errors import ConflictError, NotFoundError
from ..security import require_api_key

router = APIRouter(
    prefix="/v1/students",
    tags=["Estudiantes"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    response_model=schemas.StudentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un estudiante",
    responses={409: {"model": schemas.ErrorResponse, "description": "RUT o email ya registrados"}},
)
def crear_estudiante(payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    estudiante = models.Student(**payload.model_dump())
    db.add(estudiante)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Ya existe un estudiante con ese RUT o email", code="STUDENT_ALREADY_EXISTS"
        ) from exc
    db.refresh(estudiante)
    return estudiante


@router.get(
    "/{student_id}",
    response_model=schemas.StudentOut,
    summary="Consultar un estudiante por id",
    responses={404: {"model": schemas.ErrorResponse}},
)
def obtener_estudiante(student_id: str, db: Session = Depends(get_db)):
    estudiante = db.get(models.Student, student_id)
    if estudiante is None:
        raise NotFoundError(f"No existe un estudiante con id {student_id}")
    return estudiante


@router.get(
    "",
    response_model=schemas.StudentPage,
    summary="Listar estudiantes",
)
def listar_estudiantes(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    rut: str | None = Query(None, description="Filtrar por RUT exacto"),
):
    stmt = select(models.Student)
    if rut:
        stmt = stmt.where(models.Student.rut == rut)
    total_count = len(db.execute(stmt).all())
    items = (
        db.execute(stmt.order_by(models.Student.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return schemas.StudentPage(total=total_count, limit=limit, offset=offset, items=items)
