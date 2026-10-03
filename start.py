"""Arranque completo del agente con un solo comando (spec técnico §10, RNF-07).

Desde un clon recién bajado deja todo funcionando: entorno virtual, configuración,
contenedor de PostgreSQL, base creada y cargada, PDFs indexados e interfaz abierta.

Uso::

    python start.py [--reindexar] [--sin-ui] [--evaluar] [--mantener-db] [--reinstalar]

Flags:
    --reindexar    vuelve a indexar los PDFs aunque no hayan cambiado.
    --sin-ui       termina dejando la base levantada (para tests o consultas a mano).
    --evaluar      corre la evaluación completa en lugar de abrir la interfaz.
    --mantener-db  no baja el contenedor al salir.
    --reinstalar   recrea el entorno virtual y reinstala las dependencias.

La fase A (preparación) usa solo la biblioteca estándar, así que se puede ejecutar con el
Python del sistema antes de que exista ``.venv``. Después se vuelve a ejecutar a sí mismo con
el Python del entorno virtual para las fases B y C. Con la variable ``DEBUG=1`` se muestran
los tracebacks completos.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback
import venv
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / ".venv"
ENV = RAIZ / ".env"
ENV_EJEMPLO = RAIZ / ".env.example"
REQUIREMENTS = RAIZ / "requirements.txt"
HASH_REQ = VENV / ".requirements.sha256"
TOTAL_PASOS = 11
SECRETOS_INTERNOS = ["POSTGRES_PASSWORD", "DB_PASS_LECTOR_ADMIN", "DB_PASS_LECTOR_SOCIO", "DB_PASS_ESCRITOR", "PSEUDONIMO_SECRET"]
MARCA_VENV = "GYM_START_EN_VENV"


class PasoError(Exception):
    """Un paso del arranque falló. ``ayuda`` explica cómo resolverlo."""

    def __init__(self, mensaje: str, ayuda: str = "") -> None:
        super().__init__(mensaje)
        self.ayuda = ayuda


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
class Paso:
    """Context manager que imprime ``[n/11] <qué hace> … OK (x,y s)``."""

    def __init__(self, n: int, texto: str) -> None:
        self.n, self.texto = n, texto
        self.nota = ""

    def __enter__(self) -> "Paso":
        print(f"[{self.n}/{TOTAL_PASOS}] {self.texto} …", end=" ", flush=True)
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, tipo, valor, tb) -> bool:
        seg = f"{time.perf_counter() - self.t0:.1f}".replace(".", ",")
        if tipo is None:
            print(f"OK ({seg} s){' — ' + self.nota if self.nota else ''}", flush=True)
        else:
            print(f"FALLÓ ({seg} s)", flush=True)
        return False


def en_venv() -> bool:
    """True si este proceso corre con el Python de ``.venv``."""
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def python_venv() -> Path:
    """Ruta del intérprete de ``.venv`` según el sistema operativo."""
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def leer_env() -> dict[str, str]:
    """Lee ``.env`` como diccionario (ignora comentarios y líneas vacías)."""
    valores: dict[str, str] = {}
    if ENV.exists():
        for linea in ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", linea)
            if m:
                valor = m.group(2)
                valor = re.sub(r"\s+#.*$", "", valor) if not valor.startswith(("'", '"')) else valor.strip("'\"")
                valores[m.group(1)] = valor
    return valores


def fijar_en_env(clave: str, valor: str, pisar: bool = False) -> None:
    """Escribe ``clave=valor`` en ``.env``. Nunca pisa un valor existente salvo con ``pisar``."""
    lineas = ENV.read_text(encoding="utf-8").splitlines()
    patron = re.compile(rf"^\s*{re.escape(clave)}\s*=\s*(.*?)\s*$")
    for i, linea in enumerate(lineas):
        m = patron.match(linea)
        if m:
            actual = re.sub(r"\s+#.*$", "", m.group(1))
            if actual and not pisar:
                return
            lineas[i] = f"{clave}={valor}"
            break
    else:
        lineas.append(f"{clave}={valor}")
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def correr(cmd: list[str], timeout: float | None = None, capturar: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta un comando y devuelve el resultado (sin lanzar excepción por código de salida)."""
    return subprocess.run(cmd, cwd=RAIZ, capture_output=capturar, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Fase A: preparación (solo biblioteca estándar)
# ---------------------------------------------------------------------------
def paso_python() -> None:
    with Paso(1, "Verificando la versión de Python"):
        if sys.version_info < (3, 12):
            raise PasoError(
                f"Se necesita Python 3.12 o superior y se está usando {platform.python_version()}.",
                "Instalá Python 3.12+ desde https://www.python.org/downloads/ y volvé a ejecutar el script.",
            )


def paso_venv(reinstalar: bool) -> bool:
    """Crea o actualiza ``.venv``. Devuelve True si hay que reejecutarse con su Python."""
    with Paso(2, "Preparando el entorno virtual (.venv)") as p:
        hash_req = sha256(REQUIREMENTS)
        al_dia = VENV.exists() and python_venv().exists() and HASH_REQ.exists() \
            and HASH_REQ.read_text().strip() == hash_req
        if al_dia and not reinstalar:
            p.nota = "sin cambios"
        else:
            if reinstalar and VENV.exists() and not en_venv():
                shutil.rmtree(VENV)
            if not python_venv().exists():
                venv.EnvBuilder(with_pip=True).create(VENV)
            print("\n      instalando dependencias (puede tardar unos minutos)…", flush=True)
            r = correr([str(python_venv()), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                        "-r", str(REQUIREMENTS)], capturar=True)
            if r.returncode != 0:
                raise PasoError("Falló la instalación de dependencias:\n" + (r.stderr or r.stdout)[-2000:],
                                "Revisá la conexión a internet y volvé a ejecutar con --reinstalar.")
            HASH_REQ.write_text(hash_req)
            p.nota = "dependencias instaladas"
    return not en_venv()


def paso_config() -> None:
    with Paso(3, "Preparando la configuración (.env)") as p:
        if not ENV.exists():
            shutil.copyfile(ENV_EJEMPLO, ENV)
            p.nota = ".env creado desde .env.example"
        actuales = leer_env()
        generados = [k for k in SECRETOS_INTERNOS if not actuales.get(k)]
        for clave in generados:
            fijar_en_env(clave, secrets.token_urlsafe(24))
        if generados:
            p.nota = (p.nota + "; " if p.nota else "") + f"{len(generados)} secretos generados"


def paso_api_keys() -> None:
    with Paso(4, "Verificando las API keys") as p:
        valores = leer_env()
        interactiva = sys.stdin.isatty() and sys.stdout.isatty()
        if not valores.get("GOOGLE_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            if not interactiva:
                raise PasoError("Falta GOOGLE_API_KEY en .env.",
                                "Creala en https://aistudio.google.com/apikey y pegala en el archivo .env.")
            print()
            key = getpass.getpass("      Pegá tu GOOGLE_API_KEY de Gemini (no se muestra): ").strip()
            if not key:
                raise PasoError("No se ingresó la GOOGLE_API_KEY.",
                                "Creala en https://aistudio.google.com/apikey y volvé a ejecutar el script.")
            fijar_en_env("GOOGLE_API_KEY", key, pisar=True)
        if not valores.get("LANGSMITH_API_KEY"):
            key = ""
            if interactiva and not valores.get("LANGSMITH_PREGUNTADA"):
                print()
                key = getpass.getpass("      Pegá tu LANGSMITH_API_KEY (Enter para seguir sin trazas): ").strip()
                fijar_en_env("LANGSMITH_PREGUNTADA", "1")
            if key:
                fijar_en_env("LANGSMITH_API_KEY", key, pisar=True)
            else:
                fijar_en_env("LANGSMITH_TRACING", "false", pisar=True)
                p.nota = "sin LANGSMITH_API_KEY: la app funciona sin trazas"


# ---------------------------------------------------------------------------
# Fase B: infraestructura
# ---------------------------------------------------------------------------
def docker_responde() -> bool:
    try:
        return correr(["docker", "info"], timeout=20).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def paso_docker() -> None:
    with Paso(5, "Verificando Docker") as p:
        if shutil.which("docker") is None:
            raise PasoError("Docker no está instalado.", "Instalalo desde https://docs.docker.com/get-docker/")
        if docker_responde():
            return
        sistema = platform.system()
        if sistema == "Windows":
            exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
            if not exe.exists():
                raise PasoError("Docker no responde y no se encontró Docker Desktop.", "Inicialo a mano y volvé a ejecutar.")
            subprocess.Popen([str(exe)], close_fds=True)
        elif sistema == "Darwin":
            subprocess.Popen(["open", "-a", "Docker"])
        else:
            raise PasoError("El daemon de Docker no responde.", "Inicialo con: sudo systemctl start docker")
        print("\n      iniciando Docker Desktop, esperando hasta 120 s…", end=" ", flush=True)
        limite = time.time() + 120
        while time.time() < limite:
            if docker_responde():
                p.nota = "Docker iniciado"
                return
            time.sleep(3)
        raise PasoError("Docker no respondió en 120 segundos.", "Abrí Docker Desktop a mano, esperá a que inicie y volvé a ejecutar.")


def puerto_libre(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) != 0


def paso_puerto() -> None:
    with Paso(6, "Verificando el puerto de PostgreSQL") as p:
        # Primero se baja un contenedor viejo de este proyecto, que podría estar usando el puerto
        correr(["docker", "compose", "down", "--remove-orphans"], timeout=120)
        puerto = int(os.environ.get("DB_PORT") or leer_env().get("DB_PORT") or 5433)
        original = puerto
        while not puerto_libre(puerto):
            puerto += 1
            if puerto > original + 50:
                raise PasoError(f"No hay puertos libres entre {original} y {puerto}.", "Liberá el puerto o cambiá DB_PORT en .env.")
        os.environ["DB_PORT"] = str(puerto)
        p.nota = f"puerto {puerto}" + (f" (el {original} está ocupado; se usa este durante esta ejecución)" if puerto != original else "")


def paso_contenedor() -> None:
    with Paso(7, "Levantando PostgreSQL desde cero (docker compose)"):
        correr(["docker", "compose", "down", "--remove-orphans"], timeout=120)
        r = correr(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "90", "db"], timeout=300)
        if r.returncode != 0:
            raise PasoError("No se pudo levantar el contenedor:\n" + (r.stderr or r.stdout)[-1500:],
                            "Revisá que Docker tenga recursos libres y que el .env tenga POSTGRES_PASSWORD.")


def paso_base() -> None:
    with Paso(8, "Creando y cargando la base de datos") as p:
        from scripts.reset_db import ErrorScript, reset

        try:
            conteo = reset()
        except ErrorScript as e:
            raise PasoError(str(e), "Revisá el script SQL indicado.") from None
        p.nota = f"{sum(conteo.values())} registros"
    for tabla, n in conteo.items():
        print(f"      {tabla:<17} {n:>6}")


def paso_rag(reindexar: bool) -> None:
    with Paso(9, "Indexando los PDFs (RAG)") as p:
        from scripts.indexar_pdfs import indexar

        resultado = indexar(forzar=reindexar)
        p.nota = resultado


# ---------------------------------------------------------------------------
# Fase C: ejecución
# ---------------------------------------------------------------------------
def dni_ejemplo() -> dict[str, str]:
    """Un DNI de ejemplo por perfil, consultado en la base."""
    from app.db.conexion import conectar

    consultas = {
        "Socio": """SELECT s.dni, s.nombre || ' ' || s.apellido AS nombre FROM socio s
                    JOIN membresia m ON m.socio_id = s.id AND m.estado = 'activa'
                    WHERE s.estado = 'activo' ORDER BY m.fecha_fin DESC, s.id LIMIT 1""",
        "Administrador central": """SELECT dni, nombre || ' ' || apellido AS nombre FROM empleado
                    WHERE activo AND rol = 'administracion' AND sede_id IS NULL ORDER BY id LIMIT 1""",
        "Administrador de sede": """SELECT e.dni, e.nombre || ' ' || e.apellido || ' (' || s.nombre || ')' AS nombre
                    FROM empleado e JOIN sede s ON s.id = e.sede_id
                    WHERE e.activo AND e.rol = 'gerente' ORDER BY e.id DESC LIMIT 1""",
    }
    with conectar("lector_admin") as conn:
        return {perfil: "{dni} — {nombre}".format(**conn.execute(q).fetchone()) for perfil, q in consultas.items()}


def paso_ejecucion(args: argparse.Namespace) -> int:
    print(f"[10/{TOTAL_PASOS}] ", end="")
    if args.sin_ui:
        print("--sin-ui: la base queda levantada en el puerto", os.environ.get("DB_PORT"))
        return 0
    if args.evaluar:
        print("Corriendo la evaluación completa…", flush=True)
        return subprocess.call([sys.executable, str(RAIZ / "scripts" / "run_eval.py"), "--sin-reset"], cwd=RAIZ)
    print("Abriendo la interfaz en http://localhost:8501", flush=True)
    for perfil, dato in dni_ejemplo().items():
        print(f"      DNI de ejemplo — {perfil}: {dato}")
    print("      (Ctrl+C para salir)", flush=True)
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(RAIZ / "app" / "ui" / "app.py"),
                            "--server.headless=false", "--browser.gatherUsageStats=false"], cwd=RAIZ)


