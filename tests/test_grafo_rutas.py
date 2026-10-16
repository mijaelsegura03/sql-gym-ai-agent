"""Recorrido del grafo con un LLM simulado (spec técnico §5.3, §12; T-23, T-38, T-43). Corren sin red ni Docker.

Se reemplazan los modelos por dobles que devuelven clasificaciones, ``tool_calls`` y textos fijos, y las
herramientas (base, exploración, documentos, validación y ejecución) por funciones en memoria.
"""

from __future__ import annotations

from datetime import date
from itertools import count
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent import grafo as modulo_grafo
from app.agent.estado import Clasificacion
from app.agent.nodos import modelos
from app.agent.nodos.agente import MAX_PASOS_AGENTE
from app.auth import Usuario
from app.db.esquema import ResultadoExploracion
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
    """Doble de un chat model.

    - ``invoke`` devuelve un texto de ``textos`` (el último se repite);
    - ``with_structured_output`` devuelve el objeto fijo de ``estructurado`` según el esquema;
    - ``bind_tools`` devuelve los mensajes del agente de lectura de ``pasos`` (el último se repite).
    """

    def __init__(self, estructurado=None, textos=None, pasos=None):
        self.estructurado = estructurado or {}
        self.textos = list(textos or [])
        self.pasos = list(pasos or [])
        self.llamadas: list[str] = []
        self.herramientas_vistas: list[str] = []
        self.mensajes_vistos: list[list] = []
        self.prompts_estructurados: list[str] = []

    def invoke(self, prompt):
        self.llamadas.append("texto")
        return SimpleNamespace(content=self.textos.pop(0) if len(self.textos) > 1 else (self.textos[0] if self.textos else "ok"))

    def with_structured_output(self, esquema):
        llm = self

        class _Estructurado:
            def invoke(self, prompt):
                llm.llamadas.append(esquema.__name__)
                llm.prompts_estructurados.append(prompt)
                return llm.estructurado[esquema.__name__]

        return _Estructurado()

    def bind_tools(self, herramientas):
        llm = self
        llm.herramientas_vistas = [t.name for t in herramientas]

        class _ConHerramientas:
            def invoke(self, mensajes):
                llm.llamadas.append("agente")
                llm.mensajes_vistos.append(list(mensajes))
                if not llm.pasos:
                    return fin()
                mensaje = llm.pasos.pop(0) if len(llm.pasos) > 1 else llm.pasos[0]
                return mensaje.model_copy(update={"id": None})  # cada respuesta del modelo es un mensaje nuevo

        return _ConHerramientas()


_ids = count()


def pide(*llamadas: tuple[str, dict]) -> AIMessage:
    """Mensaje del agente que pide herramientas: ``pide(sql("SELECT 1"), docs("apto"))``."""
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"llamada-{next(_ids)}"} for n, a in llamadas])


def sql(consulta: str = "SELECT 1") -> tuple[str, dict]:
    return ("consulta_sql", {"sql": consulta})


def docs(consulta: str = "vigencia del apto") -> tuple[str, dict]:
    return ("busqueda_documentos", {"consulta": consulta})


def propone(aclaraciones: str = "") -> tuple[str, dict]:
    return ("operacion_socio", {"aclaraciones": aclaraciones})


def fin() -> AIMessage:
    """Mensaje del agente sin ``tool_calls``: ya reunió lo necesario."""
    return AIMessage(content="Listo.")


