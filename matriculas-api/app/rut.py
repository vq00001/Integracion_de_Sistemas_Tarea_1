"""Validación y normalización de RUT chileno.

Se valida el formato y el dígito verificador (módulo 11) para no almacenar
identificadores inventados: es una medida de *calidad del dato personal*
(principio de calidad de la normativa de protección de datos), y evita que
valores basura como "abc" lleguen a la base de datos.
"""
from __future__ import annotations

import re

_PATRON = re.compile(r"^(\d{7,8})-?([0-9K])$")


def digito_verificador(cuerpo: str) -> str:
    """Calcula el DV de un RUT con el algoritmo módulo 11 (serie 2..7)."""
    suma = 0
    factor = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def normalizar_rut(texto: str) -> str:
    """Devuelve el RUT en formato canónico `12345678-5` o lanza ValueError."""
    limpio = texto.strip().replace(".", "").replace(" ", "").upper()
    m = _PATRON.match(limpio)
    if m is None:
        raise ValueError("El RUT debe tener 7 u 8 dígitos y un dígito verificador (ej. 12345678-5)")
    cuerpo, dv = m.groups()
    if digito_verificador(cuerpo) != dv:
        raise ValueError("El dígito verificador del RUT no es válido")
    return f"{cuerpo}-{dv}"
