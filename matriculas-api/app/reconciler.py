"""
Conciliador de matrículas PENDIENTES.

Problema que resuelve: cuando Cupos no responde a tiempo (timeout) o se cae a
mitad de la llamada, la API no sabe si el cupo quedó ocupado. Antes ese cupo
quedaba retenido sin ninguna matrícula asociada y nadie lo devolvía.

Estrategia: una matrícula PENDIENTE más antigua que `pending_ttl_seconds` se
cierra así, usando la misma clave de idempotencia con la que se intentó:

  1. ocuparCupo(clave): por idempotencia devuelve lo que pasó la primera vez,
     o, si nunca se procesó, lo procesa ahora. Así se conoce con certeza el
     estado real sin adivinar.
  2. Si quedó un cupo retenido (CUPO_OCUPADO) → liberarCupo(clave), que también
     es idempotente (se libera una sola vez aunque se repita).
  3. La matrícula pasa a ANULADA con un motivo (queda auditada).

Si Cupos sigue caído, no se cambia nada y se reintenta en el siguiente ciclo.

Se anula en vez de confirmar a propósito: al cliente se le dijo "resultado
incierto, reintenta"; si nunca reintentó no se le debe matricular en silencio
horas después. Si reintenta dentro de la ventana (la mitad del TTL), con la
misma Idempotency-Key, la matrícula se confirma con el resultado real.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select, update

from . import models
from .config import settings
from .database import SessionLocal
from .errors import ApiError, CursoNoEncontradoError, SinCuposError
from .grpc_client import cupos_client

log = logging.getLogger("matriculas.reconciler")

MOTIVO = "Caducó sin confirmación de Cupos (conciliación automática)"


def conciliar_pendientes(db) -> dict:
    """Cierra las matrículas PENDIENTES caducadas. Devuelve un resumen."""
    corte = datetime.utcnow() - timedelta(seconds=settings.pending_ttl_seconds)
    caducadas = db.execute(
        select(models.Enrollment.id, models.Enrollment.curso_id, models.Enrollment.idempotency_key)
        .where(models.Enrollment.estado == models.EstadoMatricula.PENDIENTE,
               models.Enrollment.updated_at < corte)
        .limit(50)
    ).all()

    resumen = {"cerradas": 0, "pendientes": 0}
    for enrollment_id, curso_id, clave in caducadas:
        try:
            try:
                cupos_client.ocupar_cupo(curso_id, clave)   # estado real, de forma idempotente
                retenido = True
            except (SinCuposError, CursoNoEncontradoError):
                retenido = False                            # Cupos no retuvo nada
            if retenido:
                cupos_client.liberar_cupo(curso_id, clave)  # devuelve el cupo (idempotente)
        except ApiError as exc:
            log.warning("conciliación de %s pospuesta: %s", enrollment_id, exc.code)
            resumen["pendientes"] += 1
            continue

        db.execute(
            update(models.Enrollment)
            .where(models.Enrollment.id == enrollment_id,
                   models.Enrollment.estado == models.EstadoMatricula.PENDIENTE)
            .values(estado=models.EstadoMatricula.ANULADA, motivo=MOTIVO, updated_at=datetime.utcnow())
        )
        db.commit()
        resumen["cerradas"] += 1
        log.info("matrícula %s cerrada por el conciliador (cupo no retenido)", enrollment_id)
    return resumen


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