@pytest.fixture
def entorno(monkeypatch):
    """Grafo nuevo con herramientas en memoria. Devuelve un objeto para configurar y registrar llamadas."""
    e = SimpleNamespace(lecturas=[], busquedas=[], ejecuciones=[], auditoria=[], usuarios_lectura=[], exploraciones=[],
                        resultado_sql=ResultadoLectura(sql="SELECT 1", columnas=["n"], filas=[{"n": 1}], total_filas=1),
                        fragmentos=[Fragmento(doc_id="DOC-04", seccion="1. Apto médico", pagina=1, texto="t\nDura 12 meses.",
                                              titulo_doc="Manual de Salud, Apto Médico y Rutinas", chunk_id="DOC-04-001")],
                        validacion=None)

    def leer(consulta, usuario):
        e.lecturas.append(consulta)
        e.usuarios_lectura.append(usuario)
        if callable(e.resultado_sql):
            return e.resultado_sql(consulta)
        return e.resultado_sql.model_copy(update={"sql": consulta})  # ejecutar_lectura devuelve la SQL ejecutada

    monkeypatch.setattr("app.agent.herramientas.ejecutar_lectura", leer)
    monkeypatch.setattr("app.agent.herramientas.buscar", lambda q: e.busquedas.append(q) or e.fragmentos)
    monkeypatch.setattr("app.db.esquema.describir_tabla", lambda tabla, u: e.exploraciones.append((tabla, u))
                        or ResultadoExploracion(texto=f"Tabla {tabla}\nColumnas:\n- estado: text", resumen="1 columna"))
    monkeypatch.setattr("app.db.esquema.valores_columna", lambda tabla, col, u: e.exploraciones.append((tabla, col, u))
                        or ResultadoExploracion(texto="Valores (2 distintos):\n- activa: 85\n- vencida: 692", resumen="2 valores"))
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


def lectura(principal: LLMFalso, usuario: Usuario = ADMIN, mensaje: str = "¿…?", **kw):
    """Clasifica como lectura y responde con el agente simulado ``principal``."""
    usar(clasif("lectura", **kw), principal)
    return modulo_grafo.responder(usuario, mensaje, HOY)


# ---------------------------------------------------------------------------
# Respuesta directa y rechazos
# ---------------------------------------------------------------------------
def test_ruta_directa_sin_herramientas(entorno):
    rapido, principal = usar(clasif("directa", respuesta_directa="¡Hola! ¿En qué te ayudo?"))
    r = modulo_grafo.responder(SOCIO, "Hola, ¿cómo estás?", HOY)
    assert r.nodos == ["clasificar", "responder_directo"]
    assert r.herramientas == [] and r.fuentes == [] and r.pasos == [] and r.texto == "¡Hola! ¿En qué te ayudo?"
    assert rapido.llamadas == ["Clasificacion"] and principal.llamadas == []  # una sola llamada al LLM (CA-84)
    assert not entorno.lecturas and not entorno.busquedas


@pytest.mark.parametrize("ruta", ["fuera_dominio", "necesita_contexto"])
def test_fuera_de_dominio_y_contexto_sin_herramientas(entorno, ruta):
    usar(clasif(ruta, respuesta_directa="x"))
    r = modulo_grafo.responder(ADMIN, "¿Quién ganó el mundial?", HOY)
    assert r.herramientas == [] and r.ruta == ruta


def test_socio_con_escritura_nunca_llega_a_extraer_operacion(entorno):
    _, principal = usar(clasif("escritura"))
    r = modulo_grafo.responder(SOCIO, "Cambiá mi email a nuevo@example.com", HOY)
    assert r.nodos == ["clasificar", "rechazar_permiso"]  # ni el agente ni operacion_socio (RF-25, RF-40)
    assert "extraer_operacion" not in r.nodos and principal.llamadas == [] and r.herramientas == []
    assert "recepción" in r.texto


def test_socio_con_info_interna_se_rechaza_antes_del_agente(entorno):
    _, principal = usar(clasif("lectura", interna=True))
    r = modulo_grafo.responder(SOCIO, "¿Cuánto facturó el gimnasio este mes?", HOY)
    assert r.nodos == ["clasificar", "rechazar_permiso"] and r.ruta == "lectura"
    assert not entorno.lecturas and principal.llamadas == []


# ---------------------------------------------------------------------------
# Agente de lectura (RF-24)
# ---------------------------------------------------------------------------
def test_admin_con_info_interna_consulta(entorno):
    r = lectura(LLMFalso(pasos=[pide(sql()), fin()], textos=["Se facturaron $ 1."]), interna=True)
    assert r.nodos == ["clasificar", "agente", "ejecutar_herramientas", "agente", "sintetizar"]


