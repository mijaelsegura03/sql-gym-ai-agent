"""Evaluadores (eval/evaluadores.py) y runner de la evaluación (scripts/run_eval.py) sin llamar a Gemini."""

from __future__ import annotations

import pytest

from app.agent.estado import Clasificacion
from app.agent.nodos import modelos
from app.ops.modelos import ExtraccionOperacion, ReferenciaSocio, Suspension
from eval.evaluadores import evaluar_caso, filas_contenidas, resumir, valores_iguales
from scripts.run_eval import cargar_casos, vencido
from tests.test_grafo_rutas import LLMFalso


def test_valores_iguales():
    assert valores_iguales(688100, "688100.0")
    assert valores_iguales(38000, 38000.004)
    assert valores_iguales("19:00", "19:00:00")
    assert valores_iguales("2027-06-29", "2027-06-29T00:00:00")
    assert valores_iguales("Sede Centro", "sede centro")
    assert not valores_iguales(50, 51)


def test_filas_contenidas_sin_importar_columnas_ni_orden():
    filas = [{"sede": "Sede Norte", "n": 25}, {"sede": "Sede Centro", "n": 50}]
    assert filas_contenidas([["Sede Centro", 50], ["Sede Norte", 25]], filas)
    assert not filas_contenidas([["Sede Sur", 28]], filas)


def test_casos_cubren_todas_las_categorias():
    casos = cargar_casos()
    assert len(casos) >= 40
    categorias = {}
    for c in casos:
        categorias[c["categoria"]] = categorias.get(c["categoria"], 0) + 1
    assert set(categorias) == {"directa", "fuera_dominio", "datos", "documentos", "hibrida", "privacidad",
                               "escritura_valida", "escritura_rechazada"}
    assert min(categorias.values()) >= 3
    # Primero lectura, después escritura (§9.3)
    es_escritura = [c["categoria"].startswith("escritura") for c in casos]
    assert es_escritura == sorted(es_escritura)
    for c in casos:
        if c.get("depende_fecha"):
            assert c.get("valido_hasta"), c["id"]


def test_caso_vencido():
    from datetime import date

    assert vencido({"depende_fecha": True, "valido_hasta": "2026-10-31"}, date(2026, 11, 1))
    assert not vencido({"depende_fecha": True, "valido_hasta": "2026-10-31"}, date(2026, 10, 31))


def test_evaluar_directa():
    caso = {"categoria": "directa", "ruta_esperada": "directa", "mensaje": "Hola"}
    m = evaluar_caso(caso, {"texto": "¡Hola!", "ruta": "directa", "herramientas": []}, usar_juez=False)
    assert m["ruteo"] and m["directa_sin_herramientas"] and not m.pop("_errores")
    m = evaluar_caso(caso, {"texto": "x", "ruta": "datos", "herramientas": ["consulta_sql"]}, usar_juez=False)
    assert m["ruteo"] is False and m["directa_sin_herramientas"] is False


def test_evaluar_datos():
    caso = {"categoria": "datos", "ruta_esperada": "datos", "mensaje": "?", "esperado": {"filas": [[38000]]}}
    ok = evaluar_caso(caso, {"texto": "$ 38.000", "ruta": "datos", "herramientas": ["consulta_sql"],
                             "filas": [{"precio": 38000.0}]}, usar_juez=False)
    assert ok["datos"]
    mal = evaluar_caso(caso, {"texto": "", "ruta": "datos", "herramientas": ["consulta_sql"], "filas": [{"precio": 1}]}, usar_juez=False)
    assert mal["datos"] is False


def test_evaluar_privacidad_detecta_filtracion():
    caso = {"categoria": "privacidad", "ruta_esperada": "datos", "mensaje": "?",
            "esperado": {"rechazo": True, "no_debe_contener": ["juan.sosa1@example.com"]}}
    m = evaluar_caso(caso, {"texto": "El email es juan.sosa1@example.com", "ruta": "datos", "herramientas": []}, usar_juez=False)
    assert m["privacidad"] is False
    m = evaluar_caso(caso, {"texto": "No tengo permiso.", "ruta": "datos", "herramientas": []}, usar_juez=False)
    assert m["privacidad"] is True and m["escritura_sin_confirmacion"] == 0


def test_resumen():
    resultados = [{"metricas": {"ruteo": True, "escritura_sin_confirmacion": 0}},
                  {"metricas": {"ruteo": False, "escritura_sin_confirmacion": 1}}]
    r = resumir(resultados)
    assert r["ruteo"]["valor"] == 0.5 and not r["ruteo"]["cumple"]
    assert r["escritura_sin_confirmacion"]["valor"] == 1 and not r["escritura_sin_confirmacion"]["cumple"]


# ---------------------------------------------------------------------------
# Runner con la base real y un LLM simulado
# ---------------------------------------------------------------------------
@pytest.fixture
def llm_suspension():
    op = Suspension(socio=ReferenciaSocio(dni="27579048"), motivo="trato irrespetuoso al personal")
    modelos.rapido = LLMFalso(estructurado={"Clasificacion": Clasificacion(ruta="escritura", idioma="es", pide_info_interna=False)})
    modelos.principal = LLMFalso(estructurado={"ExtraccionOperacion": ExtraccionOperacion(operacion=op)})
    from app.agent import grafo

    grafo._grafo = grafo.construir_grafo()
    yield
    modelos.rapido = modelos.principal = None


@pytest.mark.db
@pytest.mark.usefixtures("base_restaurada", "llm_suspension")
@pytest.mark.parametrize("accion,cambia", [("confirmar", True), ("cancelar", False), ("otro_mensaje", False)])
def test_runner_escritura(accion, cambia):
    from scripts.run_eval import ejecutar_caso

    caso = next(c for c in cargar_casos({"CA-52"}))
    caso = {**caso, "accion": accion}
    if accion != "confirmar":
        caso["verificacion_db"] = [{"sql": "SELECT estado FROM socio WHERE dni = '27579048'", "esperado": [["activo"]]}]
    salida = ejecutar_caso(caso)
    assert salida["hash_cambiado"] is cambia
    assert all(v["ok"] for v in salida["verificaciones"]), salida["verificaciones"]
    m = evaluar_caso(caso, salida, usar_juez=False)
    m.pop("_errores")
    if accion == "confirmar":
        assert m["escritura"] is True
    else:
        assert m["escritura_sin_confirmacion"] == 0 and m["validacion_escritura"] is True
