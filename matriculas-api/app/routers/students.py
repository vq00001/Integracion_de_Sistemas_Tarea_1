from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..errors import ConflictError, NotFoundError
from ..rut import normalizar_rut
from ..security import (
    Principal,
    exigir_acceso_a_estudiante,
    generar_api_key,
    hash_api_key,
    require_admin,
    require_api_key,
)

router = APIRouter(
    prefix="/v1/students",
    tags=["Estudiantes"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    response_model=schemas.StudentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un estudiante (solo administrador)",
    responses={
        401: {"model": schemas.ErrorResponse, "description": "Falta la API key o es inválida"},
        403: {"model": schemas.ErrorResponse, "description": "Requiere API key de administrador"},
        409: {"model": schemas.ErrorResponse, "description": "RUT o email ya registrados"},
        422: {"model": schemas.ErrorResponse, "description": "Datos inválidos (p. ej. RUT con dígito verificador incorrecto)"},
    },
)
def crear_estudiante(
    payload: schemas.StudentCreate,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
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
    description="Un administrador puede consultar a cualquiera; un estudiante solo a sí mismo.",
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse, "description": "El estudiante intenta ver a otro"},
        404: {"model": schemas.ErrorResponse},
    },
)
def obtener_estudiante(
    student_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
):
    exigir_acceso_a_estudiante(principal, student_id)
    estudiante = db.get(models.Student, student_id)
    if estudiante is None:
        raise NotFoundError(f"No existe un estudiante con id {student_id}")
    return estudiante


@router.get(
    "",
    response_model=schemas.StudentPage,
    summary="Listar estudiantes (solo administrador)",
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse, "description": "Requiere API key de administrador"},
    },
)
def listar_estudiantes(
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    rut: str | None = Query(None, description="Filtrar por RUT exacto"),
):
    stmt = select(models.Student)
    if rut:
        try:
            rut = normalizar_rut(rut)  # el RUT se guarda normalizado: buscar "12.345.678-5" también funciona
        except ValueError:
            pass  # valor inválido: simplemente no coincidirá con ningún estudiante
        stmt = stmt.where(models.Student.rut == rut)
    total_count = len(db.execute(stmt).all())
    items = (
        db.execute(stmt.order_by(models.Student.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return schemas.StudentPage(total=total_count, limit=limit, offset=offset, items=items)


# ---------------------------------------------------------------------------
# API key personal del estudiante (emitida a demanda)
# ---------------------------------------------------------------------------

def _estudiante_o_404(db: Session, student_id: str) -> models.Student:
    estudiante = db.get(models.Student, student_id)
    if estudiante is None:
        raise NotFoundError(f"No existe un estudiante con id {student_id}")
    return estudiante


def _clave_activa(db: Session, student_id: str) -> models.ApiKey | None:
    return db.execute(
        select(models.ApiKey).where(
            models.ApiKey.student_id == student_id, models.ApiKey.revoked_at.is_(None)
        )
    ).scalar_one_or_none()


@router.post(
    "/{student_id}/api-key",
    response_model=schemas.ApiKeyIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Emitir la API key personal de un estudiante (solo administrador)",
    description=(
        "Crea la API key del estudiante. Con ella (cabecera `X-API-Key`) el estudiante puede "
        "operar **solo sobre sus propios datos**: consultar su ficha y matricularse, revertir y "
        "consultar sus matrículas. El valor completo se devuelve **una única vez**; en la base "
        "solo queda su hash. Hay una clave activa por estudiante: para rotarla, primero "
        "revócala (`DELETE`) y emite otra."
    ),
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse, "description": "Requiere API key de administrador"},
        404: {"model": schemas.ErrorResponse, "description": "El estudiante no existe"},
        409: {"model": schemas.ErrorResponse, "description": "El estudiante ya tiene una clave activa"},
    },
)
def emitir_api_key(
    student_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    _estudiante_o_404(db, student_id)
    clave = generar_api_key()
    fila = models.ApiKey(student_id=student_id, key_hash=hash_api_key(clave), key_prefix=clave[:8])
    db.add(fila)
    try:
        db.commit()
    except IntegrityError as exc:  # índice único parcial: ya hay una clave activa
        db.rollback()
        raise ConflictError(
            "El estudiante ya tiene una API key activa. Revócala antes de emitir otra.",
            code="API_KEY_ALREADY_EXISTS",
        ) from exc
    db.refresh(fila)
    return schemas.ApiKeyIssued(
        student_id=student_id, key_prefix=fila.key_prefix, created_at=fila.created_at, api_key=clave
    )


@router.get(
    "/{student_id}/api-key",
    response_model=schemas.ApiKeyInfo,
    summary="Consultar los metadatos de la API key activa de un estudiante",
    description="Nunca devuelve el secreto. Un administrador ve la de cualquiera; un estudiante, la suya.",
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse},
        404: {"model": schemas.ErrorResponse, "description": "El estudiante no existe o no tiene clave activa"},
    },
)
def consultar_api_key(
    student_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_api_key),
):
    exigir_acceso_a_estudiante(principal, student_id)
    _estudiante_o_404(db, student_id)
    fila = _clave_activa(db, student_id)
    if fila is None:
        raise NotFoundError("El estudiante no tiene una API key activa")
    return fila


@router.delete(
    "/{student_id}/api-key",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revocar la API key activa de un estudiante (solo administrador)",
    responses={
        401: {"model": schemas.ErrorResponse},
        403: {"model": schemas.ErrorResponse, "description": "Requiere API key de administrador"},
        404: {"model": schemas.ErrorResponse, "description": "El estudiante no existe o no tiene clave activa"},
    },
)
def revocar_api_key(
    student_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    _estudiante_o_404(db, student_id)
    fila = _clave_activa(db, student_id)
    if fila is None:
        raise NotFoundError("El estudiante no tiene una API key activa")
    fila.revoked_at = datetime.utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