def test_una_herramienta_datos(entorno):
    principal = LLMFalso(pasos=[pide(sql("SELECT 1")), fin()], textos=["Vence el 16/10/2026."])
    r = lectura(principal, mensaje="¿Cuándo vence…?")
    assert r.ruta == "datos" and r.herramientas == ["consulta_sql"]
    assert r.sql == "SELECT 1" and r.filas == [{"n": 1}] and r.total_filas == 1  # RF-03: filas del resultado
    assert r.fuentes == ["base de datos"] and "Fuente: base de datos" in r.texto
    assert [p.herramienta for p in r.pasos] == ["consulta_sql"] and r.pasos[0].argumentos == {"sql": "SELECT 1"}
    assert r.pasos[0].resumen == "1 fila" and len(r.consultas) == 1 and r.consultas[0].ok
    assert principal.llamadas == ["agente", "agente", "texto"]  # 2 del agente + sintetizar
    assert entorno.usuarios_lectura == [ADMIN]  # el usuario sale del estado, no del LLM (RNF-01)


def test_el_agente_solo_ve_las_herramientas_de_lectura(entorno):
    principal = LLMFalso(pasos=[fin()])
    lectura(principal)
    assert principal.herramientas_vistas == ["consulta_sql", "busqueda_documentos", "describir_tabla",
                                             "valores_columna"]  # sin operacion_socio en una lectura (RF-25)


def test_exploracion_antes_de_la_consulta(entorno):
    # RF-24: duda sobre un valor → valores_columna; después la consulta con el valor exacto
    principal = LLMFalso(pasos=[pide(("valores_columna", {"tabla": "membresia", "columna": "estado"})),
                                pide(sql("SELECT count(*) FROM membresia WHERE estado = 'activa'")), fin()],
                         textos=["Hay 85."])
    r = lectura(principal, SOCIO, "¿Cuántas membresías activas tengo?")
    assert entorno.exploraciones == [("membresia", "estado", SOCIO)]  # el usuario sale del estado (RNF-01)
    tool = principal.mensajes_vistos[1][-1]
    assert isinstance(tool, ToolMessage) and tool.name == "valores_columna" and "- activa: 85" in tool.content
    assert [p.herramienta for p in r.pasos] == ["valores_columna", "consulta_sql"]
    assert r.pasos[0].argumentos == {"tabla": "membresia", "columna": "estado"} and r.pasos[0].resumen == "2 valores"
    assert r.ruta == "datos" and r.fuentes == ["base de datos"]  # la exploración no es una fuente
    assert r.nodos.count("ejecutar_herramientas") == 2


def test_describir_tabla_y_error_de_exploracion(entorno, monkeypatch):
    monkeypatch.setattr("app.db.esquema.describir_tabla",
                        lambda tabla, u: ResultadoExploracion(error="La tabla 'socio' no existe o no está permitida"))
    principal = LLMFalso(pasos=[pide(("describir_tabla", {"tabla": "socio"})), fin()])
    r = lectura(principal, SOCIO)
    assert principal.mensajes_vistos[1][-1].content.startswith("ERROR: La tabla 'socio'")  # RNF-05
    assert r.pasos[0].resumen.startswith("error:") and r.ruta == "lectura"


def test_el_resultado_vuelve_al_agente_como_tool_message(entorno):
    principal = LLMFalso(pasos=[pide(sql()), fin()], textos=["ok"])
    lectura(principal)
    tool = principal.mensajes_vistos[1][-1]
    assert isinstance(tool, ToolMessage) and tool.name == "consulta_sql" and '{"n": 1}' in tool.content
    assert tool.tool_call_id == principal.mensajes_vistos[1][-2].tool_calls[0]["id"]


