"""
Circuit breaker mínimo, en memoria, thread-safe.

Estados:
- CLOSED: opera normalmente. Cada falla suma al contador; cada éxito lo
  resetea.
- OPEN: se alcanzó el umbral de fallas consecutivas. Se rechazan llamadas
  inmediatamente (sin tocar la red) hasta que pase `recovery_seconds`.
  Esto evita que Matrículas quede "colgada" esperando timeouts de gRPC
  en cada request mientras Cupos está caído, y evita seguir golpeando
  a un servicio que ya sabemos que no responde.
- HALF_OPEN: pasado el tiempo de recuperación, se permite UNA llamada de
  prueba. Si tiene éxito, el circuito vuelve a CLOSED; si falla, vuelve a
  OPEN y se reinicia el temporizador.
"""
from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Se lanza cuando el circuito está abierto y se rechaza la llamada sin intentarla."""


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: float):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False

    def allow_request(self) -> bool:
        """¿Se puede intentar la llamada real, o hay que fallar rápido?"""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.OPEN:
                return False
            # HALF_OPEN: solo deja pasar una llamada de prueba a la vez
            if self._half_open_probe_in_flight:
                return False
            self._half_open_probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._half_open_probe_in_flight = False
            if self._state is CircuitState.HALF_OPEN:
                self._open()
                return
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._failures = 0
