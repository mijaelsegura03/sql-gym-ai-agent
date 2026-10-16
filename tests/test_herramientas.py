"""Herramientas del agente para tool calling (spec técnico §5.7, T-35, T-42, T-43). Corren sin red ni Docker."""

from __future__ import annotations

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent import herramientas as h
from app.auth import Usuario
from app.db.esquema import ResultadoExploracion
from app.db.lectura import ResultadoLectura
from app.rag.buscador import Fragmento


def _esquema(nombre: str) -> dict:
    tool = next(t for t in [*h.HERRAMIENTAS_LECTURA, h.HERRAMIENTA_OPERACION] if t.name == nombre)
    return convert_to_openai_tool(tool)["function"]


ADMIN = Usuario(perfil="admin", id=1, nombre="A", sede_alcance=None, seudonimo="a")
SOCIO = Usuario(perfil="socio", id=2, nombre="S", sede_alcance=1, seudonimo="s")


def test_herramientas_de_lectura_sin_escritura():
    assert [t.name for t in h.HERRAMIENTAS_LECTURA] == ["consulta_sql", "busqueda_documentos", "describir_tabla",
                                                        "valores_columna"]  # RF-24, RF-25
    assert list(h.NOMBRES) == [t.name for t in h.HERRAMIENTAS_LECTURA]


def test_operacion_socio_solo_en_la_escritura_del_admin():
    # RF-25: la herramienta que propone solo se le ofrece al LLM en la ruta escritura de un administrador
    assert h.OPERACION in [t.name for t in h.herramientas_para("escritura", ADMIN)]
    for ruta, usuario in [("lectura", ADMIN), ("escritura", SOCIO), ("lectura", SOCIO)]:
        assert h.OPERACION not in [t.name for t in h.herramientas_para(ruta, usuario)]


def test_argumentos_sin_usuario_ni_perfil():
    # El LLM solo elige la consulta: el usuario, el perfil y la conexión los pone el grafo (RNF-01)
    assert set(_esquema("consulta_sql")["parameters"]["properties"]) == {"sql"}
    assert set(_esquema("busqueda_documentos")["parameters"]["properties"]) == {"consulta"}
    assert _esquema("consulta_sql")["parameters"]["required"] == ["sql"]
    assert set(_esquema("describir_tabla")["parameters"]["properties"]) == {"tabla"}
    assert set(_esquema("valores_columna")["parameters"]["properties"]) == {"tabla", "columna"}
    # operacion_socio no recibe la operación: la arma extraer_operacion desde el mensaje (DT-10)
    assert set(_esquema("operacion_socio")["parameters"]["properties"]) == {"aclaraciones"}
    assert not _esquema("operacion_socio")["parameters"].get("required")


def test_descripciones_de_exploracion_y_propuesta():
    assert "SOLO si" in _esquema("describir_tabla")["description"]  # solo ante una duda (DT-11)
    assert "SOLO si" in _esquema("valores_columna")["description"]
    assert "NO ejecuta nada" in _esquema("operacion_socio")["description"]


def test_descripciones_con_fuentes_y_regla_de_fuente_de_verdad():
    sql, documentos = _esquema("consulta_sql")["description"], _esquema("busqueda_documentos")["description"]
    assert "fuente de verdad" in sql.lower() and "precios vigentes" in sql
    assert "fuente de verdad" in documentos.lower() and "DOC-01" in documentos and "DOC-04" in documentos
    assert "castellano" in documentos  # §8.3


def test_resultado_sql_con_filas():
    r = ResultadoLectura(sql="SELECT 1", columnas=["sede", "socios"],
                         filas=[{"sede": "Sede Centro", "socios": 52}, {"sede": "Sede Norte", "socios": 31}],
                         total_filas=2)
    texto = h.texto_resultado_sql(r)
    assert texto.startswith("Columnas: sede, socios\nTotal de filas: 2")
    assert '{"sede": "Sede Centro", "socios": 52}' in texto
    assert h.resumen_sql(r) == "2 filas"


def test_resultado_sql_truncado_informa_el_total():
    r = ResultadoLectura(sql="SELECT 1", columnas=["n"], filas=[{"n": i} for i in range(50)], total_filas=120)
    assert "Total de filas: 120 (se muestran las primeras 50)" in h.texto_resultado_sql(r)


def test_resultado_sql_con_error():
    r = ResultadoLectura(sql="DELETE FROM socio", error="Consulta rechazada por el validador: solo SELECT")
    assert h.texto_resultado_sql(r) == "ERROR: Consulta rechazada por el validador: solo SELECT"
    assert h.resumen_sql(r).startswith("error: ")


def test_resultado_busqueda_con_etiquetas():
    fr = [Fragmento(doc_id="DOC-04", seccion="1. Apto médico", pagina=1, texto="DOC-04 — Manual — 1. Apto\nDura 12 meses."),
          Fragmento(doc_id="DOC-01", seccion="4. Motivos de rechazo", pagina=2, texto="enc\nCuota vencida.")]
    texto = h.texto_resultado_busqueda(fr)
    assert texto.startswith("[DOC-04 §1, p. 1]\nDura 12 meses.")  # sin el encabezado del chunk
    assert "[DOC-01 §4, p. 2]\nCuota vencida." in texto
    assert h.resumen_busqueda(fr) == "2 fragmentos (DOC-04, DOC-01)"


def test_resultado_busqueda_vacia():
    assert h.texto_resultado_busqueda([]) == "SIN RESULTADOS"
    assert h.resumen_busqueda([]) == "sin resultados"


def test_las_herramientas_no_se_ejecutan_fuera_del_grafo():
    tool = h.HERRAMIENTAS_LECTURA[0]
    try:
        tool.invoke({"sql": "SELECT 1"})
    except RuntimeError as e:
        assert "las ejecuta el grafo" in str(e)
    else:
        raise AssertionError("la herramienta no debería ejecutarse sin el usuario del estado")


def test_ejecucion_con_el_usuario_que_recibe(monkeypatch):
    vistos = []
    monkeypatch.setattr(h, "ejecutar_lectura", lambda sql, usuario: vistos.append((sql, usuario)) or
                        ResultadoLectura(sql=sql, columnas=["n"], filas=[{"n": 1}], total_filas=1))
    r = h.consulta_sql("SELECT 1", "usuario-de-la-sesion")
    assert r.ok and vistos == [("SELECT 1", "usuario-de-la-sesion")]


def test_resultado_exploracion():
    ok = ResultadoExploracion(texto="Tabla socio (120 filas visibles)", resumen="11 columnas")
    assert h.texto_resultado_exploracion(ok) == "Tabla socio (120 filas visibles)"
    assert h.resumen_exploracion(ok) == "11 columnas"
    error = ResultadoExploracion(error="La tabla 'x' no existe")
    assert h.texto_resultado_exploracion(error) == "ERROR: La tabla 'x' no existe"
    assert h.resumen_exploracion(error) == "error: La tabla 'x' no existe"
