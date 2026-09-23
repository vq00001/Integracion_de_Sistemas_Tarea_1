from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class EstadoMatricula(str, enum.Enum):
    CONFIRMADA = "CONFIRMADA"   # Cupos confirmó disponibilidad y el cupo quedó ocupado
    RECHAZADA = "RECHAZADA"     # Cupos indicó que no había cupos (o el curso no existe)
    ANULADA = "ANULADA"         # La matrícula fue revertida y el cupo fue liberado


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rut: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    apellido: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    telefono: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    matriculas: Mapped[list["Enrollment"]] = relationship(
        back_populates="estudiante", cascade="all, delete-orphan"
    )


class Enrollment(Base):
    """
    Una matrícula asocia a un estudiante con un curso de Cupos (`curso_id`,
    el identificador que vive en el servicio Cupos vía gRPC). A diferencia
    del estado pre-integración (donde se guardaba un `codigo_curso` de
    texto libre sin validar), aquí `curso_id` siempre corresponde a un
    curso que fue consultado/reservado contra Cupos antes de confirmar la
    matrícula: esa es justamente la integración que implementa esta API.
    """

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "curso_id", "estado",
            name="uq_student_curso_estado_activo",
        ),
        CheckConstraint(
            "estado IN ('CONFIRMADA','RECHAZADA','ANULADA')", name="ck_estado_valido"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    curso_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    estado: Mapped[EstadoMatricula] = mapped_column(
        Enum(EstadoMatricula, native_enum=False, length=20), nullable=False
    )
    motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Clave de idempotencia enviada a Cupos (ocuparCupo / liberarCupo).
    # Se reutiliza el propio id de la matrícula: así, si el proceso se cae
    # después de llamar a Cupos pero antes de confirmar en la BD local,
    # un reintento con el mismo id no duplica la ocupación del cupo.
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    estudiante: Mapped["Student"] = relationship(back_populates="matriculas")
