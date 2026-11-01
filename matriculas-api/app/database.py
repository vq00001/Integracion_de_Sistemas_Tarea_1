from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    # necesario para SQLite + FastAPI (múltiples hilos)
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    """Dependency de FastAPI: entrega una sesión y la cierra siempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _columnas(tabla: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def _migrar_esquema() -> None:
    """Actualiza una base creada con una versión anterior del esquema.

    `create_all` solo crea tablas que no existen; no modifica las existentes ni les agrega
    índices. Las sentencias son idempotentes (se pueden correr en cada arranque).

    Es una migración MANUAL y acotada, no un sistema de migraciones: para un esquema que siga
    creciendo corresponde Alembic (con un solo proceso que migre antes de levantar las réplicas).
    Para evitar que dos réplicas migren a la vez, en PostgreSQL se toma un candado consultivo.
    """
    tablas = set(inspect(engine).get_table_names())
    if not tablas & {"enrollments", "students", "api_keys"}:
        return  # base nueva: create_all lo crea todo
    es_pg = engine.dialect.name == "postgresql"
    with engine.begin() as con:
        if es_pg:
            con.execute(text("SELECT pg_advisory_xact_lock(727001)"))  # serializa réplicas
        if "enrollments" in tablas:
            if es_pg:
                con.execute(text("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS uq_student_curso_estado_activo"))
                con.execute(text("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS ck_estado_valido"))
                con.execute(text(
                    "ALTER TABLE enrollments ADD CONSTRAINT ck_estado_valido "
                    "CHECK (estado IN ('PENDIENTE','CONFIRMADA','RECHAZADA','ANULADA'))"
                ))
                con.execute(text("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS client_key VARCHAR(128)"))
            elif "client_key" not in _columnas("enrollments"):
                con.execute(text("ALTER TABLE enrollments ADD COLUMN client_key VARCHAR(128)"))
            con.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_matricula_activa ON enrollments (student_id, curso_id) "
                "WHERE estado IN ('PENDIENTE','CONFIRMADA')"
            ))
            # La clave de idempotencia pasa de ser global a ser única por estudiante.
            con.execute(text("DROP INDEX IF EXISTS uq_matricula_client_key"))
            con.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_matricula_student_client_key "
                "ON enrollments (student_id, client_key)"
            ))
        if "students" in tablas and "anonimizado_en" not in _columnas("students"):
            con.execute(text("ALTER TABLE students ADD COLUMN anonimizado_en TIMESTAMP"))
        if "api_keys" in tablas and "expires_at" not in _columnas("api_keys"):
            con.execute(text("ALTER TABLE api_keys ADD COLUMN expires_at TIMESTAMP"))
    # Índice de correo sin distinguir mayúsculas: se intenta aparte porque falla si la base
    # ya tiene correos que solo difieren en mayúsculas (hay que resolverlos a mano).
    if "students" in tablas:
        try:
            with engine.begin() as con:
                con.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_student_email_lower ON students (lower(email))"))
        except Exception:  # noqa: BLE001
            logging.getLogger("matriculas.database").warning(
                "No se pudo crear uq_student_email_lower: hay correos duplicados salvo por mayúsculas. "
                "Normalízalos (minúsculas) y reinicia."
            )


def init_db() -> None:
    # Importa los modelos para que queden registrados en Base.metadata
    from . import models  # noqa: F401

    _migrar_esquema()
    # Crea tablas faltantes e índices nuevos
    Base.metadata.create_all(bind=engine)
