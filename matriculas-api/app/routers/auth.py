"""Inicio de sesión OAuth2 local y emisión de JWT para estudiantes."""
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..security import (comprobar_limite_fallos, crear_access_token,
                        fallo_de_autenticacion, verificar_password)

router = APIRouter(prefix="/v1/auth", tags=["Autenticación"])


@router.post(
    "/token",
    response_model=schemas.AccessToken,
    summary="Iniciar sesión y obtener un JWT",
    description=(
        "Flujo OAuth2 password local: usa el email como `username` y la contraseña "
        "configurada por un administrador. No contacta servicios externos."
    ),
)
def emitir_token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    comprobar_limite_fallos(request)
    email = form.username.strip().lower()
    credencial = db.execute(
        select(models.StudentCredential, models.Student)
        .join(models.Student, models.Student.id == models.StudentCredential.student_id)
        .where(models.Student.email == email, models.Student.anonimizado_en.is_(None))
    ).first()
    if credencial is None or not verificar_password(form.password, credencial[0].password_hash):
        raise fallo_de_autenticacion(request, "Usuario o contraseña incorrectos")

    jwt = crear_access_token(credencial[1].id)
    return schemas.AccessToken(access_token=jwt, expires_in=settings.jwt_ttl_minutes * 60)
