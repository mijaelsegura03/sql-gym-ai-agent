"""Corre la evaluación completa del agente (spec técnico §9.3; RF-73 a RF-76).

1. Restaura la base de trabajo desde la plantilla (``--reset`` además recrea la plantilla).
2. Ejecuta cada caso de ``eval/casos.yaml``: primero los de lectura y después los de escritura;
   antes y después de cada caso de escritura se restaura la base (RF-74). Para cada caso se
   identifica al usuario, se invoca el agente y, si corresponde, se reanuda con la acción indicada
   (confirmar, cancelar, escribir "sí" u otro mensaje). Se calculan los hashes de ``socio`` y
   ``membresia`` antes y después, y las consultas de verificación.
3. Respeta ``EVAL_RPM`` (requests por minuto a Gemini).
4. Calcula las métricas (``eval/evaluadores.py``) y escribe ``eval/resultados/<fecha>_<commit>.md``
   (y un ``.json`` con el detalle) (RF-73, RF-75).
5. Si LangSmith está configurado, sube el dataset ``gimnasio-eval`` y registra la corrida como
   *experiment* (RF-75); las trazas de cada caso quedan en el proyecto de LangSmith.

El progreso se guarda en ``eval/resultados/en_curso.json`` después de cada caso. Si la corrida se
corta (Ctrl+C, cuota diaria agotada, Gemini saturado), ``--reanudar`` la continúa: saltea los casos
ya evaluados y vuelve a correr los que terminaron con un error del agente.

Uso::

    python scripts/run_eval.py [--reset] [--casos CA-10,CA-20] [--sin-juez] [--sin-langsmith] [--reanudar]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import yaml  # noqa: E402

from app.config import configurar_trazas, get_settings  # noqa: E402

CASOS = RAIZ / "eval" / "casos.yaml"
RESULTADOS = RAIZ / "eval" / "resultados"
PROGRESO = RESULTADOS / "en_curso.json"
DATASET = "gimnasio-eval"
CATEGORIAS_ESCRITURA = ("escritura_valida", "escritura_rechazada")
MENSAJE_OTRO = "Gracias"


def cargar_casos(filtro: set[str] | None = None) -> list[dict]:
    """Casos de ``casos.yaml``: primero los de lectura y después los de escritura (§9.3 paso 4)."""
    casos = yaml.safe_load(CASOS.read_text(encoding="utf-8"))
    if filtro:
        casos = [c for c in casos if c["id"] in filtro]
    return sorted(casos, key=lambda c: c["categoria"] in CATEGORIAS_ESCRITURA)


def commit_actual() -> str:
    """Commit corto de ``HEAD``, con ``-dirty`` si el código evaluado tiene cambios sin commitear."""
    try:
        return subprocess.run(["git", "describe", "--always", "--abbrev=7", "--dirty", "--exclude=*"], cwd=RAIZ,
                              capture_output=True, text=True, timeout=10).stdout.strip() or "sin-commit"
    except Exception:  # noqa: BLE001
        return "sin-commit"


def hash_tablas() -> str:
    """Hash del contenido de ``socio`` y ``membresia`` (para detectar escrituras sin confirmación)."""
    from app.db.conexion import conectar

    with conectar("superusuario") as conn:
        return conn.execute(
            """SELECT md5((SELECT coalesce(string_agg(t::text, '|' ORDER BY t.id), '') FROM socio t) || '#' ||
                          (SELECT coalesce(string_agg(t::text, '|' ORDER BY t.id), '') FROM membresia t)) AS h"""
        ).fetchone()["h"]


def verificar(consultas: list[dict]) -> list[dict]:
    """Ejecuta las consultas de verificación del caso y compara con el esperado."""
    from app.db.conexion import conectar
    from eval.evaluadores import valores_iguales

    salida = []
    with conectar("superusuario") as conn:
        for v in consultas:
            filas = [list(f.values()) for f in conn.execute(v["sql"]).fetchall()]
            filas_json = [[x.isoformat() if hasattr(x, "isoformat") else x for x in f] for f in filas]
            ok = len(filas) == len(v["esperado"]) and all(
                len(f) == len(e) and all(valores_iguales(a, b) for a, b in zip(e, f)) for f, e in zip(filas, v["esperado"]))
            salida.append({"sql": v["sql"], "esperado": v["esperado"], "obtenido": filas_json, "ok": ok})
    return salida


def ejecutar_caso(caso: dict) -> dict:
    """Identifica al usuario, invoca el agente, aplica la acción y verifica la base. Devuelve la salida del caso."""
    from app.agent import grafo
    from app.auth import identificar

    usuario = identificar(caso["perfil"], caso["dni_usuario"])
    if usuario is None:
        return {"error_ejecucion": f"usuario {caso['dni_usuario']} no válido para el perfil {caso['perfil']}"}
    from app.llm import segundos_esperados_por_limite

    antes = hash_tablas()
    t0, espera0 = time.perf_counter(), segundos_esperados_por_limite()
    r = grafo.responder(usuario, caso["mensaje"])
    final = r
    ejecutada = False
    accion = caso.get("accion")
    if r.propuesta_pendiente:
        if accion == "confirmar":
            final = grafo.reanudar(usuario, r.thread_id, "confirmar")
            ejecutada = bool(final.resultado_operacion and final.resultado_operacion.ok)
        elif accion == "cancelar":
            final = grafo.reanudar(usuario, r.thread_id, "cancelar")
        elif accion in ("escribir_si", "otro_mensaje"):
            grafo.descartar(usuario, r.thread_id)  # la interfaz descarta la propuesta al llegar otro mensaje
            final = grafo.responder(usuario, "sí" if accion == "escribir_si" else MENSAJE_OTRO)
        else:  # caso de lectura que armó una propuesta por error: se descarta
            grafo.descartar(usuario, r.thread_id)
    # Sin las esperas del limitador de la evaluación: es el tiempo que vería el usuario (RNF-04)
    duracion = time.perf_counter() - t0 - (segundos_esperados_por_limite() - espera0)
    verificaciones = verificar(caso.get("verificacion_db") or [])
    despues = hash_tablas()
    propuesta = r.propuesta
    return {
        "texto": r.texto if final is r else f"{r.texto}\n\n{final.texto}",
        "ruta": r.ruta,
        "herramientas": r.herramientas,
        "nodos": r.nodos + (final.nodos if final is not r else []),
        "fuentes": r.fuentes,
        "citas": r.citas,
        "sql": r.sql,
        "filas": r.filas,
        "total_filas": r.total_filas,
        "consultas": [c.model_dump(mode="json") for c in r.consultas],
        "pasos": [p.model_dump(mode="json") for p in r.pasos],
        "fragmentos": [{"etiqueta": f.etiqueta, "texto": f.texto, "distancia": f.distancia} for f in r.fragmentos],
        "propuesta": ({"operacion": propuesta.operacion, "advertencias": [a.codigo for a in propuesta.advertencias],
                       "efectos": [e.codigo for e in propuesta.efectos]} if propuesta else None),
        "ejecutada": ejecutada,
        "verificaciones": verificaciones,
        "hash_cambiado": antes != despues,
        "duracion_seg": round(duracion, 2),
        "trace_id": r.trace_id,
        "error_ejecucion": "respuesta de error del agente" if r.error else None,
    }


def cuota_agotada() -> bool:
    """True si alguna llamada de este proceso chocó con la cuota diaria (ver ``app/llm.py``)."""
    from app import llm

    return llm.cuota_diaria_agotada


def vencido(caso: dict, hoy: date) -> bool:
    """True si el caso depende de la fecha y su esperado ya no es válido (S-05)."""
    return bool(caso.get("depende_fecha") and caso.get("valido_hasta") and hoy > date.fromisoformat(caso["valido_hasta"]))


def guardar_progreso(progreso: dict) -> None:
    """Escribe el progreso de la corrida (después de cada caso) para poder reanudarla con ``--reanudar``."""
    RESULTADOS.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESO.with_suffix(".tmp")
    tmp.write_text(json.dumps(progreso, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(PROGRESO)


def cargar_progreso() -> dict | None:
    """Progreso de la última corrida sin terminar. Descarta los casos con error del agente, que se repiten."""
    if not PROGRESO.exists():
        return None
    progreso = json.loads(PROGRESO.read_text(encoding="utf-8"))
    progreso["resultados"] = [r for r in progreso["resultados"] if not r["salida"].get("error_ejecucion")]
    return progreso


def correr(casos: list[dict], usar_juez: bool, progreso: dict) -> tuple[list[dict], bool]:
    """Evalúa los casos que todavía no están en ``progreso`` y lo guarda después de cada uno.

    Devuelve los resultados en el orden de ``casos`` y si la corrida se completó (``False`` si se
    cortó por la cuota diaria).
    """
    from eval.evaluadores import evaluar_caso
    from scripts.reset_db import restaurar

    hoy = date.today()
    hechos = {r["caso"]["id"]: r for r in progreso["resultados"]}

    def registrar(resultado: dict) -> None:
        hechos[resultado["caso"]["id"]] = resultado
        progreso["resultados"].append(resultado)
        guardar_progreso(progreso)

    completa = True
    for i, caso in enumerate(casos, 1):
        if caso["id"] in hechos:
            print(f"[{i}/{len(casos)}] {caso['id']:<12} ya evaluado")
            continue
        if vencido(caso, hoy):
            print(f"[{i}/{len(casos)}] {caso['id']:<12} OMITIDO (esperado válido hasta {caso['valido_hasta']})")
            registrar({"caso": caso, "salida": {}, "metricas": {}, "omitido": True})
            continue
        escritura = caso["categoria"] in CATEGORIAS_ESCRITURA
        if escritura:
            restaurar()
        try:
            salida = ejecutar_caso(caso)
        except Exception as e:  # noqa: BLE001
            salida = {"error_ejecucion": f"{type(e).__name__}: {e}"}
        if cuota_agotada():
            print("Se agotó la cuota diaria gratuita de Gemini: la evaluación se corta acá (sin costo). "
                  "Cuando se renueve la cuota, seguila con --reanudar.")
            if escritura:
                restaurar()
            completa = False
            break
        if escritura:
            restaurar()
        metricas = evaluar_caso(caso, salida, usar_juez=usar_juez)
        errores = metricas.pop("_errores", [])
        ok = not errores and all(v in (None, True, 0) for v in metricas.values())
        registrar({"caso": caso, "salida": salida, "metricas": metricas, "errores": errores, "ok": ok})
        print(f"[{i}/{len(casos)}] {caso['id']:<12} {'OK ' if ok else 'FALLA'} {salida.get('duracion_seg', '-')} s"
              + (f"  ← {'; '.join(errores)[:160]}" if errores else ""), flush=True)
    return [hechos[c["id"]] for c in casos if c["id"] in hechos], completa


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f} %"


def _primer_mensaje(nodos: list[str]) -> list[str]:
    """Recorrido del mensaje del caso, sin el de la reanudación (que repite el hilo desde ``clasificar``)."""
    return nodos[:nodos.index("clasificar", 1)] if nodos.count("clasificar") > 1 else nodos


def resumen_agente(evaluados: list[dict]) -> list[str]:
    """Promedios del agente, separados en lectura y escritura (DT-08 del spec técnico)."""
    lineas = []
    for nombre, es_ruta in (("lectura", lambda n: "sintetizar" in n), ("escritura", lambda n: "extraer_operacion" in n)):
        casos = [(_primer_mensaje(r["salida"].get("nodos") or []), r["salida"].get("pasos") or []) for r in evaluados]
        casos = [(n, p) for n, p in casos if "agente" in n and es_ruta(n)]
        if not casos:
            lineas.append(f"- Agente en {nombre}: sin casos")
            continue
        rondas = statistics.mean(n.count("ejecutar_herramientas") for n, _ in casos)
        llamadas = statistics.mean(n.count("agente") for n, _ in casos)
        invocaciones = statistics.mean(sum(p["herramienta"] != "operacion_socio" for p in pasos) for _, pasos in casos)
        maximo = max(n.count("ejecutar_herramientas") for n, _ in casos)
        exploracion = sum(any(p["herramienta"] in ("describir_tabla", "valores_columna") for p in pasos)
                          for _, pasos in casos)
        extra = (f"; propuso con `operacion_socio` en {sum(any(p['herramienta'] == 'operacion_socio' for p in pasos) for _, pasos in casos)}"
                 if nombre == "escritura" else "")
        lineas.append(f"- Agente en {nombre} ({len(casos)} casos): {rondas:.2f} rondas de herramientas (máximo "
                      f"{maximo}), {invocaciones:.2f} invocaciones de herramientas de lectura y {llamadas:.2f} "
                      f"llamadas a `agente` por mensaje en promedio; exploró el esquema en {exploracion}{extra}")
    return lineas


def reporte(resultados: list[dict], resumen: dict, commit: str, inicio: datetime, experimento: str | None) -> str:
    s = get_settings()
    evaluados = [r for r in resultados if not r.get("omitido")]
    tiempos = [r["salida"].get("duracion_seg") for r in evaluados if r["salida"].get("duracion_seg") is not None]
    p90 = statistics.quantiles(tiempos, n=10)[-1] if len(tiempos) >= 2 else (tiempos[0] if tiempos else None)
    lineas = [
        f"# Reporte de evaluación — {inicio:%d/%m/%Y %H:%M}",
        "",
        f"- Commit: `{commit}`",
        f"- Modelos: rápido `{s.gemini_model_fast}`, principal `{s.gemini_model_main}`, juez `{s.gemini_model_juez}`, embeddings `{s.gemini_embedding_model}`",
        f"- RAG: top-k {s.rag_top_k}, distancia máxima {s.rag_distancia_max}",
        f"- Casos: {len(resultados)} ({len(evaluados)} evaluados, {len(resultados) - len(evaluados)} omitidos por fecha)",
        f"- Casos OK: {sum(1 for r in evaluados if r['ok'])} de {len(evaluados)}",
        f"- Tiempo de respuesta: p90 {p90:.1f} s, mediana {statistics.median(tiempos):.1f} s (objetivo RNF-04: p90 < 15 s; sin las esperas del límite de RPM de la evaluación)"
        if tiempos else "- Tiempo de respuesta: —",
    ]
    lineas += resumen_agente(evaluados)
    if experimento:
        lineas.append(f"- Experimento de LangSmith: `{experimento}`")
    lineas += ["", "## Métricas (§5.8.1 del spec funcional)", "",
               "| Métrica | Valor | Casos | Objetivo | Cumple |", "|---|---|---|---|---|"]
    for m in resumen.values():
        if m["tipo"] == "max":
            valor, objetivo = str(m["valor"]), f"= {int(m['objetivo'])}"
        else:
            valor, objetivo = _pct(m["valor"]), f"≥ {m['objetivo'] * 100:.0f} %"
        lineas.append(f"| {m['nombre']} | {valor} | {m['casos']} | {objetivo} | {'✅' if m['cumple'] else '❌'} |")
    lineas += ["", "## Detalle por caso", "",
               "| Caso | Categoría | Ruta esperada → obtenida | Herramientas | Resultado | Tiempo | Observaciones |",
               "|---|---|---|---|---|---|---|"]
    for r in resultados:
        c, sal = r["caso"], r["salida"]
        if r.get("omitido"):
            lineas.append(f"| {c['id']} | {c['categoria']} | — | — | omitido | — | esperado válido hasta {c['valido_hasta']} |")
            continue
        obs = "; ".join(r.get("errores") or []).replace("|", "/")[:220]
        lineas.append(f"| {c['id']} | {c['categoria']} | {c.get('ruta_esperada', '—')} → {sal.get('ruta', '—')} | "
                      f"{', '.join(sal.get('herramientas') or []) or 'ninguna'} | {'✅' if r['ok'] else '❌'} | "
                      f"{sal.get('duracion_seg', '—')} s | {obs} |")
    lineas += ["", "## Respuestas", ""]
    for r in evaluados:
        c, sal = r["caso"], r["salida"]
        texto = (sal.get("texto") or sal.get("error_ejecucion") or "").replace("\n", " ")
        lineas.append(f"- **{c['id']}** — _{c['mensaje']}_ → {texto[:400]}")
    return "\n".join(lineas) + "\n"


def registrar_en_langsmith(resultados: list[dict], commit: str) -> str | None:
    """Sube el dataset y registra la corrida como experiment de LangSmith (RF-75). Best effort."""
    if not get_settings().trazas_activas:
        return None
    try:
        from langsmith import Client, evaluate

        client = Client()
        if client.has_dataset(dataset_name=DATASET):
            dataset = client.read_dataset(dataset_name=DATASET)
        else:
            dataset = client.create_dataset(DATASET, description="Conjunto de evaluación del agente del gimnasio (eval/casos.yaml)")
        existentes = {e.metadata.get("caso_id"): e for e in client.list_examples(dataset_id=dataset.id) if e.metadata}
        for r in resultados:
            c = r["caso"]
            entradas = {"id": c["id"], "perfil": c["perfil"], "mensaje": c["mensaje"]}
            esperadas = {"ruta": c.get("ruta_esperada"), "esperado": c.get("esperado"), "accion": c.get("accion")}
            if c["id"] in existentes:
                client.update_example(existentes[c["id"]].id, inputs=entradas, outputs=esperadas,
                                      metadata={"caso_id": c["id"], "categoria": c["categoria"]})
            else:
                client.create_example(inputs=entradas, outputs=esperadas, dataset_id=dataset.id,
                                      metadata={"caso_id": c["id"], "categoria": c["categoria"]})
        por_id = {r["caso"]["id"]: r for r in resultados if not r.get("omitido")}

        def objetivo(entradas: dict) -> dict:
            r = por_id.get(entradas["id"], {})
            sal = r.get("salida", {})
            return {"respuesta": sal.get("texto"), "ruta": sal.get("ruta"), "herramientas": sal.get("herramientas"),
                    "trace_id": sal.get("trace_id"), "duracion_seg": sal.get("duracion_seg")}

        def metricas(run, example) -> dict:
            r = por_id.get(example.inputs["id"], {})
            res = [{"key": k, "score": float(v)} for k, v in (r.get("metricas") or {}).items() if v is not None]
            res.append({"key": "ok", "score": 1.0 if r.get("ok") else 0.0})
            return {"results": res}

        ejemplos = [e for e in client.list_examples(dataset_id=dataset.id) if e.inputs.get("id") in por_id]
        exp = evaluate(objetivo, data=ejemplos, evaluators=[metricas], experiment_prefix=f"eval-{commit}",
                       metadata={"commit": commit, "modelo_principal": get_settings().gemini_model_main},
                       max_concurrency=0)
        return getattr(exp, "experiment_name", None)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se pudo registrar el experiment en LangSmith ({type(e).__name__}: {e})")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluación completa del agente.")
    parser.add_argument("--reset", action="store_true", help="recrear la plantilla de la base antes de evaluar")
    parser.add_argument("--sin-reset", action="store_true", help=argparse.SUPPRESS)  # compatibilidad con start.py
    parser.add_argument("--casos", help="ids separados por coma (por defecto, todos)")
    parser.add_argument("--sin-juez", action="store_true", help="no usar jueces LLM (métricas RAG quedan sin calcular)")
    parser.add_argument("--sin-langsmith", action="store_true", help="no registrar el experiment en LangSmith")
    parser.add_argument("--reanudar", action="store_true",
                        help="continuar la última corrida sin terminar (con sus mismos casos y juez)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import logging
    import warnings

    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.WARNING, format="      %(message)s", stream=sys.stdout)

    from app.llm import limitar_rpm
    from scripts.reset_db import reset, restaurar
    from eval.evaluadores import resumir

    configurar_trazas()
    commit = commit_actual()
    if args.reanudar:
        progreso = cargar_progreso()
        if progreso is None:
            print("No hay una evaluación sin terminar para reanudar.")
            return 1
        if progreso["commit"] != commit:
            print(f"Aviso: la corrida empezó con el commit {progreso['commit']} y el código actual es {commit}.")
        commit = progreso["commit"]
    else:
        if PROGRESO.exists():
            print("Aviso: había una evaluación sin terminar; empieza una nueva (para continuarla, usá --reanudar).")
        progreso = {"commit": commit, "fecha": datetime.now().isoformat(), "casos": args.casos,
                    "usar_juez": not args.sin_juez, "resultados": []}
    inicio = datetime.fromisoformat(progreso["fecha"])
    if args.reset:
        reset()
    else:
        restaurar()  # siempre se parte del estado inicial (RF-74)
    limitar_rpm(get_settings().eval_rpm)

    casos = cargar_casos(set(progreso["casos"].split(",")) if progreso["casos"] else None)
    print(f"Evaluando {len(casos)} casos (EVAL_RPM={get_settings().eval_rpm})"
          + (f"; {len(progreso['resultados'])} ya evaluados en la corrida anterior" if args.reanudar else "") + "…")
    try:
        resultados, completa = correr(casos, usar_juez=progreso["usar_juez"], progreso=progreso)
    except KeyboardInterrupt:
        restaurar()
        print(f"\nEvaluación interrumpida: {len(progreso['resultados'])} casos guardados. "
              "Para seguir: python scripts/run_eval.py --reanudar")
        return 130
    restaurar()
    resumen = resumir([r for r in resultados if not r.get("omitido")])
    # El experiment de LangSmith se registra una sola vez, con la corrida completa.
    experimento = None if args.sin_langsmith or not completa else registrar_en_langsmith(resultados, commit)

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    base = RESULTADOS / f"{inicio:%Y-%m-%d_%H%M}_{commit}"
    base.with_suffix(".md").write_text(reporte(resultados, resumen, commit, inicio, experimento), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(
        {"commit": commit, "fecha": inicio.isoformat(), "resumen": resumen, "resultados": resultados},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print()
    for m in resumen.values():
        valor = m["valor"] if m["tipo"] == "max" else _pct(m["valor"])
        print(f"  {'✔' if m['cumple'] else '✘'} {m['nombre']:<40} {valor}")
    print(f"\nReporte: {base.with_suffix('.md').relative_to(RAIZ)}")
    if completa:
        PROGRESO.unlink(missing_ok=True)
    else:
        print(f"Reporte parcial ({len(resultados)} de {len(casos)} casos). Para completarlo: "
              "python scripts/run_eval.py --reanudar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
