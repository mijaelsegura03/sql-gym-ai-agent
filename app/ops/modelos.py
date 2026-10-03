"""Modelos de las operaciones de escritura sobre socios (spec técnico §7.1).

``extraer_operacion`` (LLM) convierte el mensaje del administrador en una :data:`OperacionSocio`.
Los campos de datos son opcionales **a propósito**: si el mensaje no trae un dato, el LLM lo deja
vacío y la validación informa qué falta (RF-45), en lugar de inventarlo.

``validar`` (código) devuelve un :class:`ResultadoValidacion` con la :class:`PropuestaCambio` que se
muestra al administrador para confirmar (RF-46). Los errores y advertencias son :class:`Aviso`
con un código y parámetros; el texto en castellano o inglés lo arman las plantillas de
``app/ops/mensajes.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Salida de extraer_operacion
# ---------------------------------------------------------------------------
class ReferenciaSocio(BaseModel):
    """Cómo identificó el administrador al socio: preferentemente por DNI."""

    dni: str | None = Field(None, description="DNI del socio, solo dígitos, si el mensaje lo indica")
    nombre: str | None = Field(None, description="Nombre (y apellido) del socio, solo si no se indicó el DNI")


class ContactoEmergencia(BaseModel):
    """Contacto de emergencia del alta (DOC-01 §2: nombre, parentesco y teléfono)."""

    nombre: str | None = Field(None, description="Nombre y apellido del contacto")
    parentesco: str | None = Field(None, description="Parentesco o relación, p. ej. padre, madre, pareja, amigo")
    telefono: str | None = Field(None, description="Teléfono del contacto")


class AltaSocio(BaseModel):
    """OP-01: alta de un socio nuevo."""

    tipo: Literal["alta_socio"] = "alta_socio"
    dni: str | None = Field(None, description="DNI, solo dígitos")
    nombre: str | None = None
    apellido: str | None = None
    fecha_nacimiento: date | None = Field(None, description="Fecha de nacimiento (AAAA-MM-DD)")
    contacto_emergencia: ContactoEmergencia | None = None
    email: str | None = None
    telefono: str | None = None
    sede: str | None = Field(None, description="Sede principal si el mensaje la indica (Centro, Norte o Sur)")


class CambiosSocio(BaseModel):
    """Datos nuevos de una modificación. Solo se completan los que el mensaje pide cambiar."""

    nombre: str | None = None
    apellido: str | None = None
    email: str | None = None
    telefono: str | None = None
    fecha_nacimiento: date | None = Field(None, description="AAAA-MM-DD")
    contacto_emergencia: str | None = Field(None, description="Contacto de emergencia completo, tal como lo indica el mensaje")
    sede_principal: str | None = Field(None, description="Nueva sede principal (Centro, Norte o Sur)")
    dni: str | None = Field(None, description="Solo si el mensaje pide cambiar el DNI (no está permitido)")

    def pedidos(self) -> dict[str, Any]:
        """Campos que el mensaje pide cambiar (los no vacíos)."""
        return {k: v for k, v in self.model_dump().items() if v not in (None, "")}


class ModificacionSocio(BaseModel):
    """OP-02: modificación de datos de un socio existente."""

    tipo: Literal["modificacion_socio"] = "modificacion_socio"
    socio: ReferenciaSocio
    cambios: CambiosSocio


class Suspension(BaseModel):
    """OP-03: suspensión de un socio activo."""

    tipo: Literal["suspension"] = "suspension"
    socio: ReferenciaSocio
    motivo: str | None = None


class Reactivacion(BaseModel):
    """OP-04: reactivación de un socio suspendido o de baja."""

    tipo: Literal["reactivacion"] = "reactivacion"
    socio: ReferenciaSocio


class Baja(BaseModel):
    """OP-05: baja de un socio (cambio de estado, sin borrar nada)."""

    tipo: Literal["baja"] = "baja"
    socio: ReferenciaSocio
    motivo: str | None = None


class FueraDeCatalogo(BaseModel):
    """Pedido de escritura que no está en el catálogo, o que afecta a varios socios."""

    tipo: Literal["fuera_de_catalogo"] = "fuera_de_catalogo"
    descripcion: str = Field(description="Qué se pidió, en pocas palabras")
    es_masiva: bool = Field(False, description="True si afecta a varios socios (p. ej. 'todos los morosos')")


OperacionSocio = Annotated[
    Union[AltaSocio, ModificacionSocio, Suspension, Reactivacion, Baja, FueraDeCatalogo],
    Field(discriminator="tipo"),
]


class ExtraccionOperacion(BaseModel):
    """Envoltorio para la salida estructurada del LLM (``with_structured_output``)."""

    operacion: OperacionSocio


# ---------------------------------------------------------------------------
# Resultado de la validación
# ---------------------------------------------------------------------------
class Aviso(BaseModel):
    """Error o advertencia de negocio. ``codigo`` indexa las plantillas de ``mensajes.py``."""

    codigo: str
    params: dict[str, Any] = {}


class SocioAfectado(BaseModel):
    id: int | None = None  # None en el alta (todavía no existe)
    nombre: str
    dni: str


class PropuestaCambio(BaseModel):
    """Propuesta de cambio que se muestra antes de ejecutar (RF-46)."""

    operacion: str                       # alta_socio | modificacion_socio | suspension | reactivacion | baja
    socio: SocioAfectado
    antes: dict[str, Any] = {}
    despues: dict[str, Any] = {}
    efectos: list[Aviso] = []            # p. ej. membresías que se cancelan
    advertencias: list[Aviso] = []
    errores: list[Aviso] = []
    motivo: str | None = None
    # Datos para ejecutar (no se muestran)
    datos: dict[str, Any] = {}           # valores normalizados que se escriben
    membresias_a_cancelar: list[int] = []
    sede_alcance_admin: int | None = None


class ResultadoValidacion(BaseModel):
    """Resultado de ``validar``: la propuesta si es válida, o los errores."""

    operacion: str
    errores: list[Aviso] = []
    propuesta: PropuestaCambio | None = None

    @property
    def valida(self) -> bool:
        return not self.errores and self.propuesta is not None


class ResultadoEjecucion(BaseModel):
    """Resultado de ejecutar una propuesta confirmada (RF-48)."""

    ok: bool
    operacion: str
    socio_id: int | None = None
    errores: list[Aviso] = []
