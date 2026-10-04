# sql-gym-ai-agent

Agente de IA para un gimnasio con tres sedes (Centro, Norte y Sur). Recibe mensajes en lenguaje natural
(castellano o inglés) y decide si responde directamente, si consulta la **base de datos** (text-to-SQL), los
**documentos de políticas** (RAG sobre 4 PDFs) o ambos, y, para un administrador, ejecuta **operaciones de
gestión de socios** siempre con confirmación previa. Respeta en todo momento lo que cada perfil puede ver y hacer.

Trabajo práctico integrador de AI Engineering (UCSE, 2026). Se desarrolló con **Spec Driven Development**:
los artefactos están en [`sdd/`](sdd/) (spec funcional, spec técnico y `tasks.json`).

| Componente | Tecnología |
|---|---|
| Agente | LangGraph (grafo con ruteo, fan-out en paralelo e `interrupt()` para confirmar) |
| LLM y embeddings | Google Gemini (plan gratuito): modelo rápido, modelo principal y `gemini-embedding-001` |
| Base relacional | PostgreSQL 16 en Docker, con roles de solo lectura, vistas filtradas por socio y auditoría |
| RAG | PyMuPDF (con tablas a Markdown) + Chroma embebida |
| Interfaz | Streamlit |
| Observabilidad y evaluación | LangSmith (trazas y experimentos) + conjunto de 58 casos versionado |

---

## 1. Requisitos (lo único que se hace a mano)

