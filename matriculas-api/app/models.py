from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .time import utc_now


def _uuid() -> str:
    return str(uuid.uuid4())


class EstadoMatricula(str, enum.Enum):
    PENDIENTE = "PENDIENTE"     # Intención registrada; aún no se sabe el resultado en Cupos
    CONFIRMADA = "CONFIRMADA"   # Cupos confirmó disponibilidad y el cupo quedó ocupado
    RECHAZADA = "RECHAZADA"     # Reservado (hoy los rechazos definitivos no se persisten)
    ANULADA = "ANULADA"         # Revertida por el usuario o cerrada por el conciliador; el cupo quedó libre


# Estados "activos": ocupan (o podrían estar ocupando) un cupo del curso.
ESTADOS_ACTIVOS_SQL = "estado IN ('PENDIENTE','CONFIRMADA')"


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        # El correo se guarda en minúsculas (ver schemas.StudentCreate) y además la base lo
        # exige único sin distinguir mayúsculas: Ana@x.com y ana@x.com no pueden coexistir.
        Index("uq_student_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rut: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    apellido: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    telefono: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )
    # Fecha en que se anonimizaron sus datos personales (derecho de supresión). Las matrículas
    # se conservan como historial sin datos personales. Ver DELETE /v1/students/{id}.
    anonimizado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    matriculas: Mapped[list["Enrollment"]] = relationship(
        back_populates="estudiante", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
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
        # Un estudiante no puede tener dos matrículas ACTIVAS (pendiente o
        # confirmada) en el mismo curso. El índice es PARCIAL: las filas
        # ANULADAS no cuentan, así que matricular → revertir → matricular →
        # revertir deja dos filas ANULADAS sin conflicto. (La restricción
        # anterior incluía `estado` en la clave única y bloqueaba la segunda
        # anulación.) Además, la base de datos es quien decide la carrera
        # entre peticiones simultáneas: solo una inserción puede ganar.
        Index(
            "uq_matricula_activa", "student_id", "curso_id", unique=True,
            postgresql_where=text(ESTADOS_ACTIVOS_SQL),
            sqlite_where=text(ESTADOS_ACTIVOS_SQL),
        ),
        # Clave de idempotencia entregada por el cliente (cabecera Idempotency-Key). Es única
        # POR ESTUDIANTE (antes era global): un estudiante ya no puede averiguar si otro usó
        # una clave, ni "quemar" claves ajenas.
        Index("uq_matricula_student_client_key", "student_id", "client_key", unique=True),
        CheckConstraint(
            "estado IN ('PENDIENTE','CONFIRMADA','RECHAZADA','ANULADA')", name="ck_estado_valido"
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
    # Clave de idempotencia enviada a Cupos (ocuparCupo / liberarCupo). Es un
    # UUID interno, opaco y estable para esta matrícula: no contiene datos
    # personales. Como la fila se inserta ANTES de llamar a Cupos, cualquier
    # reintento (del cliente o del conciliador) reutiliza la misma clave y
    # Cupos no ocupa ni libera el cupo dos veces.
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid)
    # Cabecera Idempotency-Key enviada por el cliente de la API REST (opcional).
    # Si el cliente reintenta con la misma clave, recibe la misma matrícula.
    client_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    estudiante: Mapped["Student"] = relationship(back_populates="matriculas")


class ApiKey(Base):
    """API key personal de un estudiante, emitida a demanda por un administrador.

    Solo se guarda el HASH (SHA-256) de la clave: el valor en claro se muestra una
    única vez al emitirla y no se puede recuperar. Como la clave es un token
    aleatorio de 256 bits (no una contraseña elegida por una persona), un hash
    rápido sin sal es suficiente y permite buscarla por índice en cada request.

    Un índice único parcial garantiza UNA clave activa por estudiante; las
    revocadas (`revoked_at` no nulo) quedan como historial.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        Index(
            "uq_api_key_activa_por_estudiante", "student_id", unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Identificador PÚBLICO de la clave (mat_<8 hex aleatorios>): sirve para reconocerla en logs y
    # listados. Es independiente del secreto: no revela ningún carácter de él.
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # Vencimiento (None = no caduca). Una clave vencida deja de autenticar (401).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    estudiante: Mapped["Student"] = relationship(back_populates="api_keys")