def bajar_contenedor() -> None:
    print(f"[11/{TOTAL_PASOS}] Bajando el contenedor …", end=" ", flush=True)
    correr(["docker", "compose", "down", "--remove-orphans"], timeout=120)
    print("OK", flush=True)


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Arranque completo del agente del gimnasio.")
    parser.add_argument("--reindexar", action="store_true", help="reindexar los PDFs aunque no hayan cambiado")
    parser.add_argument("--sin-ui", action="store_true", help="dejar la base levantada sin abrir la interfaz")
    parser.add_argument("--evaluar", action="store_true", help="correr la evaluación en lugar de la interfaz")
    parser.add_argument("--mantener-db", action="store_true", help="no bajar el contenedor al salir")
    parser.add_argument("--reinstalar", action="store_true", help="recrear .venv y reinstalar dependencias")
    args = parser.parse_args()
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")

    # ---- Fase A
    if os.environ.get(MARCA_VENV) != "1":
        paso_python()
        if paso_venv(args.reinstalar):
            env = dict(os.environ, **{MARCA_VENV: "1"})
            argv = [a for a in sys.argv[1:] if a != "--reinstalar"]
            try:
                return subprocess.call([str(python_venv()), str(Path(__file__).resolve()), *argv], env=env)
            except KeyboardInterrupt:
                return 130
    paso_config()
    paso_api_keys()

    # ---- Fases B y C (ya dentro de .venv)
    sys.path.insert(0, str(RAIZ))
    paso_docker()
    paso_puerto()
    contenedor_arriba = False
    codigo = 1
    try:
        paso_contenedor()
        contenedor_arriba = True
        paso_base()
        paso_rag(args.reindexar)
        codigo = paso_ejecucion(args)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        codigo = 130
    finally:
        mantener = args.mantener_db or (args.sin_ui and codigo == 0)
        if contenedor_arriba and not mantener:
            bajar_contenedor()
    return codigo


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PasoError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        if e.ayuda:
            print(f"Cómo resolverlo: {e.ayuda}", file=sys.stderr)
        if os.environ.get("DEBUG") == "1":
            traceback.print_exc()
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - mensaje claro en lugar de traceback
        print(f"\nERROR inesperado: {type(e).__name__}: {e}", file=sys.stderr)
        print("Ejecutá con DEBUG=1 para ver el detalle.", file=sys.stderr)
        if os.environ.get("DEBUG") == "1":
            traceback.print_exc()
        sys.exit(1)
