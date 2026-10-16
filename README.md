# sql-gym-ai-agent

Agente de IA para un gimnasio con tres sedes (Centro, Norte y Sur). Recibe mensajes en lenguaje natural
(castellano o inglés) y decide si responde directamente, si es una consulta de lectura o una operación de
escritura. Un **agente ReAct** decide paso a paso si consulta la **base de datos** (text-to-SQL, con herramientas
para explorar el esquema), los **documentos de políticas** (RAG sobre 4 PDFs) o ambos, con qué consulta y cuándo
tiene información suficiente. Para un administrador, el mismo agente prepara las **operaciones de gestión de
socios** (por ejemplo, busca al socio por su email) y las propone; desde la propuesta, la validación, la
confirmación y la ejecución son un **flujo fijo**. Respeta en todo momento lo que cada perfil puede ver y hacer.

Trabajo práctico integrador de AI Engineering (UCSE, 2026). Se desarrolló con **Spec Driven Development**:
los artefactos están en [`sdd/`](sdd/) (spec funcional, spec técnico y `tasks.json`).

| Componente | Tecnología |
|---|---|
| Agente | LangGraph: agente ReAct con *tool calling* para la lectura y la preparación de la escritura, y flujo fijo con `interrupt()` para confirmarla |
| LLM y embeddings | Google Gemini (plan gratuito): modelo rápido, modelo principal y `gemini-embedding-001` |
| Base relacional | PostgreSQL 16 en Docker, con roles de solo lectura, vistas filtradas por socio y auditoría |
| RAG | PyMuPDF (con tablas a Markdown) + Chroma embebida |
| Interfaz | Streamlit |
| Observabilidad y evaluación | LangSmith (trazas y experimentos) + conjunto de 59 casos versionado |

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
| `--reanudar` | Con `--evaluar`, continúa la última evaluación cortada (ver §5). |
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
  "Suspendé al socio con DNI 27579048 por trato irrespetuoso al personal" o, sin el DNI, "Suspendé al socio con
  email ezequiel.ramirez5@example.com por prestar su QR a un tercero" (el agente lo busca en la base).

En las consultas de lectura, la ruta (datos, documentos o híbrida) sale de las herramientas que el agente
decidió usar. Cada respuesta tiene un desplegable **Detalle** con la ruta, las herramientas invocadas, los
**pasos del agente** (cada consulta a la base o búsqueda en los documentos, con su resultado), la SQL ejecutada
(solo administrador) y los fragmentos de documentos citados.

### Cómo trabaja el agente

```
clasificar ─┬─ directa / fuera de dominio ─────────────▶ respuesta (1 llamada al LLM, sin herramientas)
            ├─ socio que pide escribir o datos ajenos ─▶ rechazo (en código)
            ├─ lectura ──────────┬─▶ agente ⇄ ejecutar_herramientas   (hasta 4 rondas)
            └─ escritura (admin) ┘     │  consulta_sql, busqueda_documentos,
                                       │  describir_tabla, valores_columna
                                       ├─ lectura ─▶ sintetizar
                                       └─ escritura: operacion_socio (solo propone)
                                            ─▶ extraer ─▶ validar ─▶ confirmar (botón) ─▶ ejecutar
```

- **Lectura (agente):** el modelo principal, con `bind_tools`, elige la herramienta, escribe la SQL o la búsqueda
  y decide cuándo parar. Puede encadenar (consultar un dato y buscar la regla que corresponde), corregir una SQL
  que falló y combinar las dos fuentes. Si duda de una columna o de un valor, puede explorar el esquema
  (`describir_tabla`, `valores_columna`). La respuesta final la redacta `sintetizar`, que arma las citas y las
  fuentes en código.
