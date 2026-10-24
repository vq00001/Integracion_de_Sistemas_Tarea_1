"""O4 — Pruebas de contrato: la implementación en ejecución cumple el OpenAPI.

1. El contrato versionado en el repositorio (matriculas-api/docs/openapi.{json,yaml}) es
   exactamente el que publica la API en ejecución (no hay deriva entre código y contrato).
2. Cada respuesta real (código de estado incluido) cumple el esquema que el contrato
   declara para esa operación y ese código.
"""
import json
import uuid
from pathlib import Path

import jsonschema
import pytest
import yaml

from tests.conftest import CURSO_CON_CUPO, ROOT

DOCS = ROOT / "matriculas-api" / "docs"


@pytest.fixture(scope="module")
def spec(api_sin_auth):
    r = api_sin_auth.get("/openapi.json")
    assert r.status_code == 200
    return r.json()


def validar(spec, metodo, ruta, respuesta):
    """Valida `respuesta` contra lo que el OpenAPI declara para (ruta, método, código)."""
    operacion = spec["paths"][ruta][metodo]
    codigo = str(respuesta.status_code)
    assert codigo in operacion["responses"], (
        f"{metodo.upper()} {ruta} devolvió {codigo}, que el contrato no declara "
        f"(declara {sorted(operacion['responses'])})"
    )
    contenido = operacion["responses"][codigo].get("content", {}).get("application/json")
    if contenido is None:
        return
    esquema = {**contenido["schema"], "components": spec["components"]}
    jsonschema.validate(respuesta.json(), esquema)


def test_el_contrato_versionado_coincide_con_la_api_en_ejecucion(spec):
    # encoding explícito: el contrato tiene tildes y en Windows read_text() usaría cp1252
    assert json.loads((DOCS / "openapi.json").read_text(encoding="utf-8")) == spec, (
        "docs/openapi.json está desactualizado respecto de la API en ejecución. Regénralo desde "
        "la API levantada: python matriculas-api/exportar_openapi.py --desde-url http://localhost:8000/openapi.json"
    )
    assert yaml.safe_load((DOCS / "openapi.yaml").read_text(encoding="utf-8")) == spec


def test_el_contrato_declara_autenticacion_y_idempotencia(spec):
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    params = spec["paths"]["/v1/enrollments"]["post"]["parameters"]
    assert any(p["name"] == "Idempotency-Key" and p["in"] == "header" for p in params)
    assert "504" in spec["paths"]["/v1/enrollments"]["post"]["responses"]
    assert "403" not in spec["paths"]["/v1/enrollments"]["post"]["responses"]


def test_respuestas_de_estudiantes_cumplen_el_contrato(api, api_sin_auth, spec, estudiante):
    validar(spec, "get", "/v1/students/{student_id}", api.get(f"/v1/students/{estudiante['id']}"))
    validar(spec, "get", "/v1/students/{student_id}", api.get("/v1/students/no-existe"))
    validar(spec, "get", "/v1/students", api.get("/v1/students"))
    validar(spec, "get", "/v1/students", api_sin_auth.get("/v1/students"))  # 401
    validar(spec, "post", "/v1/students", api.post("/v1/students", json={"nombre": "x"}))  # 422


def test_respuestas_de_matriculas_cumplen_el_contrato(api, spec, estudiante):
    cuerpo = {"student_id": estudiante["id"], "curso_id": CURSO_CON_CUPO}
    creada = api.post("/v1/enrollments", json=cuerpo, headers={"Idempotency-Key": str(uuid.uuid4())})
    validar(spec, "post", "/v1/enrollments", creada)                       # 201
    validar(spec, "post", "/v1/enrollments", api.post("/v1/enrollments", json=cuerpo))  # 409
    validar(spec, "post", "/v1/enrollments",
            api.post("/v1/enrollments", json={"student_id": "x", "curso_id": 1}))      # 404
    eid = creada.json()["id"]
    validar(spec, "get", "/v1/enrollments/{enrollment_id}", api.get(f"/v1/enrollments/{eid}"))
    validar(spec, "get", "/v1/enrollments", api.get("/v1/enrollments"))
    validar(spec, "post", "/v1/enrollments/{enrollment_id}/revert", api.post(f"/v1/enrollments/{eid}/revert"))
    validar(spec, "post", "/v1/enrollments/{enrollment_id}/revert", api.post(f"/v1/enrollments/{eid}/revert"))  # 409
