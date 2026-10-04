"""Identificación simulada de usuarios por DNI (RF-50, RF-51, RF-55; supuesto S-01).

No hay contraseñas: el usuario elige su perfil y escribe su DNI, y el sistema valida que exista:

- **Socio**: cualquier socio registrado, en cualquier estado (activo, suspendido o baja; S-06).
- **Administrador**: empleado **activo** con rol ``administracion``, ``gerente`` o ``recepcion`` (S-03).

El :class:`Usuario` resultante es la única fuente del perfil, la identidad y el alcance de sede
del agente; nunca se toman del texto del mensaje (RF-55). No guarda el DNI en claro: solo el id
y un seudónimo HMAC para las trazas.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Literal

from pydantic import BaseModel

from app.config import get_settings
from app.db.conexion import conectar

Perfil = Literal["socio", "admin"]
ROLES_ADMIN = ("administracion", "gerente", "recepcion")


class Usuario(BaseModel):
    """Usuario identificado. Sale de :func:`identificar`, nunca del LLM (RF-55)."""

    perfil: Perfil
    id: int                       # socio.id o empleado.id
    nombre: str
    sede_alcance: int | None      # admin: sede_id o None = todas; socio: sede principal
    sede_nombre: str | None = None
    rol: str | None = None        # solo admin: administracion | gerente | recepcion
    seudonimo: str                # HMAC-SHA256(dni, PSEUDONIMO_SECRET)[:12]

    @property
    def es_admin(self) -> bool:
        return self.perfil == "admin"

    def descripcion_alcance(self) -> str:
        """Texto del alcance de escritura para la interfaz."""
        if not self.es_admin:
            return "Solo lectura de tus datos"
        return f"Escritura: socios de {self.sede_nombre}" if self.sede_alcance else "Escritura: todas las sedes"


def seudonimo(dni: str) -> str:
    """Seudónimo estable del DNI para las trazas: HMAC-SHA256 truncado a 12 caracteres."""
    secreto = get_settings().pseudonimo_secret.encode()
    return hmac.new(secreto, dni.strip().encode(), hashlib.sha256).hexdigest()[:12]


def _normalizar_dni(dni: str) -> str:
    return "".join(c for c in (dni or "") if c.isdigit())


def identificar(perfil: Perfil, dni: str) -> Usuario | None:
    """Valida el DNI para el perfil elegido.

    Returns:
        El :class:`Usuario`, o ``None`` si el DNI no es válido para ese perfil. La interfaz muestra
        siempre el mismo mensaje genérico, sin revelar si el DNI existe con otro perfil (RF-51).
    """
    dni = _normalizar_dni(dni)
    if not dni:
        return None
    with conectar("lector_admin") as conn:
        if perfil == "socio":
            fila = conn.execute(
                """SELECT s.id, s.nombre || ' ' || s.apellido AS nombre, s.sede_principal_id AS sede, se.nombre AS sede_nombre
                   FROM socio s LEFT JOIN sede se ON se.id = s.sede_principal_id
                   WHERE s.dni = %s""",
                (dni,),
            ).fetchone()
        elif perfil == "admin":
            fila = conn.execute(
                """SELECT e.id, e.nombre || ' ' || e.apellido AS nombre, e.sede_id AS sede, se.nombre AS sede_nombre, e.rol
                   FROM empleado e LEFT JOIN sede se ON se.id = e.sede_id
                   WHERE e.dni = %s AND e.activo AND e.rol = ANY(%s)""",
                (dni, list(ROLES_ADMIN)),
            ).fetchone()
        else:
            return None
    if not fila:
        return None
    return Usuario(perfil=perfil, id=fila["id"], nombre=fila["nombre"], sede_alcance=fila["sede"],
                   sede_nombre=fila["sede_nombre"], rol=fila.get("rol"), seudonimo=seudonimo(dni))