- **Escritura (agente + workflow):** el agente puede buscar en la base lo que el pedido no dice directamente (por
  ejemplo, el DNI de un socio a partir de su email) y propone con `operacion_socio`, que no ejecuta nada. Desde ahí
  los pasos son fijos: la operación se arma desde el mensaje original, se valida en código y solo se ejecuta con
  el botón **Confirmar**. Si el agente no propone, el pedido pasa igual por la validación. En una lectura, y con
  perfil socio siempre, el agente no tiene ninguna herramienta de escritura.
- **Permisos:** las herramientas reciben el usuario de la sesión, nunca del modelo; la SQL pasa por el validador,
  el rol de solo lectura y la transacción `READ ONLY`, y la exploración del esquema solo muestra las tablas y las
  filas que el perfil puede leer.

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
python start.py --evaluar --reanudar       # sigue una evaluación cortada
.venv/Scripts/python scripts/run_eval.py   # con la base ya levantada
```

Corre los casos de [`eval/casos.yaml`](eval/casos.yaml) (todos los criterios de aceptación automatizables más
variantes en inglés), primero los de lectura y después los de escritura, restaurando la base antes y después de
cada caso de escritura. Respeta `EVAL_RPM` (requests por minuto a Gemini, por el plan gratuito). Genera
`eval/resultados/<fecha>_<commit>.md` con las métricas del §5.8.1 del spec funcional (exactitud de ruteo, respuesta
directa sin herramientas, datos, fidelidad y corrección RAG, escritura, validación, permisos y privacidad) en total
y por caso, y, si hay LangSmith, registra la corrida como *experiment* del dataset `gimnasio-eval`.

Opciones: `--casos CA-10,CA-20` (solo algunos), `--sin-juez` (sin jueces LLM), `--sin-langsmith`, `--reset`.

El progreso se guarda después de cada caso en `eval/resultados/en_curso.json`. Si la corrida se corta (Ctrl+C,
cuota diaria agotada o Gemini saturado), `--reanudar` la continúa con los mismos casos: saltea los ya evaluados y
repite los que terminaron con error del agente (`python start.py --evaluar --reanudar` o
`scripts/run_eval.py --reanudar`). El experiment de LangSmith se registra recién cuando la corrida se completa.

Los casos que usan fechas relativas ("hoy", "este mes") tienen `depende_fecha` y `valido_hasta`: los datos
tienen fechas fijas (fecha base 16/10/2026) pero el agente usa la fecha real del sistema.

## 6. Estructura del repositorio

```
app/
  config.py            Configuración (.env) con pydantic-settings
  auth.py              Identificación por DNI y seudónimo para las trazas
  llm.py               Modelos de Gemini con reintentos ante el error 429
  db/                  Conexiones por rol, validación (sqlglot) y ejecución de SQL de lectura, exploración del esquema, catálogos
  rag/                 Ingesta de PDFs (secciones, tablas a Markdown) y búsqueda en Chroma
  ops/                 Operaciones de escritura: modelos, validaciones de negocio, ejecución atómica, auditoría, mensajes
  agent/               Grafo LangGraph, herramientas del agente, nodos y prompts versionados (.md)
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
| Error 503 / `UNAVAILABLE` ("high demand") de Gemini | Saturación temporal de Google en ese modelo: el agente pasa al otro modelo configurado (rápido ↔ principal) y reintenta con espera creciente. |
| `Falta GOOGLE_API_KEY` | Pegala en `.env` o ejecutá `python start.py` en una terminal interactiva. |
| `Could not install packages due to an OSError: [Errno 2] No such file or directory` (Windows) | La ruta del repositorio es demasiado larga: algunas dependencias superan el límite de 260 caracteres de Windows dentro de `.venv`. Cloná el repositorio en una carpeta más corta (por ejemplo `C:\Users\<usuario>\Documents\sql-gym-ai-agent`) y ejecutá `python start.py --reinstalar`. |
| Los PDFs cambiaron | `python start.py --reindexar`. |
| Quiero ver el detalle de un error | `DEBUG=1 python start.py` (PowerShell: `$env:DEBUG=1; python start.py`). |
