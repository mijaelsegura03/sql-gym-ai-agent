"""Interfaz Streamlit del agente del gimnasio (fuera del SDD: consume ``RespuestaAgente`` y las
funciones de entrada del spec técnico §5.6).

- Pantalla de ingreso: perfil + DNI (RF-50, RF-51).
- Chat con historial solo visual (cada mensaje se resuelve sin contexto, RF-21), indicador de
  "procesando", tablas, detalle de cada respuesta y tarjeta de propuesta de cambio con los botones
  Confirmar y Cancelar (RF-46, RF-47).
- Barra lateral con el usuario, su alcance, mensajes de ejemplo, limpiar historial y cerrar sesión.

Ejecutar con ``streamlit run app/ui/app.py`` (o ``python start.py``, que además prepara todo).
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
# Streamlit agrega la carpeta del script (app/ui) al sys.path; como el script se llama app.py,
# taparía al paquete `app`. Se saca esa carpeta y se agrega la raíz del repositorio.
_CARPETA_UI = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _CARPETA_UI]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
if "app" in sys.modules and not hasattr(sys.modules["app"], "__path__"):
    del sys.modules["app"]

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.agent.estado import RespuestaAgente  # noqa: E402
from app.auth import Usuario, identificar  # noqa: E402
from app.ops.mensajes import OPERACIONES, filas_comparacion, texto_aviso  # noqa: E402

st.set_page_config(page_title="Asistente del gimnasio", page_icon=":material/fitness_center:", layout="wide")

# Ajustes de estilo que el tema de .streamlit/config.toml no cubre: ancho de lectura, jerarquía
# tipográfica y botones de ejemplo alineados a la izquierda.
ESTILOS = """
<style>
.block-container, [data-testid="stBottomBlockContainer"] { max-width: 860px; }
.block-container { padding-top: 2.75rem; }
h1 { font-size: 1.65rem !important; font-weight: 600 !important; letter-spacing: -0.01em; }
h4 { font-weight: 600 !important; }
.antetitulo { color: #2E6F6A; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em;
              text-transform: uppercase; margin-bottom: -0.6rem; }
