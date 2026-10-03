"""Interfaz Streamlit con ``AppTest`` y un LLM simulado, contra la base real (CA-80 a CA-86, CA-50, CA-55 a CA-57)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.agent.estado import Clasificacion
from app.agent.nodos import modelos
from app.ops.modelos import AltaSocio, ContactoEmergencia, ExtraccionOperacion
from tests.test_grafo_rutas import LLMFalso

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("base_restaurada")]

APP = str(Path(__file__).resolve().parent.parent / "app" / "ui" / "app.py")
ADMIN_NORTE = "26789012"   # gerente de la Sede Norte
RECEPCION_SUR = "39654120"  # CA-86
INSTRUCTOR = "28345671"
RECEPCION_INACTIVO = "36789012"  # CA-81


@pytest.fixture
def at():
    from streamlit.testing.v1 import AppTest

    yield AppTest.from_file(APP, default_timeout=60).run()
    modelos.rapido = modelos.principal = None


def ingresar(at, perfil: str, dni: str):
    at.radio[0].set_value(perfil)
    at.text_input[0].input(dni)
    at.button[0].click().run()
    return at


def textos(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


@pytest.mark.parametrize("perfil,dni", [("Administrador", INSTRUCTOR), ("Administrador", RECEPCION_INACTIVO),
                                        ("Socio", "30123987")])
def test_ingreso_invalido_mensaje_generico(at, perfil, dni):
    ingresar(at, perfil, dni)
    assert [e.value for e in at.error] == ["No encontramos un usuario con esos datos."]
    assert not at.chat_input


def test_ingreso_recepcion_sur(at):
    ingresar(at, "Administrador", RECEPCION_SUR)
    assert not at.exception and at.chat_input
    assert any("Sede Sur" in str(m.value) for m in at.sidebar.markdown)


def test_detalle_directa_sin_herramientas(at):
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": Clasificacion(
        ruta="directa", idioma="es", pide_info_interna=False, respuesta_directa="¡Hola!")})
    ingresar(at, "Socio", "21899127")
    at.chat_input[0].set_value("Hola").run()
    assert "¡Hola!" in textos(at)
    assert "**Herramientas invocadas:** ninguna" in textos(at)  # CA-84


def _clasif_datos():
    return Clasificacion(ruta="datos", idioma="es", pide_info_interna=False, pregunta_datos="socios por sede")


def test_detalle_admin_muestra_sql(at):
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": _clasif_datos()})
    modelos.principal = LLMFalso(textos=["```sql\nSELECT nombre FROM sede ORDER BY id\n```", "Hay 3 sedes."])
    ingresar(at, "Administrador", ADMIN_NORTE)
    at.chat_input[0].set_value("¿Qué sedes hay?").run()
    assert any("SELECT nombre FROM sede" in c.value for c in at.code)  # CA-82
    assert at.dataframe  # tabla (RF-03)


def test_detalle_socio_sin_sql(at):
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": _clasif_datos()})
    modelos.principal = LLMFalso(textos=["```sql\nSELECT nombre FROM sede ORDER BY id\n```", "Hay 3 sedes."])
    ingresar(at, "Socio", "21899127")
    at.chat_input[0].set_value("¿Qué sedes hay?").run()
    assert not at.code and "Consulta de datos" in textos(at)  # CA-83


def _alta_falsa():
    op = AltaSocio(dni="40111222", nombre="Ana", apellido="Torres", fecha_nacimiento=date(1995, 3, 12),
                   email="ana.torres@example.com",
                   contacto_emergencia=ContactoEmergencia(nombre="Luis Torres", parentesco="padre", telefono="341 5551234"))
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": Clasificacion(ruta="escritura", idioma="es", pide_info_interna=False)})
    modelos.principal = LLMFalso(estructurado={"ExtraccionOperacion": ExtraccionOperacion(operacion=op)})


def _existe(su) -> bool:
    return bool(su.execute("SELECT 1 FROM socio WHERE dni='40111222'").fetchone())


def _boton(at, prefijo: str):
    return next(b for b in at.button if b.key and b.key.startswith(prefijo))


def test_alta_confirmada(at, su):
    _alta_falsa()
    ingresar(at, "Administrador", ADMIN_NORTE)
    at.chat_input[0].set_value("Dá de alta a Ana Torres…").run()
    assert "Propuesta de cambio" in textos(at) and not _existe(su)
    _boton(at, "confirmar_").click().run()
    assert _existe(su)  # CA-50
    assert _boton(at, "confirmar_").disabled and _boton(at, "cancelar_").disabled  # RF-65
    assert any("realizada" in str(s.value) for s in at.success)


def test_alta_cancelada(at, su):
    _alta_falsa()
    ingresar(at, "Administrador", ADMIN_NORTE)
    at.chat_input[0].set_value("Dá de alta a Ana Torres…").run()
    _boton(at, "cancelar_").click().run()
    assert not _existe(su)  # CA-55
    assert su.execute("SELECT resultado FROM agente.auditoria ORDER BY id DESC LIMIT 1").fetchone()["resultado"] == "cancelada"


def test_otro_mensaje_descarta_y_si_no_confirma(at, su):
    _alta_falsa()
    ingresar(at, "Administrador", ADMIN_NORTE)
    at.chat_input[0].set_value("Dá de alta a Ana Torres…").run()
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": Clasificacion(
        ruta="necesita_contexto", idioma="es", pide_info_interna=False, respuesta_directa="Usá el botón Confirmar.")})
    at.chat_input[0].set_value("sí").run()  # CA-56 y CA-57
    assert not _existe(su)
    assert su.execute("SELECT resultado FROM agente.auditoria ORDER BY id DESC LIMIT 1").fetchone()["resultado"] == "descartada"
    assert "descartada" in "\n".join(str(c.value) for c in at.caption)
