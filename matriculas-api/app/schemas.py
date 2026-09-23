from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import EstadoMatricula

# ---------------------------------------------------------------------------
# Estudiantes
# ---------------------------------------------------------------------------


class StudentCreate(BaseModel):
    rut: str = Field(..., min_length=3, max_length=12, examples=["19011022-3"])
    nombre: str = Field(..., min_length=1, max_length=150, examples=["Ana"])
    apellido: str = Field(..., min_length=1, max_length=150, examples=["Pérez"])
    email: EmailStr = Field(..., examples=["ana.perez@example.com"])
    telefono: str | None = Field(None, max_length=20, examples=["+56912345678"])


class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rut: str
    nombre: str
    apellido: str
    email: EmailStr
    telefono: str | None
    created_at: datetime
    updated_at: datetime


class StudentPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[StudentOut]


# ---------------------------------------------------------------------------
# Matrículas
# ---------------------------------------------------------------------------


class EnrollmentCreate(BaseModel):
    student_id: str = Field(..., description="Id del estudiante (UUID)")
    curso_id: int = Field(
        ..., ge=1, description="Id del curso en el sistema Cupos", examples=[5]
    )


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    student_id: str
    curso_id: int
    estado: EstadoMatricula
    motivo: str | None
    created_at: datetime
    updated_at: datetime


class EnrollmentPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EnrollmentOut]


# ---------------------------------------------------------------------------
# Errores (envoltorio JSON uniforme para toda la API)
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str = Field(..., examples=["SIN_CUPOS"])
    message: str = Field(..., examples=["El curso 5 no tiene cupos disponibles"])
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