.etiqueta { color: #6B6F73; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
.st-key-ejemplos button, .st-key-ejemplos button > div { justify-content: flex-start; text-align: left; }
.st-key-ejemplos button p { font-size: 0.875rem; }
[data-testid="stExpander"] details { border-color: #E2DFD8; }
</style>
"""

MENSAJE_LOGIN_INVALIDO = "No encontramos un usuario con esos datos."
NOMBRES_RUTA = {
    "directa": "Respuesta directa", "fuera_dominio": "Fuera de dominio", "necesita_contexto": "Necesita contexto",
    "datos": "Consulta de datos", "documentos": "Consulta de documentos", "hibrida": "Consulta híbrida",
    "escritura": "Operación de escritura", "error": "Error",
}


# ---------------------------------------------------------------------------
# Recursos y estado de la sesión
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def grafo():
    """Grafo compilado una sola vez por proceso."""
    from app.agent.grafo import get_grafo

    return get_grafo()


def agente():
    """Módulo de entrada del agente (asegura que el grafo esté compilado y cacheado)."""
    grafo()
    from app.agent import grafo as modulo

    return modulo


def iniciar_estado() -> None:
    st.session_state.setdefault("usuario", None)
    st.session_state.setdefault("historial", [])        # [{"rol": "user"|"assistant", "texto", "respuesta", "decision"}]
    st.session_state.setdefault("pendiente", None)      # thread_id de la propuesta que espera decisión
    st.session_state.setdefault("entrada_ejemplo", None)


def cerrar_sesion() -> None:
    """Descarta la propuesta pendiente (RF-47) y vuelve a la pantalla de ingreso."""
    usuario: Usuario | None = st.session_state.get("usuario")
    if usuario and st.session_state.get("pendiente"):
        try:
            agente().descartar(usuario, st.session_state["pendiente"])
        except Exception:  # noqa: BLE001
            pass
    for clave in ("usuario", "historial", "pendiente", "entrada_ejemplo"):
        st.session_state.pop(clave, None)


def limpiar_historial() -> None:
    usuario = st.session_state.get("usuario")
    if usuario and st.session_state.get("pendiente"):
        agente().descartar(usuario, st.session_state["pendiente"])
    st.session_state["historial"] = []
    st.session_state["pendiente"] = None


# ---------------------------------------------------------------------------
# Pantalla de ingreso
# ---------------------------------------------------------------------------
def encabezado() -> None:
    st.markdown(ESTILOS, unsafe_allow_html=True)
    st.markdown('<p class="antetitulo">Sedes Centro · Norte · Sur</p>', unsafe_allow_html=True)
    st.title("Asistente del gimnasio")


def pantalla_ingreso() -> None:
    _, centro, _ = st.columns([1, 3, 1])
    with centro:
        encabezado()
        st.caption("Consultas de datos, políticas del gimnasio y gestión de socios.")
        with st.form("ingreso", border=True):
            perfil = st.radio("Perfil", ["Socio", "Administrador"], horizontal=True)
            dni = st.text_input("DNI", placeholder="Solo números, sin puntos")
            enviar = st.form_submit_button("Ingresar", type="primary", use_container_width=True)
        if enviar:
            try:
                usuario = identificar("admin" if perfil == "Administrador" else "socio", dni)
            except Exception:  # noqa: BLE001
                st.error("No se pudo conectar con la base de datos. Verificá que esté levantada (python start.py).")
                return
            if usuario is None:
                st.error(MENSAJE_LOGIN_INVALIDO)  # genérico: no revela si existe con otro perfil (RF-51)
                return
            st.session_state["usuario"] = usuario
            st.session_state["historial"] = []
            st.rerun()
        st.caption("Identificación simulada por DNI, sin contraseña (supuesto S-01 del spec funcional).")


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
def ejemplos(usuario: Usuario) -> list[str]:
    """Entre 4 y 6 mensajes de ejemplo por perfil; el administrador tiene al menos uno de escritura."""
    if not usuario.es_admin:
        return [
            "Hola, ¿cuándo vence mi cuota?",
            "¿A cuántas clases fui este mes?",
            "¿Cuánto dura el apto médico?",
            "¿Puedo reservar Crossfit con mi plan?",
            "¿Cuántos lugares quedan en la próxima clase de Zumba de mi sede?",
            "¿Puedo cancelar una reserva de clase sin penalización?",
        ]
    sede = "" if usuario.sede_alcance else ", sede Norte"
    return [
        "¿Cuántos socios activos tiene cada sede?",
        "¿Cuánto se recaudó con MercadoPago el mes pasado?",
        "¿Qué pasa si un socio presta su QR?",
        "¿Por qué le rechazaron el ingreso al socio con DNI 34896217 y qué tiene que hacer?",
        "Dá de alta a Ana Torres, DNI 40111222, nacida el 12/03/1995, email ana.torres@example.com, "
        f"contacto de emergencia Luis Torres (padre) 341 5551234{sede}.",
        "¿Qué podés hacer?",
    ]


def barra_lateral(usuario: Usuario) -> None:
    with st.sidebar:
        st.subheader(usuario.nombre)
        perfil = "Administrador" if usuario.es_admin else "Socio"
        if usuario.es_admin and usuario.rol:
            perfil += f" ({usuario.rol})"
        st.markdown(f"**Perfil:** {perfil}")
        if usuario.es_admin:
            st.markdown(f"**Alcance:** {usuario.descripcion_alcance()}")
        elif usuario.sede_nombre:
            st.markdown(f"**Sede principal:** {usuario.sede_nombre}")
        st.divider()
        st.markdown('<p class="etiqueta">Probá con estos mensajes</p>', unsafe_allow_html=True)
        with st.container(key="ejemplos"):
            for i, ejemplo in enumerate(ejemplos(usuario)):
                if st.button(ejemplo, key=f"ejemplo_{i}", use_container_width=True):
                    st.session_state["entrada_ejemplo"] = ejemplo
        st.divider()
        c1, c2 = st.columns(2)
        c1.button("Limpiar chat", on_click=limpiar_historial, use_container_width=True)
        c2.button("Cerrar sesión", on_click=cerrar_sesion, use_container_width=True)
        st.caption("Cada mensaje se responde de forma independiente: el historial es solo visual.")


# ---------------------------------------------------------------------------
# Render de respuestas
# ---------------------------------------------------------------------------
def mostrar_tabla(r: RespuestaAgente) -> None:
    if not r.filas or len(r.filas) == 0:
        return
    if len(r.filas) == 1 and len(r.filas[0]) == 1:
        return  # un único valor ya está en el texto
    st.dataframe(pd.DataFrame(r.filas, columns=r.columnas or None), hide_index=True, use_container_width=True)
    if r.total_filas and r.total_filas > len(r.filas):
        st.caption(f"Mostrando {len(r.filas)} de {r.total_filas} filas.")


def mostrar_detalle(r: RespuestaAgente) -> None:
    with st.expander("Detalle"):
        st.markdown(f"**Ruta:** {NOMBRES_RUTA.get(r.ruta, r.ruta)}")
        st.markdown("**Herramientas invocadas:** " + (", ".join(f"`{h}`" for h in r.herramientas) or "ninguna"))
        if r.nodos:
            st.markdown("**Recorrido:** " + " → ".join(r.nodos))
        if r.sql:  # solo perfil Administrador (RF-56, el filtro está en el agente)
            st.markdown("**SQL ejecutada:**")
            st.code(r.sql, language="sql")
        citas = getattr(r, "citas", [])  # respuestas del historial creadas antes de agregar el campo
        if citas:
            st.markdown("**Secciones citadas:** " + ", ".join(citas))
        if r.fragmentos:
            st.markdown("**Fragmentos de documentos usados:**")
            for f in r.fragmentos:
                cuerpo = f.texto.split("\n", 1)[-1]
                st.markdown(f"- `{f.etiqueta}` {f.seccion}\n\n  > {cuerpo[:400]}{'…' if len(cuerpo) > 400 else ''}")
        pie = []
        if r.duracion_seg is not None:
            pie.append(f"{r.duracion_seg:.1f} s")
        if r.trace_id:
            pie.append(f"traza `{r.trace_id}`")
        if pie:
            st.caption(" · ".join(pie))


def decidir(indice: int, decision: str) -> None:
    """Callback de los botones Confirmar / Cancelar de la tarjeta (RF-46)."""
    entrada = st.session_state["historial"][indice]
    if entrada.get("decision"):
        return
    usuario = st.session_state["usuario"]
    r: RespuestaAgente = entrada["respuesta"]
    try:
        resultado = agente().reanudar(usuario, r.thread_id, decision)
    except Exception:  # noqa: BLE001
        resultado = None
    entrada["decision"] = decision
    entrada["resultado"] = resultado.texto if resultado else "No se pudo procesar la decisión. No se hizo ningún cambio."
    entrada["resultado_ok"] = bool(resultado and resultado.resultado_operacion and resultado.resultado_operacion.ok)
    if st.session_state.get("pendiente") == r.thread_id:
        st.session_state["pendiente"] = None


def mostrar_propuesta(indice: int, entrada: dict) -> None:
    r: RespuestaAgente = entrada["respuesta"]
    p = r.propuesta
    idioma = r.idioma
    en = idioma == "en"
    with st.container(border=True):
        titulo = OPERACIONES["en" if en else "es"].get(p.operacion, p.operacion)
        st.markdown(f"#### {'Change proposal' if en else 'Propuesta de cambio'}: {titulo}")
        st.markdown(f"**{'Member' if en else 'Socio'}:** {p.socio.nombre} — DNI {p.socio.dni}")
        if p.motivo:
            st.markdown(f"**{'Reason' if en else 'Motivo'}:** {p.motivo}")
        st.dataframe(pd.DataFrame(filas_comparacion(p, idioma)), hide_index=True, use_container_width=True)
        for efecto in p.efectos:
            st.warning(texto_aviso(efecto, idioma))
        for aviso in p.advertencias:
            st.info(texto_aviso(aviso, idioma))
        decidida = bool(entrada.get("decision"))
        c1, c2, _ = st.columns([1, 1, 4])
        c1.button("Confirm" if en else "Confirmar", key=f"confirmar_{r.thread_id}", type="primary", disabled=decidida,
                  on_click=decidir, args=(indice, "confirmar"))
        c2.button("Cancel" if en else "Cancelar", key=f"cancelar_{r.thread_id}", disabled=decidida,
                  on_click=decidir, args=(indice, "cancelar"))
        if entrada.get("decision") == "descartada":
            st.caption("Propuesta descartada: se envió otro mensaje sin confirmarla. No se hizo ningún cambio.")
        elif decidida:
            (st.success if entrada.get("resultado_ok") else st.info)(entrada.get("resultado", ""))


def mostrar_mensaje(indice: int, entrada: dict) -> None:
    if entrada["rol"] == "user":
        with st.chat_message("user"):
            st.markdown(entrada["texto"])
        return
    with st.chat_message("assistant"):
        r: RespuestaAgente | None = entrada.get("respuesta")
        if r is None:
            st.markdown(entrada["texto"])
            return
        if r.error:
            st.warning(r.texto)
        else:
            st.markdown(r.texto)
        mostrar_tabla(r)
        if r.propuesta and (r.propuesta_pendiente or entrada.get("decision")):
            mostrar_propuesta(indice, entrada)
        mostrar_detalle(r)


# ---------------------------------------------------------------------------
# Procesamiento de un mensaje
# ---------------------------------------------------------------------------
def procesar(usuario: Usuario, mensaje: str) -> None:
    historial = st.session_state["historial"]
    # Un mensaje nuevo descarta la propuesta pendiente sin ejecutarla (RF-47, CA-56)
    pendiente = st.session_state.get("pendiente")
    if pendiente:
        try:
            agente().descartar(usuario, pendiente)
        except Exception:  # noqa: BLE001
            pass
        for entrada in historial:
            r = entrada.get("respuesta")
            if r is not None and r.thread_id == pendiente and not entrada.get("decision"):
                entrada["decision"] = "descartada"
        st.session_state["pendiente"] = None

    historial.append({"rol": "user", "texto": mensaje})
    with st.chat_message("user"):
        st.markdown(mensaje)
    with st.chat_message("assistant"):
        with st.status("Procesando…", expanded=False) as estado:
            try:
                respuesta = agente().responder(usuario, mensaje)
                estado.update(label="Listo", state="complete")
            except Exception:  # noqa: BLE001 - sin trazas técnicas (RF-23)
                respuesta = None
                estado.update(label="Error", state="error")
    if respuesta is None:
        historial.append({"rol": "assistant", "texto": "Tuve un problema interno. Por favor, volvé a intentarlo."})
    else:
        historial.append({"rol": "assistant", "texto": respuesta.texto, "respuesta": respuesta})
        if respuesta.propuesta_pendiente:
            st.session_state["pendiente"] = respuesta.thread_id
    st.rerun()


def pantalla_chat(usuario: Usuario) -> None:
    barra_lateral(usuario)
    encabezado()
    if not st.session_state["historial"]:
        st.caption("Escribí una consulta o elegí uno de los mensajes de ejemplo de la barra lateral.")
    for i, entrada in enumerate(st.session_state["historial"]):
        mostrar_mensaje(i, entrada)
    escrito = st.chat_input("Escribí tu consulta…")
    mensaje = escrito or st.session_state.pop("entrada_ejemplo", None)
    if mensaje and mensaje.strip():
        procesar(usuario, mensaje.strip())


def main() -> None:
    iniciar_estado()
    usuario = st.session_state.get("usuario")
    if usuario is None:
        pantalla_ingreso()
    else:
        pantalla_chat(usuario)


main()
