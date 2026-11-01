"""
Conciliador de matrículas PENDIENTES.

Problema que resuelve: cuando Cupos no responde a tiempo (timeout) o se cae a
mitad de la llamada, la API no sabe si el cupo quedó ocupado. Antes ese cupo
quedaba retenido sin ninguna matrícula asociada y nadie lo devolvía.

Estrategia: una matrícula PENDIENTE más antigua que `pending_ttl_seconds` se
cierra así, usando la misma clave de idempotencia con la que se intentó:

  1. consultarOperacion(clave): RPC de SOLO LECTURA que dice qué ocurrió con esa clave en
     Cupos. Antes se hacía ocupar+liberar, que retenía un cupo real unos milisegundos (un
     estudiante legítimo podía ver SIN_CUPOS justo en ese instante) y volvía a mutar Cupos
     para averiguar algo que solo había que leer.
  2. Si la clave ocupó un cupo y no lo liberó → liberarCupo(clave) (idempotente; Cupos solo
     libera lo que esa clave ocupó).
  3. Si la clave nunca ocupó nada → no hay nada que devolver. Es seguro porque Cupos no confirma
     operaciones cuyo deadline ya venció (ver cupos/src/server.py) y el TTL es muy superior al
     deadline: una llamada de hace > TTL ya no puede completarse.
  4. La matrícula pasa a ANULADA con un motivo (queda auditada).

Si Cupos sigue caído, no se cambia nada y se reintenta en el siguiente ciclo.

Con varias réplicas de la API corre un conciliador por réplica: en PostgreSQL se toma un candado
consultivo por ciclo para que solo uno trabaje a la vez.

Se anula en vez de confirmar a propósito: al cliente se le dijo "resultado
incierto, reintenta"; si nunca reintentó no se le debe matricular en silencio
horas después. Si reintenta dentro de la ventana (la mitad del TTL), con la
misma Idempotency-Key, la matrícula se confirma con el resultado real.
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import select, text, update

from . import models
from .config import settings
from .database import SessionLocal
from .errors import ApiError
from .grpc_client import cupos_client
from .time import utc_now

log = logging.getLogger("matriculas.reconciler")

MOTIVO = "Caducó sin confirmación de Cupos (conciliación automática)"


LOCK_CONCILIADOR = 727002  # clave del candado consultivo de PostgreSQL


def conciliar_pendientes(db) -> dict:
    """Cierra las matrículas PENDIENTES caducadas. Devuelve un resumen."""
    resumen = {"cerradas": 0, "pendientes": 0}
    es_pg = db.get_bind().dialect.name == "postgresql"
    if es_pg and not db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_CONCILIADOR}).scalar():
        return resumen  # otra réplica está conciliando
    try:
        corte = utc_now() - timedelta(seconds=settings.pending_ttl_seconds)
        caducadas = db.execute(
            select(models.Enrollment.id, models.Enrollment.curso_id, models.Enrollment.idempotency_key)
            .where(models.Enrollment.estado == models.EstadoMatricula.PENDIENTE,
                   models.Enrollment.updated_at < corte)
            .limit(50)
        ).all()

        for enrollment_id, curso_id, clave in caducadas:
            try:
                estado_ocupar, estado_liberar = cupos_client.consultar_operacion(clave)  # solo lectura
                if estado_ocupar == "CUPO_OCUPADO" and estado_liberar != "CUPO_LIBERADO":
                    cupos_client.liberar_cupo(curso_id, clave)  # devuelve el cupo (idempotente)
            except ApiError as exc:
                log.warning("conciliación de %s pospuesta: %s", enrollment_id, exc.code)
                resumen["pendientes"] += 1
                continue

            db.execute(
                update(models.Enrollment)
                .where(models.Enrollment.id == enrollment_id,
                       models.Enrollment.estado == models.EstadoMatricula.PENDIENTE)
                .values(estado=models.EstadoMatricula.ANULADA, motivo=MOTIVO, updated_at=utc_now())
            )
            db.commit()
            resumen["cerradas"] += 1
            log.info("matrícula %s cerrada por el conciliador (cupo no retenido)", enrollment_id)
        return resumen
    finally:
        if es_pg:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_CONCILIADOR})
            db.commit()


class Conciliador(threading.Thread):
    """Hilo en segundo plano que ejecuta `conciliar_pendientes` periódicamente."""

    def __init__(self, intervalo_s: float):
        super().__init__(name="conciliador", daemon=True)
        self._intervalo = intervalo_s
        self._detener = threading.Event()

    def run(self) -> None:
        while not self._detener.wait(self._intervalo):
            db = SessionLocal()
            try:
                conciliar_pendientes(db)
            except Exception:  # el hilo nunca debe morir por un error puntual
                log.exception("falló un ciclo de conciliación")
                db.rollback()
            finally:
                db.close()

    def detener(self) -> None:
        self._detener.set()
