from __future__ import annotations

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


def _migrar_esquema_postgres() -> None:
    """Actualiza una base creada con la versión anterior del esquema.

    `create_all` solo crea tablas que no existen; no modifica las existentes.
    Con el volumen `matriculas_db_data` de una ejecución previa, la tabla
    `enrollments` ya existe con la restricción única antigua
    (student_id, curso_id, estado) y sin la columna `client_key`. Las
    sentencias son idempotentes, por lo que correrlas en cada arranque es seguro.
    En una base nueva no hacen nada relevante.

    (SQLite no permite quitar una restricción de tabla: si usas SQLite en
    desarrollo, borra el archivo `matriculas.db` para recrearlo.)
    """
    if engine.dialect.name != "postgresql":
        return
    if "enrollments" not in inspect(engine).get_table_names():
        return
    with engine.begin() as con:
        con.execute(text("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS uq_student_curso_estado_activo"))
        con.execute(text("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS ck_estado_valido"))
        con.execute(text(
            "ALTER TABLE enrollments ADD CONSTRAINT ck_estado_valido "
            "CHECK (estado IN ('PENDIENTE','CONFIRMADA','RECHAZADA','ANULADA'))"
        ))
        con.execute(text("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS client_key VARCHAR(128)"))
        # create_all no agrega índices a una tabla que ya existía: se crean aquí.
        con.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_matricula_activa ON enrollments (student_id, curso_id) "
            "WHERE estado IN ('PENDIENTE','CONFIRMADA')"
        ))
        con.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_matricula_client_key ON enrollments (client_key)"
        ))


def init_db() -> None:
    # Importa los modelos para que queden registrados en Base.metadata
    from . import models  # noqa: F401

    _migrar_esquema_postgres()
    # Crea tablas faltantes e índices nuevos (uq_matricula_activa, uq_matricula_client_key)
    Base.metadata.create_all(bind=engine)
