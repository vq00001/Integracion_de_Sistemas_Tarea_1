"""Caché en memoria (TTL corto) de la lista de cursos leída de Cupos.

Por qué existe: el enunciado dice que Matrículas consulta la disponibilidad "con muchísima
frecuencia". Cada consulta de un portal es un RPC a Cupos y una lectura a su SQLite; con un TTL
de unos segundos, N consultas por segundo se reducen a ~1/TTL llamadas, sin tocar el camino de
escritura.

Lo que NO hace (declarado en D1/D2):
  - No es la fuente de verdad: `cupos_libres` leído de aquí es REFERENCIAL. Quien decide si hay
    cupo es siempre `POST /v1/enrollments` → `ocuparCupo` (atómico). Un dato cacheado puede decir
    "hay 1 cupo" y la matrícula responder 409 SIN_CUPOS; eso es correcto y esperado.
  - Se INVALIDA al ocupar o liberar un cupo desde esta API, así que tras matricular el listado
    se refresca solo en esta réplica. Con varias réplicas, las demás ven el dato viejo hasta que
    venza el TTL (consistencia eventual acotada por TTL).
  - No es la caché con Redis del requisito opcional O1: es un caché por proceso.

Anti-estampida: si el TTL vence con muchas solicitudes en vuelo, solo una va a Cupos; las demás
esperan ese resultado en vez de lanzar todas el mismo RPC.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class CacheCursos:
    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._datos: list[dict] | None = None
        self._ts = 0.0
        self._lock = threading.Lock()
        self.aciertos = 0
        self.fallos = 0

    def _fresco(self) -> bool:
        return self._datos is not None and (time.monotonic() - self._ts) < self.ttl_s

    def obtener(self, cargar: Callable[[], list[dict]]) -> tuple[list[dict], bool]:
        """Devuelve (cursos, vino_de_cache). `cargar` consulta a Cupos si hace falta."""
        if self.ttl_s <= 0:
            self.fallos += 1
            return cargar(), False
        if self._fresco():
            self.aciertos += 1
            return self._datos, True  # type: ignore[return-value]
        with self._lock:
            if self._fresco():          # otra solicitud ya refrescó mientras esperábamos
                self.aciertos += 1
                return self._datos, True  # type: ignore[return-value]
            datos = cargar()
            self._datos, self._ts = datos, time.monotonic()
            self.fallos += 1
            return datos, False

    def invalidar(self) -> None:
        with self._lock:
            self._datos = None


# Instancia compartida; el TTL se fija al importar (config) y se puede cambiar en pruebas.
from .config import settings  # noqa: E402

cache_cursos = CacheCursos(settings.cursos_cache_ttl_seconds)