1. **Python 3.12 o superior** ([python.org](https://www.python.org/downloads/)).
2. **Docker**: Docker Desktop en Windows/macOS o Docker Engine en Linux. No hace falta que esté abierto:
   `start.py` lo inicia si está apagado (en Linux indica el comando).
3. **API key de Gemini** (gratuita): <https://aistudio.google.com/apikey>.
4. *(Opcional)* **API key de LangSmith** para ver las trazas y los experimentos: <https://smith.langchain.com>.
   Sin ella la aplicación funciona igual, sin trazas.

## 2. Arranque con un solo comando

```bash
python start.py
```

La primera vez, el script:

1. verifica la versión de Python;
2. crea el entorno virtual `.venv` e instala `requirements.txt` (y se vuelve a ejecutar dentro de él);
3. crea `.env` a partir de `.env.example` y genera las contraseñas internas;
4. pide la API key de Gemini (y opcionalmente la de LangSmith) y las guarda en `.env`;
5. inicia Docker si está apagado;
6. busca un puerto libre para PostgreSQL (por defecto 5433);
7. levanta el contenedor desde cero (`docker compose down` + `up --wait`);
8. crea la base, carga los datos, las vistas, la auditoría y los roles, e imprime los registros por tabla;
9. indexa los PDFs en Chroma (solo si cambiaron);
10. abre la interfaz en <http://localhost:8501> e imprime **un DNI de ejemplo por perfil**;
11. al salir con `Ctrl+C`, baja el contenedor.

Las ejecuciones siguientes no reinstalan dependencias, no piden las keys y no reindexan si no hubo cambios.

| Flag | Qué hace |
|---|---|
| `--sin-ui` | Deja la base levantada y termina (para tests o consultas a mano). |
| `--evaluar` | Corre la evaluación completa en lugar de abrir la interfaz. |
| `--reindexar` | Vuelve a indexar los PDFs aunque no hayan cambiado. |
| `--mantener-db` | No baja el contenedor al salir. |
| `--reinstalar` | Recrea `.venv` y reinstala las dependencias. |

Cada paso imprime `[n/11] … OK (x,y s)`. Si algo falla, se muestra qué pasó y cómo resolverlo; con la variable
`DEBUG=1` se ve el traceback completo.

> **La base se crea de cero en cada ejecución** (decisión DC-08): los cambios que hagan los administradores se
> pierden al reiniciar. La evidencia permanente de cada operación es su traza en LangSmith.

## 3. Uso

### DNIs de ejemplo

| Perfil | DNI | Quién es |
|---|---|---|
| Socio | `37270609` | Emiliano Juárez — Sede Centro, Full Anual, rutina de 3 días |
| Socio | `23679921` | Santiago González — apto con "evitar ejercicios de alto impacto" |
| Administrador central | `30123987` | Romina Aguirre — administración, escribe sobre todas las sedes |
| Administrador de sede | `26789012` | Marcela Suárez — gerente de la Sede Norte |
| Recepción | `39654120` | Agustina Ledesma — recepción de la Sede Sur |

La identificación es **simulada** (solo DNI, sin contraseña; supuesto S-01 del spec funcional). Los instructores y
los empleados inactivos no tienen acceso.

### Qué se puede preguntar

- **Directas** (sin herramientas): "Hola", "¿Qué podés hacer?", "What can you do?".
- **Datos**: "¿Cuántos socios activos tiene cada sede?", "¿Cuánto se recaudó con MercadoPago el mes pasado?",
  "¿Cuándo vence mi membresía?", "¿A cuántas clases fui este mes?".
- **Documentos**: "¿Cuánto dura el apto médico?", "¿Puedo congelar mi membresía?", "Can I cancel a class booking without penalty?".
- **Híbridas**: "¿Por qué le rechazaron el ingreso al socio con DNI 44475265 y qué tiene que hacer?",
  "Con mi apto médico, ¿qué clases me recomiendan?".
- **Escritura** (solo administrador, con propuesta y botón **Confirmar**): alta, modificación de datos,
  suspensión, reactivación y baja de un socio, por ejemplo
  "Suspendé al socio con DNI 27579048 por trato irrespetuoso al personal".

Cada respuesta tiene un desplegable **Detalle** con la ruta, las herramientas invocadas, la SQL ejecutada
(solo administrador) y los fragmentos de documentos citados.

## 4. Tests

```bash
python start.py --sin-ui          # deja la base levantada
.venv/Scripts/python -m pytest    # Windows  (Linux/macOS: .venv/bin/python -m pytest)
```

- Los tests marcados `db` necesitan la base levantada (si no está, se saltean).
- Los marcados `llm` llaman a Gemini y no corren por defecto: `pytest -m llm`.
- El grafo, la interfaz y el runner de evaluación se prueban con un **LLM simulado**, sin red.

## 5. Evaluación

```bash
python start.py --evaluar                  # todo en un comando
.venv/Scripts/python scripts/run_eval.py   # con la base ya levantada
```

Corre los casos de [`eval/casos.yaml`](eval/casos.yaml) (todos los criterios de aceptación automatizables más
variantes en inglés), primero los de lectura y después los de escritura, restaurando la base antes y después de
cada caso de escritura. Respeta `EVAL_RPM` (requests por minuto a Gemini, por el plan gratuito). Genera
`eval/resultados/<fecha>_<commit>.md` con las métricas del §5.8.1 del spec funcional (exactitud de ruteo, respuesta
directa sin herramientas, datos, fidelidad y corrección RAG, escritura, validación, permisos y privacidad) en total
y por caso, y, si hay LangSmith, registra la corrida como *experiment* del dataset `gimnasio-eval`.

Opciones: `--casos CA-10,CA-20` (solo algunos), `--sin-juez` (sin jueces LLM), `--sin-langsmith`, `--reset`.

Los casos que usan fechas relativas ("hoy", "este mes") tienen `depende_fecha` y `valido_hasta`: los datos
tienen fechas fijas (fecha base 16/10/2026) pero el agente usa la fecha real del sistema.

## 6. Estructura del repositorio

```
app/
  config.py            Configuración (.env) con pydantic-settings
  auth.py              Identificación por DNI y seudónimo para las trazas
  llm.py               Modelos de Gemini con reintentos ante el error 429
  db/                  Conexiones por rol, validación (sqlglot) y ejecución de SQL de lectura, catálogos del esquema
  rag/                 Ingesta de PDFs (secciones, tablas a Markdown) y búsqueda en Chroma
  ops/                 Operaciones de escritura: modelos, validaciones de negocio, ejecución atómica, auditoría, mensajes
  agent/               Grafo LangGraph, nodos y prompts versionados (.md)
  ui/app.py            Interfaz Streamlit
data/
  gimnasio_schema.sql  Esquema (17 tablas)
  gimnasio_datos.sql   Datos sintéticos con fechas fijas (fecha base 16/10/2026)
  agente_schema.sql    Vistas del perfil Socio (socio_api) y auditoría (agente)
  roles.sql            Roles de la aplicación y permisos
eval/                  Conjunto de evaluación, evaluadores y reportes
rag-source/            Los 4 PDFs de políticas
scripts/               reset_db.py, indexar_pdfs.py, run_eval.py
sdd/                   Spec funcional, spec técnico y tasks.json
tests/                 pytest
start.py               Arranque completo
```

## 7. Seguridad: permisos aplicados fuera del modelo

- El perfil, la identidad y el alcance de sede salen del ingreso, nunca del texto del mensaje.
- La SQL generada pasa por tres barreras: el validador (una sola sentencia `SELECT`, solo tablas permitidas y
  funciones de una lista permitida), un rol de base de datos de solo lectura y una transacción `READ ONLY`.
- El socio consulta solo vistas del esquema `socio_api`, filtradas por su id, sin acceso a las tablas de base.
- Las escrituras son funciones Python con sentencias fijas y parámetros validados, ejecutadas con un rol que no
  tiene `DELETE`, y solo después de que el administrador presiona **Confirmar**.
- Las trazas identifican al usuario con un seudónimo (HMAC del DNI), nunca con el DNI.

## 8. Solución de problemas

| Problema | Qué hacer |
|---|---|
| `Docker no respondió en 120 segundos` | Abrí Docker Desktop a mano, esperá a que diga *Running* y volvé a ejecutar. |
| `Docker no está instalado` | Instalalo desde <https://docs.docker.com/get-docker/>. |
| El puerto 5433 está ocupado | `start.py` usa el siguiente libre y lo informa. Para fijar otro, cambiá `DB_PORT` en `.env`. |
| Error 429 / `RESOURCE_EXHAUSTED` de Gemini | Es el límite del plan gratuito: el agente reintenta con espera creciente. Para la evaluación, bajá `EVAL_RPM` en `.env`. |
| `Falta GOOGLE_API_KEY` | Pegala en `.env` o ejecutá `python start.py` en una terminal interactiva. |
| `Could not install packages due to an OSError: [Errno 2] No such file or directory` (Windows) | La ruta del repositorio es demasiado larga: algunas dependencias superan el límite de 260 caracteres de Windows dentro de `.venv`. Cloná el repositorio en una carpeta más corta (por ejemplo `C:\Users\<usuario>\Documents\sql-gym-ai-agent`) y ejecutá `python start.py --reinstalar`. |
| Los PDFs cambiaron | `python start.py --reindexar`. |
| Quiero ver el detalle de un error | `DEBUG=1 python start.py` (PowerShell: `$env:DEBUG=1; python start.py`). |
