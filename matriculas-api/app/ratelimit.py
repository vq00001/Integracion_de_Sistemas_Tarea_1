"""Limitador de ventana deslizante, en memoria y thread-safe.

Se usa para dos cosas (ver security.py y routers/enrollments.py):
  - frenar la fuerza bruta contra X-API-Key (intentos FALLIDOS por IP del cliente);
  - frenar a un estudiante con clave propia que matricula/revierte en bucle.

Limitaciones asumidas (declaradas en docs/adr/D5):
  - El estado vive en el proceso: con varias réplicas el límite efectivo es N × límite
    (para un límite global habría que usar Redis o el balanceador).
  - La IP es la del socket (`request.client`). Detrás de un proxy hay que configurar el proxy
    (uvicorn --proxy-headers --forwarded-allow-ips) para que sea la del cliente real; sin eso
    todos los clientes compartirían una IP. No se confía en X-Forwarded-For por defecto
    porque cualquier cliente podría falsificarlo para evadir el límite.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class LimitadorVentana:
    def __init__(self, limite: int, ventana_s: float):
        self.limite = limite
        self.ventana_s = ventana_s
        self._eventos: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _purgar(self, clave: str, ahora: float) -> deque[float]:
        q = self._eventos[clave]
        while q and ahora - q[0] > self.ventana_s:
            q.popleft()
        if not q:
            self._eventos.pop(clave, None)
            return deque()
        return q

    def excedido(self, clave: str) -> float | None:
        """Segundos que faltan para poder reintentar si `clave` ya alcanzó el límite; si no, None."""
        if self.limite <= 0:
            return None
        ahora = time.monotonic()
        with self._lock:
            q = self._purgar(clave, ahora)
            if len(q) >= self.limite:
                return max(0.0, self.ventana_s - (ahora - q[0]))
        return None

    def registrar(self, clave: str) -> None:
        if self.limite <= 0:
            return
        ahora = time.monotonic()
        with self._lock:
            self._purgar(clave, ahora)
            self._eventos[clave].append(ahora)

    def consumir(self, clave: str) -> float | None:
        """Registra un evento si hay cupo y devuelve None; si no, devuelve la espera en segundos."""
        espera = self.excedido(clave)
        if espera is None:
            self.registrar(clave)
        return espera

    def reiniciar(self) -> None:
        with self._lock:
            self._eventos.clear()