def test_una_herramienta_documentos_con_citas_desde_metadatos(entorno):
    principal = LLMFalso(pasos=[pide(docs("vigencia del apto")), fin()],
                         textos=["Dura 12 meses [DOC-04 §1, p. 1]. Además [DOC-09 §3, p. 1]."])
    r = lectura(principal, SOCIO, "¿Cuánto dura el apto médico?")
    assert r.ruta == "documentos" and r.herramientas == ["busqueda_documentos"]
    assert entorno.busquedas == ["vigencia del apto"] and r.sql is None
    assert r.pasos[0].resumen == "1 fragmento (DOC-04)"
    assert r.citas == ["DOC-04 §1"]  # la cita inventada (DOC-09) no entra
    assert r.fuentes == ["Manual de Salud, Apto Médico y Rutinas"]  # el título, sin secciones (RF-19)
    assert "[DOC" not in r.texto and r.texto.startswith("Dura 12 meses. Además.")  # RF-10
    assert r.texto.endswith("*Fuente: Manual de Salud, Apto Médico y Rutinas*")


def test_dos_herramientas_encadenadas(entorno):
    # CA-30: primero el motivo del rechazo en la base y después cómo se resuelve en los documentos
    principal = LLMFalso(pasos=[pide(sql("SELECT motivo")), pide(docs("cuota vencida cómo se resuelve")), fin()],
                         textos=["Respuesta [DOC-04 §1, p. 1]"])
    r = lectura(principal, mensaje="¿Por qué le rechazaron el ingreso…?")
    assert r.ruta == "hibrida" and set(r.herramientas) == {"consulta_sql", "busqueda_documentos"}
    assert [p.herramienta for p in r.pasos] == ["consulta_sql", "busqueda_documentos"]
    assert r.nodos.count("ejecutar_herramientas") == 2 and r.nodos.count("sintetizar") == 1
    assert r.fuentes == ["base de datos", "Manual de Salud, Apto Médico y Rutinas"] and r.citas == ["DOC-04 §1"]
    assert "[DOC" not in r.texto


def test_dos_herramientas_en_paralelo_cuentan_como_un_paso(entorno):
    principal = LLMFalso(pasos=[pide(sql(), docs()), fin()], textos=["Respuesta [DOC-04 §1, p. 1]"])
    r = lectura(principal, SOCIO, "Con mi apto médico, ¿qué clases me recomiendan?")
    assert r.ruta == "hibrida" and r.nodos.count("ejecutar_herramientas") == 1
    assert len(r.pasos) == 2 and entorno.lecturas == ["SELECT 1"] and entorno.busquedas == ["vigencia del apto"]
    assert len([m for m in principal.mensajes_vistos[1] if isinstance(m, ToolMessage)]) == 2


def test_error_de_sql_corregido_en_el_paso_siguiente(entorno):
    def resultado(consulta):
        if consulta == "SELECT x":
            return ResultadoLectura(sql=consulta, error="Error de la base de datos: no existe la columna x")
        return ResultadoLectura(sql=consulta, columnas=["n"], filas=[{"n": 2}], total_filas=1)

    entorno.resultado_sql = resultado
    principal = LLMFalso(pasos=[pide(sql("SELECT x")), pide(sql("SELECT 2")), fin()], textos=["Son 2."])
    r = lectura(principal)
    assert principal.mensajes_vistos[1][-1].content.startswith("ERROR: Error de la base de datos")  # RNF-05
    assert [c.ok for c in r.consultas] == [False, True] and r.pasos[0].resumen.startswith("error:")
    assert r.sql == "SELECT 2" and r.filas == [{"n": 2}]  # la última consulta exitosa
    assert r.fuentes == ["base de datos"] and not r.error


def test_sql_que_siempre_falla_corta_en_el_limite_y_responde_comprensible(entorno):
    entorno.resultado_sql = ResultadoLectura(sql="SELECT x", error="Error de la base de datos: no existe x")
    principal = LLMFalso(pasos=[pide(sql("SELECT x"))])
    r = lectura(principal)
    assert r.nodos.count("ejecutar_herramientas") == MAX_PASOS_AGENTE
    assert r.nodos.count("agente") == MAX_PASOS_AGENTE + 1
    assert "no existe" not in r.texto and "No pude obtener" in r.texto  # RF-23, sin traza técnica
    assert "texto" not in principal.llamadas  # no se llama al LLM para sintetizar
    assert r.ruta == "datos" and r.sql == "SELECT x"


