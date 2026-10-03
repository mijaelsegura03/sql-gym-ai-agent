"""Recorrido del grafo con un LLM simulado (spec técnico §5.3, T-23). Corren sin red ni Docker.

Se reemplazan los modelos por dobles que devuelven clasificaciones y textos fijos, y las
herramientas (base, documentos, validación y ejecución) por funciones en memoria.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.agent import grafo as modulo_grafo
from app.agent.estado import Clasificacion
from app.agent.nodos import modelos
from app.auth import Usuario
from app.db.lectura import ResultadoLectura
from app.ops.modelos import (
    Aviso, ExtraccionOperacion, PropuestaCambio, ReferenciaSocio, ResultadoEjecucion, ResultadoValidacion,
    SocioAfectado, Suspension,
)
from app.rag.buscador import Fragmento

SOCIO = Usuario(perfil="socio", id=7, nombre="Socia Prueba", sede_alcance=1, seudonimo="s1")
ADMIN = Usuario(perfil="admin", id=15, nombre="Admin Prueba", sede_alcance=None, seudonimo="a1")
HOY = date(2026, 10, 16)


class LLMFalso:
    """Doble de un chat model: ``invoke`` devuelve un texto y ``with_structured_output`` un objeto fijo."""

    def __init__(self, estructurado=None, textos=None):
        self.estructurado = estructurado or {}
        self.textos = list(textos or [])
        self.llamadas: list[str] = []

    def invoke(self, prompt):
        self.llamadas.append("texto")
        return SimpleNamespace(content=self.textos.pop(0) if len(self.textos) > 1 else (self.textos[0] if self.textos else "ok"))

    def with_structured_output(self, esquema):
        llm = self

        class _Estructurado:
            def invoke(self, prompt):
                llm.llamadas.append(esquema.__name__)
                return llm.estructurado[esquema.__name__]

        return _Estructurado()


@pytest.fixture
def entorno(monkeypatch):
    """Grafo nuevo con herramientas en memoria. Devuelve un objeto para configurar y registrar llamadas."""
    e = SimpleNamespace(lecturas=[], busquedas=[], ejecuciones=[], auditoria=[],
                        resultado_sql=ResultadoLectura(sql="SELECT 1", columnas=["n"], filas=[{"n": 1}], total_filas=1),
                        fragmentos=[Fragmento(doc_id="DOC-04", seccion="1. Apto médico", pagina=1, texto="t\nDura 12 meses.")],
                        validacion=None)

    def leer(sql, usuario):
        e.lecturas.append(sql)
        return e.resultado_sql if not callable(e.resultado_sql) else e.resultado_sql(sql)

    monkeypatch.setattr("app.agent.nodos.sql.ejecutar_lectura", leer)
    monkeypatch.setattr("app.agent.nodos.documentos.buscar", lambda q: e.busquedas.append(q) or e.fragmentos)
    monkeypatch.setattr("app.agent.nodos.escritura.validar", lambda op, u, hoy: e.validacion)
    monkeypatch.setattr("app.agent.nodos.escritura.ejecutar",
                        lambda p, op, u, hoy, trace_id=None: e.ejecuciones.append(p) or ResultadoEjecucion(ok=True, operacion=p.operacion, socio_id=p.socio.id))
    monkeypatch.setattr("app.ops.auditoria.registrar", lambda resultado, *a, **k: e.auditoria.append(resultado))
    monkeypatch.setattr(modulo_grafo, "_grafo", modulo_grafo.construir_grafo())
    monkeypatch.setattr(modulo_grafo, "get_grafo", lambda: modulo_grafo._grafo)
    yield e
    modelos.rapido = modelos.principal = None


def usar(clasificacion: Clasificacion, principal: LLMFalso | None = None) -> tuple[LLMFalso, LLMFalso]:
    rapido = LLMFalso(estructurado={"Clasificacion": clasificacion})
    principal = principal or LLMFalso(textos=["respuesta"])
    modelos.rapido, modelos.principal = rapido, principal
    return rapido, principal


def clasif(ruta, **kw) -> Clasificacion:
    return Clasificacion(ruta=ruta, idioma=kw.pop("idioma", "es"), pide_info_interna=kw.pop("interna", False), **kw)


# ---------------------------------------------------------------------------
def test_ruta_directa_sin_herramientas(entorno):
    rapido, principal = usar(clasif("directa", respuesta_directa="¡Hola! ¿En qué te ayudo?"))
    r = modulo_grafo.responder(SOCIO, "Hola, ¿cómo estás?", HOY)
    assert r.nodos == ["clasificar", "responder_directo"]
    assert r.herramientas == [] and r.fuentes == [] and r.texto == "¡Hola! ¿En qué te ayudo?"
    assert rapido.llamadas == ["Clasificacion"] and principal.llamadas == []  # una sola llamada al LLM
    assert not entorno.lecturas and not entorno.busquedas


@pytest.mark.parametrize("ruta", ["fuera_dominio", "necesita_contexto"])
def test_fuera_de_dominio_y_contexto_sin_herramientas(entorno, ruta):
    usar(clasif(ruta, respuesta_directa="x"))
    r = modulo_grafo.responder(ADMIN, "¿Quién ganó el mundial?", HOY)
    assert r.herramientas == [] and r.ruta == ruta


def test_socio_con_escritura_nunca_llega_a_extraer_operacion(entorno):
    _, principal = usar(clasif("escritura"))
    r = modulo_grafo.responder(SOCIO, "Cambiá mi email a nuevo@example.com", HOY)
    assert r.nodos == ["clasificar", "rechazar_permiso"]
    assert "extraer_operacion" not in r.nodos and principal.llamadas == [] and r.herramientas == []
    assert "recepción" in r.texto


def test_socio_con_info_interna_se_rechaza(entorno):
    usar(clasif("datos", interna=True, pregunta_datos="facturación del mes"))
    r = modulo_grafo.responder(SOCIO, "¿Cuánto facturó el gimnasio este mes?", HOY)
    assert r.nodos == ["clasificar", "rechazar_permiso"] and not entorno.lecturas


def test_admin_con_info_interna_consulta(entorno):
    usar(clasif("datos", interna=True), LLMFalso(textos=["```sql\nSELECT 1\n```", "Se facturaron $ 1."]))
    r = modulo_grafo.responder(ADMIN, "¿Cuánto facturó el gimnasio este mes?", HOY)
    assert r.nodos == ["clasificar", "generar_sql", "ejecutar_sql", "sintetizar"]


def test_ruta_datos(entorno):
    usar(clasif("datos", pregunta_datos="vencimiento"), LLMFalso(textos=["```sql\nSELECT 1\n```", "Vence el 16/10/2026."]))
    r = modulo_grafo.responder(ADMIN, "¿Cuándo vence…?", HOY)
    assert r.nodos == ["clasificar", "generar_sql", "ejecutar_sql", "sintetizar"]
    assert r.herramientas == ["consulta_sql"] and r.sql == "SELECT 1" and r.filas == [{"n": 1}]
    assert r.fuentes == ["base de datos"] and "Fuente: base de datos" in r.texto


def test_sql_oculta_para_el_socio(entorno):
    usar(clasif("datos"), LLMFalso(textos=["```sql\nSELECT 1\n```", "ok"]))
    r = modulo_grafo.responder(SOCIO, "¿Cuándo vence mi cuota?", HOY)
    assert r.sql is None and r.filas == [{"n": 1}]  # RF-56


def test_sql_con_error_reintenta_dos_veces_y_responde_comprensible(entorno):
    entorno.resultado_sql = ResultadoLectura(sql="SELECT x", error="Error de la base de datos: no existe x")
    _, principal = usar(clasif("datos"), LLMFalso(textos=["```sql\nSELECT x\n```"]))
    r = modulo_grafo.responder(ADMIN, "¿…?", HOY)
    assert r.nodos.count("generar_sql") == 3 and r.nodos.count("ejecutar_sql") == 3
    assert "no existe" not in r.texto and "No pude obtener" in r.texto  # RF-23, sin traza técnica
    assert principal.llamadas == ["texto"] * 3  # no se llama al LLM para sintetizar


def test_ruta_documentos_con_citas_desde_metadatos(entorno):
    usar(clasif("documentos", consulta_documentos="vigencia del apto"),
         LLMFalso(textos=["Dura 12 meses [DOC-04 §1, p. 1]. Además [DOC-09 §3, p. 1]."]))
    r = modulo_grafo.responder(SOCIO, "¿Cuánto dura el apto médico?", HOY)
    assert r.nodos == ["clasificar", "buscar_documentos", "sintetizar"]
    assert r.herramientas == ["busqueda_documentos"] and entorno.busquedas == ["vigencia del apto"]
    assert r.fuentes == ["DOC-04 §1"]  # la cita inventada (DOC-09) no entra en las fuentes


def test_ruta_hibrida_en_paralelo_y_sintetiza_una_vez(entorno):
    principal = LLMFalso(textos=["```sql\nSELECT 1\n```", "Respuesta [DOC-04 §1, p. 1]"])
    usar(clasif("hibrida", pregunta_datos="mi apto", consulta_documentos="observaciones"), principal)
    r = modulo_grafo.responder(SOCIO, "Con mi apto médico, ¿qué clases me recomiendan?", HOY)
    assert set(r.herramientas) == {"consulta_sql", "busqueda_documentos"}
    assert r.nodos.count("sintetizar") == 1 and r.nodos[-1] == "sintetizar"
    assert r.fuentes == ["base de datos", "DOC-04 §1"]


def test_ruta_hibrida_espera_los_reintentos_de_sql(entorno):
    llamadas = {"n": 0}

    def resultado(sql):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return ResultadoLectura(sql=sql, error="falla")
        return ResultadoLectura(sql=sql, columnas=["n"], filas=[{"n": 2}], total_filas=1)

    entorno.resultado_sql = resultado
    usar(clasif("hibrida"), LLMFalso(textos=["```sql\nSELECT 1\n```", "```sql\nSELECT 2\n```", "fin"]))
    r = modulo_grafo.responder(ADMIN, "¿…?", HOY)
    assert r.nodos.count("generar_sql") == 2 and r.nodos.count("sintetizar") == 1 and r.nodos[-1] == "sintetizar"
    assert r.filas == [{"n": 2}]


# ---------------------------------------------------------------------------
# Escritura con confirmación
# ---------------------------------------------------------------------------
def _propuesta() -> PropuestaCambio:
    return PropuestaCambio(operacion="suspension", socio=SocioAfectado(id=3, nombre="Bruno Juárez", dni="21899127"),
                           antes={"estado": "activo"}, despues={"estado": "suspendido"}, motivo="x", datos={"estado": "suspendido"})


def _usar_escritura(entorno, valida=True):
    op = Suspension(socio=ReferenciaSocio(dni="21899127"), motivo="x")
    entorno.validacion = (ResultadoValidacion(operacion="suspension", propuesta=_propuesta()) if valida
                          else ResultadoValidacion(operacion="suspension", errores=[Aviso(codigo="falta_motivo", params={"operacion": "suspension"})]))
    usar(clasif("escritura"), LLMFalso(estructurado={"ExtraccionOperacion": ExtraccionOperacion(operacion=op)}))


def test_escritura_valida_se_detiene_en_el_interrupt(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127 por x", HOY)
    assert r.propuesta_pendiente and r.propuesta.operacion == "suspension"
    assert r.nodos == ["clasificar", "extraer_operacion", "validar_operacion"]
    assert r.herramientas == ["operacion_socio"] and entorno.ejecuciones == []  # nada escrito todavía


def test_confirmar_ejecuta(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    r2 = modulo_grafo.reanudar(ADMIN, r.thread_id, "confirmar")
    assert len(entorno.ejecuciones) == 1 and not r2.propuesta_pendiente
    assert r2.nodos[-3:] == ["confirmar", "ejecutar_operacion", "responder_operacion"]
    assert "realizada" in r2.texto
    # Reanudar otra vez (p. ej. al recargar la página) no vuelve a ejecutar
    r3 = modulo_grafo.reanudar(ADMIN, r.thread_id, "confirmar")
    assert r3.error and len(entorno.ejecuciones) == 1


def test_cancelar_no_escribe_y_audita(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    r2 = modulo_grafo.reanudar(ADMIN, r.thread_id, "cancelar")
    assert entorno.ejecuciones == [] and entorno.auditoria == ["cancelada"] and "cancelada" in r2.texto


def test_descartar_no_escribe_y_audita(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    assert modulo_grafo.descartar(ADMIN, r.thread_id)
    assert entorno.auditoria == ["descartada"]
    assert modulo_grafo.reanudar(ADMIN, r.thread_id, "confirmar").error and entorno.ejecuciones == []


def test_otro_usuario_no_puede_confirmar(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    otro = ADMIN.model_copy(update={"id": 16})
    assert modulo_grafo.reanudar(otro, r.thread_id, "confirmar").error and entorno.ejecuciones == []


def test_escribir_si_no_confirma(entorno):
    _usar_escritura(entorno)
    modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    usar(clasif("necesita_contexto", respuesta_directa="Usá el botón Confirmar."))
    r = modulo_grafo.responder(ADMIN, "sí", HOY)  # hilo nuevo (CA-57)
    assert entorno.ejecuciones == [] and r.herramientas == []


def test_escritura_invalida_responde_sin_propuesta(entorno):
    _usar_escritura(entorno, valida=False)
    r = modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127", HOY)
    assert not r.propuesta_pendiente and r.propuesta is None
    assert r.nodos == ["clasificar", "extraer_operacion", "validar_operacion", "responder_operacion"]
    assert entorno.auditoria == ["rechazada"] and "motivo" in r.texto


def test_cada_mensaje_usa_un_hilo_nuevo(entorno):
    usar(clasif("directa", respuesta_directa="hola"))
    ids = {modulo_grafo.responder(SOCIO, "hola", HOY).thread_id for _ in range(3)}
    assert len(ids) == 3


def test_error_interno_mensaje_comprensible(entorno):
    class Roto(LLMFalso):
        def with_structured_output(self, esquema):
            raise RuntimeError("boom: traceback interno")

    modelos.rapido = Roto()
    r = modulo_grafo.responder(SOCIO, "hola", HOY)
    assert r.error and "boom" not in r.texto
