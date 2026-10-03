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