def test_corte_en_max_pasos_aunque_el_agente_pida_mas(entorno):
    principal = LLMFalso(pasos=[pide(docs())], textos=["Dura 12 meses [DOC-04 §1, p. 1]."])
    r = lectura(principal)
    assert len(entorno.busquedas) == MAX_PASOS_AGENTE  # la última tanda de tool_calls no se ejecuta (RF-24)
    assert principal.llamadas.count("agente") == MAX_PASOS_AGENTE + 1 and principal.llamadas[-1] == "texto"
    assert r.nodos[-1] == "sintetizar" and r.ruta == "documentos"
    assert len(r.fragmentos) == 1  # los fragmentos repetidos no se acumulan (por chunk_id)


def test_operacion_socio_rechazada_en_una_lectura(entorno):
    principal = LLMFalso(pasos=[pide(("operacion_socio", {"dni": "21899127", "accion": "baja"})), fin()])
    r = lectura(principal)
    tool = principal.mensajes_vistos[1][-1]
    assert isinstance(tool, ToolMessage) and tool.content.startswith("ERROR:") and "operacion_socio" in tool.content
    assert entorno.ejecuciones == [] and entorno.lecturas == [] and r.herramientas == [] and r.pasos == []
    assert r.ruta == "lectura" and "No encontré información" in r.texto
    assert "extraer_operacion" not in r.nodos and principal.llamadas == ["agente", "agente"]


def test_herramienta_inventada_rechazada(entorno):
    principal = LLMFalso(pasos=[pide(("borrar_socio", {"dni": "21899127"})), fin()])
    r = lectura(principal)
    assert principal.mensajes_vistos[1][-1].content.startswith("ERROR: la herramienta 'borrar_socio'")
    assert r.herramientas == [] and entorno.ejecuciones == []


def test_sin_herramientas_ruta_lectura(entorno):
    principal = LLMFalso(pasos=[fin()])
    r = lectura(principal, idioma="en")
    assert r.ruta == "lectura" and r.herramientas == [] and r.fuentes == []
    assert "couldn't find" in r.texto and principal.llamadas == ["agente"]  # mensaje fijo, sin sintetizar con LLM


@pytest.mark.parametrize("herramientas,ruta", [
    (["consulta_sql"], "datos"), (["busqueda_documentos"], "documentos"),
    (["busqueda_documentos", "consulta_sql"], "hibrida"), ([], "lectura"),
])
def test_ruta_final_derivada_de_las_herramientas(herramientas, ruta):
    from app.agent.nodos.sintetizar import ruta_de_lectura

    assert ruta_de_lectura(herramientas) == ruta


def test_sql_oculta_para_el_socio(entorno):
    r = lectura(LLMFalso(pasos=[pide(sql("SELECT * FROM mis_pagos"), docs()), fin()], textos=["ok"]), SOCIO,
                "¿Cuándo vence mi cuota?")
    assert r.sql is None and r.filas == [{"n": 1}]  # RF-56 (CA-83)
    assert all(c.sql is None for c in r.consultas)
    assert r.pasos[0].argumentos == {} and r.pasos[1].argumentos == {"consulta": "vigencia del apto"}
    assert "mis_pagos" not in r.model_dump_json()
    assert entorno.usuarios_lectura == [SOCIO]


def test_sql_visible_para_el_admin(entorno):
    r = lectura(LLMFalso(pasos=[pide(sql("SELECT nombre FROM sede")), fin()], textos=["ok"]))
    assert r.sql == "SELECT nombre FROM sede" and r.consultas[0].sql == "SELECT nombre FROM sede"  # CA-82
    assert r.pasos[0].argumentos == {"sql": "SELECT nombre FROM sede"}


# ---------------------------------------------------------------------------
# Escritura con confirmación
# ---------------------------------------------------------------------------
def _propuesta() -> PropuestaCambio:
    return PropuestaCambio(operacion="suspension", socio=SocioAfectado(id=3, nombre="Bruno Juárez", dni="21899127"),
                           antes={"estado": "activo"}, despues={"estado": "suspendido"}, motivo="x", datos={"estado": "suspendido"})


