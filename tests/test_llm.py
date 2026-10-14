"""Reintentos ante errores de Gemini, sin llamar a la API (T-09)."""

from __future__ import annotations

import pytest

from app import llm


class Error429(Exception):
    code = 429


def test_reintenta_429_con_espera_creciente():
    esperas, intentos = [], {"n": 0}

    def fn():
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise Error429("RESOURCE_EXHAUSTED: GenerateRequestsPerMinutePerProjectPerModel")
        return "ok"

    assert llm.con_reintentos(fn, max_reintentos=5, dormir=esperas.append) == "ok"
    assert len(esperas) == 2 and esperas[1] > esperas[0]


def test_respeta_el_maximo_de_reintentos():
    esperas = []

    def fn():
        raise Error429("503 UNAVAILABLE: model overloaded")

    with pytest.raises(Error429):
        llm.con_reintentos(fn, max_reintentos=2, dormir=esperas.append)
    assert len(esperas) == 2


def test_cuota_diaria_no_se_reintenta(monkeypatch):
    monkeypatch.setattr(llm, "cuota_diaria_agotada", False)
    esperas = []

    def fn():
        raise Error429("RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    with pytest.raises(llm.CuotaDiariaAgotada):
        llm.con_reintentos(fn, max_reintentos=5, dormir=esperas.append)
    assert esperas == [] and llm.cuota_diaria_agotada


def test_error_no_reintentable_se_propaga():
    with pytest.raises(ValueError):
        llm.con_reintentos(lambda: (_ for _ in ()).throw(ValueError("schema inválido")), dormir=lambda s: None)


class Error503(Exception):
    code = 503


def test_saturacion_pasa_al_respaldo_sin_esperar():
    esperas, llamadas = [], []

    def principal():
        llamadas.append("principal")
        raise Error503("503 UNAVAILABLE: This model is currently experiencing high demand")

    def respaldo():
        llamadas.append("respaldo")
        return "ok"

    assert llm.con_reintentos(principal, max_reintentos=5, dormir=esperas.append, respaldo=respaldo) == "ok"
    assert llamadas == ["principal", "respaldo"] and esperas == []


def test_con_respaldo_alterna_y_espera_al_volver_al_primero():
    esperas, llamadas = [], []

    def falla(nombre):
        def fn():
            llamadas.append(nombre)
            raise Error503("503 UNAVAILABLE")
        return fn

    with pytest.raises(Error503):
        llm.con_reintentos(falla("a"), max_reintentos=3, dormir=esperas.append, respaldo=falla("b"))
    assert llamadas == ["a", "b", "a", "b"] and len(esperas) == 1


def test_cuota_diaria_del_principal_sigue_con_el_respaldo(monkeypatch):
    monkeypatch.setattr(llm, "cuota_diaria_agotada", False)

    def principal():
        raise Error429("RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    assert llm.con_reintentos(principal, max_reintentos=5, dormir=lambda s: None, respaldo=lambda: "ok") == "ok"
    assert not llm.cuota_diaria_agotada


def test_cuota_diaria_de_ambos_modelos(monkeypatch):
    monkeypatch.setattr(llm, "cuota_diaria_agotada", False)

    def sin_cuota():
        raise Error429("RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    with pytest.raises(llm.CuotaDiariaAgotada):
        llm.con_reintentos(sin_cuota, max_reintentos=5, dormir=lambda s: None, respaldo=sin_cuota)
    assert llm.cuota_diaria_agotada