def _usar_escritura(entorno, valida=True, pasos=None) -> LLMFalso:
    """Escritura de un administrador. Sin ``pasos``, el agente termina sin proponer (pasa igual por la extracción)."""
    op = Suspension(socio=ReferenciaSocio(dni="21899127"), motivo="x")
    entorno.validacion = (ResultadoValidacion(operacion="suspension", propuesta=_propuesta()) if valida
                          else ResultadoValidacion(operacion="suspension", errores=[Aviso(codigo="falta_motivo", params={"operacion": "suspension"})]))
    principal = LLMFalso(estructurado={"ExtraccionOperacion": ExtraccionOperacion(operacion=op)}, pasos=pasos)
    usar(clasif("escritura"), principal)
    return principal


def test_escritura_valida_se_detiene_en_el_interrupt(entorno):
    _usar_escritura(entorno)
    r = modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127 por x", HOY)
    assert r.propuesta_pendiente and r.propuesta.operacion == "suspension"
    assert r.nodos == ["clasificar", "agente", "extraer_operacion", "validar_operacion"]
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
    assert r.nodos == ["clasificar", "agente", "extraer_operacion", "validar_operacion", "responder_operacion"]
    assert entorno.auditoria == ["rechazada"] and "motivo" in r.texto


# ---------------------------------------------------------------------------
# Escritura preparada por el agente (RF-25, DT-10)
# ---------------------------------------------------------------------------
def test_en_la_escritura_el_agente_ve_operacion_socio(entorno):
    principal = _usar_escritura(entorno, pasos=[pide(propone())])
    modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127 por x", HOY)
    assert principal.herramientas_vistas[-1] == "operacion_socio" and "consulta_sql" in principal.herramientas_vistas
    assert "operacion_socio" in principal.mensajes_vistos[0][0].content  # la tarea de escritura en el prompt


def test_propone_directo_con_el_dni(entorno):
    principal = _usar_escritura(entorno, pasos=[pide(propone())])
    r = modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127 por x", HOY)
    assert r.nodos == ["clasificar", "agente", "extraer_operacion", "validar_operacion"] and r.propuesta_pendiente
    assert [p.herramienta for p in r.pasos] == ["operacion_socio"]
    assert r.pasos[0].resumen == "propuesta lista para confirmar"
    assert principal.llamadas == ["agente", "ExtraccionOperacion"]
    assert "## Datos verificados en la base por el agente\nninguno" in principal.prompts_estructurados[0]


def test_busca_en_la_base_y_propone_con_aclaraciones(entorno):
    # CA-59: el mensaje identifica al socio por su email; el agente busca el DNI y lo pasa como aclaración
    aclaracion = "El socio con email bruno@example.com es Bruno Juárez, DNI 21899127"
    principal = _usar_escritura(entorno, pasos=[pide(sql("SELECT dni FROM socio WHERE email = 'bruno@example.com'")),
                                                pide(propone(aclaracion))])
    mensaje = "Suspendé al socio con email bruno@example.com por x"
    r = modulo_grafo.responder(ADMIN, mensaje, HOY)
    assert r.nodos == ["clasificar", "agente", "ejecutar_herramientas", "agente", "extraer_operacion",
                       "validar_operacion"]
    assert r.propuesta_pendiente and entorno.ejecuciones == []  # nada escrito sin confirmar (RF-46)
    prompt = principal.prompts_estructurados[0]
    assert mensaje in prompt and aclaracion in prompt  # los datos salen del mensaje; la aclaración va aparte (DT-10)
    assert [p.herramienta for p in r.pasos] == ["consulta_sql", "operacion_socio"]
    assert r.pasos[1].argumentos == {"aclaraciones": aclaracion}
    assert set(r.herramientas) == {"consulta_sql", "operacion_socio"} and r.sql  # el admin ve la SQL
    r2 = modulo_grafo.reanudar(ADMIN, r.thread_id, "confirmar")  # el hilo con mensajes del agente se reanuda
    assert len(entorno.ejecuciones) == 1 and "realizada" in r2.texto


def test_operacion_socio_junto_a_otras_herramientas_no_las_ejecuta(entorno):
    _usar_escritura(entorno, pasos=[pide(sql(), propone())])
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    assert entorno.lecturas == [] and r.nodos[-2:] == ["extraer_operacion", "validar_operacion"]


def test_escritura_en_el_limite_de_pasos_pasa_igual_por_la_validacion(entorno):
    principal = _usar_escritura(entorno, pasos=[pide(sql())])
    r = modulo_grafo.responder(ADMIN, "Suspendé…", HOY)
    assert r.nodos.count("ejecutar_herramientas") == MAX_PASOS_AGENTE
    assert r.nodos[-2:] == ["extraer_operacion", "validar_operacion"] and r.propuesta_pendiente
    assert entorno.ejecuciones == [] and "ninguno" in principal.prompts_estructurados[0]


def test_escritura_invalida_propuesta_por_el_agente_no_vuelve_al_agente(entorno):
    principal = _usar_escritura(entorno, valida=False, pasos=[pide(propone())])
    r = modulo_grafo.responder(ADMIN, "Suspendé al socio con DNI 21899127", HOY)
    assert r.nodos == ["clasificar", "agente", "extraer_operacion", "validar_operacion", "responder_operacion"]
    assert r.pasos[0].resumen == "rechazada: falta_motivo" and principal.llamadas.count("agente") == 1


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


def test_cuota_diaria_agotada_mensaje_claro(entorno):
    from app.llm import CuotaDiariaAgotada

    class SinCuota(LLMFalso):
        def with_structured_output(self, esquema):
            raise CuotaDiariaAgotada("sin cuota")

    modelos.rapido = SinCuota()
    r = modulo_grafo.responder(SOCIO, "hola", HOY)
    assert r.error and "límite diario" in r.texto


def test_varias_citas_en_un_corchete():
    from app.agent.nodos.sintetizar import fuentes_citadas

    fr = [Fragmento(doc_id="DOC-04", seccion="9. Preguntas frecuentes — x", pagina=4, texto="t"),
          Fragmento(doc_id="DOC-04", seccion="1. Apto médico", pagina=1, texto="t")]
    assert fuentes_citadas("Dura 12 meses [DOC-04 §9, p. 4; DOC-04 §1, p. 1].", fr) == ["DOC-04 §9", "DOC-04 §1"]


def test_quitar_citas_y_documentos_citados():
    from app.agent.nodos.sintetizar import documentos_citados, quitar_citas

    assert quitar_citas("Dura 12 meses [DOC-04 §9, p. 4] [DOC-04 §1, p. 1]. Renovalo antes [DOC-04 §1, p. 1].") == \
        "Dura 12 meses. Renovalo antes."
    fr = [Fragmento(doc_id="DOC-04", seccion="1. Apto", pagina=1, texto="t", titulo_doc="Manual de Salud"),
          Fragmento(doc_id="DOC-04", seccion="9. FAQ", pagina=4, texto="t", titulo_doc="Manual de Salud"),
          Fragmento(doc_id="DOC-02", seccion="9. Congelamiento", pagina=3, texto="t")]
    assert documentos_citados(["DOC-04 §9", "DOC-02 §9", "DOC-04 §1"], fr) == ["Manual de Salud", "DOC-02"]


def test_clasificar_usa_el_modelo_principal_si_el_rapido_esta_saturado(entorno, monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)

    class Saturado(LLMFalso):
        def with_structured_output(self, esquema):
            class _Falla:
                def invoke(self, prompt):
                    raise RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand")
            return _Falla()

    principal = LLMFalso(estructurado={"Clasificacion": clasif("directa", respuesta_directa="hola")})
    modelos.rapido, modelos.principal = Saturado(), principal
    r = modulo_grafo.responder(SOCIO, "hola", HOY)
    assert not r.error and principal.llamadas == ["Clasificacion"]
