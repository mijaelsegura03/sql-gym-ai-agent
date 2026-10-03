# Spec técnico — Agente de consultas y gestión del gimnasio

| Campo | Valor |
|---|---|
| Versión | 0.3 |
| Estado | Listo para dividir en tasks |
| Fecha | 2026-10-03 |
| Spec funcional de referencia | `sdd/functional/spec.md` v0.4 |
| Siguiente artefacto | `sdd/tasks.json` |

> Este documento define **cómo** se implementa lo que pide el spec funcional. Cada decisión
> referencia los requisitos (RF, RNF, OP, CA) que resuelve. Si algo de este documento contradice
> al spec funcional, manda el funcional y este documento se corrige.

---

## 1. Decisiones de stack

| ID | Tema | Decisión | Alternativas descartadas y motivo |
|---|---|---|---|
| T-01 | Lenguaje | **Python 3.12+** | — |
| T-02 | LLM | **Google Gemini** vía `langchain-google-genai`. Dos modelos configurables por variable de entorno: uno **rápido** (clasificación) y uno **principal** (SQL, extracción, síntesis y juez de evaluación). | Elegido por el plan gratuito. Ver §1.1. |
| T-03 | Framework agéntico | **LangGraph** (R-01) | Smolagents: no trae control de flujo explícito ni pausa para confirmar (`interrupt`), que se necesita para RF-46. |
| T-04 | Interfaz | **Streamlit** (RF-60 a RF-67) | — |
| T-05 | Observabilidad y evaluación | **LangSmith**: trazas (RF-70), datasets y experimentos (RF-72 a RF-75) | Langfuse: hay que levantarlo en Docker y suma servicios. |
| T-06 | Base relacional | **PostgreSQL 16** en Docker, sin volumen (DC-08) | — |
| T-07 | Base vectorial | **Chroma** embebida (`chromadb.PersistentClient`) en `.chroma/` (no se versiona) | pgvector: se descartó a favor de un componente separado de la base relacional. |
| T-08 | Embeddings | **`gemini-embedding-001`**, con `task_type` `RETRIEVAL_DOCUMENT` al indexar y `RETRIEVAL_QUERY` al buscar | Es el modelo estable de embeddings de Gemini; `gemini-embedding-2-preview` está en preview. |
| T-09 | Extracción de PDF | **PyMuPDF** (`pymupdf`), con `find_tables()` para las tablas | `pypdf` y `pdftotext` mezclan las columnas de las tablas (por ejemplo, la de motivos de rechazo de DOC-01 §4). |
| T-10 | Validación de SQL | **sqlglot** (dialecto `postgres`) | Validar con expresiones regulares es frágil. |
| T-11 | Driver de Postgres | **psycopg 3** (`psycopg[binary]`) | — |
| T-12 | Esquemas de datos | **Pydantic v2** para el estado, las salidas estructuradas del LLM y las operaciones | — |
| T-13 | Tests | **pytest** | — |
| T-14 | Arranque | Script **`start.py`** en la raíz: con un solo comando prepara el entorno y la configuración, levanta `docker compose`, crea las tablas, carga los datos, indexa los PDFs y abre la interfaz (§10) | `docker-entrypoint-initdb.d`: también serviría, pero deja la carga dentro del contenedor y hace menos visible cada paso. |

### 1.1 Modelos de Gemini

Verificado en la documentación de Google el 03/10/2026 (ai.google.dev, páginas *Models* y *Pricing*):

| Variable | Valor por defecto | Uso | Plan gratuito |
|---|---|---|---|
| `GEMINI_MODEL_FAST` | `gemini-3.5-flash-lite` | Nodo `clasificar` | Sí |
| `GEMINI_MODEL_MAIN` | `gemini-3.8-flash` | `generar_sql`, `extraer_operacion`, `sintetizar` y juez de la evaluación | Sí |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Indexación y búsqueda | Sí |

- **Límites del plan gratuito:** Google no los publica en la documentación; se ven en AI Studio (`aistudio.google.com/rate-limit`). Por eso todo llamado al LLM tiene reintentos con *backoff* exponencial ante el error 429, y la evaluación tiene un límite de requests por minuto configurable (`EVAL_RPM`).
- **Uso de los datos:** en el plan gratuito, Google puede usar el contenido para mejorar sus productos. Se acepta porque los datos son sintéticos (S-04).
- **Parámetros:** `temperature=0` en todos los nodos, salvo `responder_directo` (`0.3`).

---

## 2. Arquitectura

```
                         ┌──────────────────────────────────────────────┐
  Navegador ───────────▶ │ Streamlit (app/ui)                           │
                         │  login · chat · tarjeta de propuesta         │
                         └───────┬───────────────────────▲──────────────┘
                                 │ invoke / resume        │ RespuestaAgente
                         ┌───────▼───────────────────────┴──────────────┐
                         │ Grafo LangGraph (app/agent)                  │──▶ LangSmith (trazas)
                         │  clasificar → ruta → nodos → sintetizar      │
                         └──┬──────────────┬───────────────┬────────────┘
                            │ lectura      │ búsqueda      │ escritura
               ┌────────────▼───┐  ┌───────▼───────┐  ┌────▼────────────────┐
               │ app/db/lectura │  │ app/rag       │  │ app/ops (catálogo)  │
               │ sqlglot + rol  │  │ Chroma +      │  │ validación + rol    │
               │ de solo lectura│  │ embeddings    │  │ de escritura        │
               └────────┬───────┘  └───────────────┘  └────┬────────────────┘
                        │                                   │
                  ┌─────▼───────────────────────────────────▼─────┐
                  │ PostgreSQL 16 (Docker, sin volumen)           │
                  │  public: tablas del gimnasio                  │
                  │  socio_api: vistas filtradas para el socio    │
                  │  agente: auditoría                            │
                  └───────────────────────────────────────────────┘
```

**Principio de diseño:** el LLM **interpreta** (qué ruta, qué SQL de lectura, qué datos
tiene la operación) y **redacta**. Los **permisos** los aplica el código y la base de datos
(RNF-01, RNF-02): el perfil sale de la sesión, la lectura del socio pasa por vistas filtradas,
la SQL se valida antes de ejecutarse y las escrituras son funciones Python con parámetros
validados, ejecutadas con un rol distinto.

---

## 3. Estructura del repositorio

```
.
├── app/
│   ├── config.py                 # Settings (pydantic-settings) leídos del .env
│   ├── auth.py                   # identificar(perfil, dni) → Usuario | None; seudónimo
│   ├── llm.py                    # Fábrica de modelos Gemini con reintentos
│   ├── db/
│   │   ├── conexion.py           # Conexiones por rol (lector_admin, lector_socio, escritor)
│   │   ├── lectura.py            # validar_sql() + ejecutar_lectura()
│   │   └── catalogos/
│   │       ├── admin.md          # Descripción de tablas y semántica para generar SQL (admin)
│   │       └── socio.md          # Ídem, solo vistas de socio_api y tablas públicas
│   ├── rag/
│   │   ├── ingesta.py            # PDF → secciones → chunks → Chroma
│   │   └── buscador.py           # buscar(consulta, k, doc_ids) → list[Fragmento]
│   ├── ops/
│   │   ├── modelos.py            # Pydantic: AltaSocio, ModificacionSocio, Suspension, ...
│   │   ├── validacion.py         # Reglas de negocio (§7) → ResultadoValidacion
│   │   ├── ejecucion.py          # Transacción con rol escritor + auditoría
│   │   └── auditoria.py
│   ├── agent/
│   │   ├── estado.py             # EstadoAgente (TypedDict) y RespuestaAgente
│   │   ├── grafo.py              # Construcción del StateGraph
│   │   ├── nodos/                # Un módulo por nodo (§5.2)
│   │   └── prompts/              # Prompts versionados en .md
│   └── ui/
│       └── app.py                # Streamlit
├── data/
│   ├── gimnasio_schema.sql       # Existente (punto 1)
│   ├── gimnasio_datos.sql        # Existente (punto 2) — se modifica (§4.4)
│   ├── agente_schema.sql         # Nuevo: esquemas socio_api y agente
│   └── roles.sql                 # Nuevo: roles y permisos de la app
├── eval/
│   ├── casos.yaml                # Conjunto de evaluación versionado (RF-72)
│   ├── evaluadores.py
│   └── resultados/               # Reportes .md por corrida (RF-77)
├── scripts/
│   ├── reset_db.py               # Recrear la base (usado por start.py y la evaluación)
│   ├── indexar_pdfs.py
│   └── run_eval.py
├── tests/
├── rag-source/                   # PDFs existentes
├── start.py                      # Arranque completo con un solo comando (§10)
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 4. Base de datos

### 4.1 Contenedor

`docker-compose.yml` define un único servicio `db` (`postgres:16-alpine`), **sin volumen**, con
`healthcheck` (`pg_isready`) y el puerto publicado en `DB_PORT` (por defecto `5433`, para no chocar
con un Postgres local). El superusuario se configura con `POSTGRES_USER` y `POSTGRES_PASSWORD`.

### 4.2 Bases: plantilla y trabajo

`reset_db.py` carga el esquema y los datos en una base **plantilla** `gimnasio_template` y crea la
base de trabajo con `CREATE DATABASE gimnasio TEMPLATE gimnasio_template`. Así, volver al estado
inicial lleva menos de un segundo (drop + create) y la evaluación puede restaurar la base entre
casos de escritura (RF-74) sin recrear el contenedor.

Orden de ejecución sobre `gimnasio_template`, con psycopg y el superusuario:

1. `data/gimnasio_schema.sql`
2. `data/gimnasio_datos.sql`
3. `data/agente_schema.sql`
4. `data/roles.sql` (las contraseñas de los roles se pasan por variables de entorno)

### 4.3 Roles y permisos (RNF-01, RNF-02)

| Rol | Lo usa | Permisos | Configuración |
|---|---|---|---|
| `gym_lector_admin` | Lectura del perfil Administrador; `auth.py` | `SELECT` sobre todas las tablas de `public` y sobre `agente.auditoria` | `default_transaction_read_only = on`, `statement_timeout = 5s` |
| `gym_lector_socio` | Lectura del perfil Socio | `USAGE` sobre `socio_api` y `SELECT` sobre sus vistas. **Sin acceso a `public`.** | Ídem |
| `gym_escritor` | Solo `app/ops/ejecucion.py` | `SELECT` sobre las tablas necesarias para validar; `INSERT` y `UPDATE` sobre `socio`; `UPDATE (estado)` sobre `membresia`; `INSERT` sobre `agente.auditoria`. **Sin `DELETE` en ninguna tabla.** | `statement_timeout = 5s` |

El LLM **nunca** recibe una herramienta que ejecute SQL libre con `gym_escritor`.

### 4.4 Esquema `socio_api` (vistas del perfil Socio)

Las vistas propias filtran por `current_setting('app.socio_id')::bigint`. La aplicación fija ese
valor con `set_config('app.socio_id', <id de la sesión>, true)` dentro de la transacción, **antes**
de ejecutar la SQL generada. La SQL generada no puede cambiarlo, porque el validador rechaza
cualquier función fuera de una lista permitida, y `set_config` no está en ella (§6.2).

Las vistas se crean con `security_barrier` y su dueño es el superusuario: el socio lee a través
de ellas sin tener permisos sobre las tablas de base. El rol `gym_lector_socio` tiene
`search_path = socio_api`, así la SQL generada usa nombres simples (`sede`, `mis_pagos`).

| Vista | Contenido | Clase (§2.2 funcional) |
|---|---|---|
| `sede`, `sala`, `actividad`, `plan`, `plan_actividad`, `ejercicio` | Copia directa de la tabla | Pública |
| `clase` | Grilla con `instructor_nombre` (nombre y apellido; sin email, DNI ni rol) | Pública |
| `sesion_clase` | Sesiones con `instructor_nombre` del reemplazo | Pública |
| `ocupacion_sesion` | `sesion_id`, `cupo`, `confirmadas`, `en_lista_espera`, `lugares_disponibles` (agregado, sin identidades) | Pública (RF-54) |
| `mi_socio` | La fila del socio de la sesión | Propia |
| `mis_aptos`, `mis_membresias`, `mis_reservas`, `mis_accesos`, `mis_rutinas`, `mis_rutina_ejercicios` | Filas del socio de la sesión | Propia |
| `mis_pagos` | Pagos del socio de la sesión, **sin** `registrado_por` | Propia |

### 4.5 Esquema `agente` (auditoría, RF-49)

```sql
CREATE TABLE agente.auditoria (
    id              BIGSERIAL PRIMARY KEY,
    fecha           TIMESTAMP NOT NULL DEFAULT now(),
    empleado_id     BIGINT NOT NULL REFERENCES public.empleado(id),
    operacion       VARCHAR(30) NOT NULL,   -- alta_socio | modificacion_socio | suspension | reactivacion | baja
    socio_id        BIGINT REFERENCES public.socio(id),
    resultado       VARCHAR(20) NOT NULL
                    CHECK (resultado IN ('ejecutada','cancelada','descartada','rechazada','fallida')),
    motivo          TEXT,                   -- motivo de suspensión o baja (S-07)
    valores_antes   JSONB,
    valores_despues JSONB,
    detalle         TEXT,                   -- errores de validación o de ejecución
    trace_id        VARCHAR(64)             -- vínculo con la traza de LangSmith
);
```

El motivo de una suspensión o una baja se guarda en `auditoria.motivo`; **no** se agregan
columnas al esquema del punto 1.

### 4.6 Modificación del script de datos (D-01, DC-07)

- Al inicio del script se define la fecha base: `SELECT set_config('gym.fecha_base', '2026-10-16', false);`.
  Todas las apariciones de `CURRENT_DATE`, `NOW()` y `LOCALTIMESTAMP` se reemplazan por
  `current_setting('gym.fecha_base')::date` (o por su versión `timestamp`).
- Con fecha base 16/10/2026, los eventos quedan entre mediados de agosto y el 23/10/2026: accesos
  de los últimos 60 días, sesiones de las últimas 6 semanas y de la semana siguiente. Las altas
  más viejas y la emisión de aptos pueden ser anteriores (DC-07 no lo restringe).
- Se mantiene `setseed(0.42)`: con la semilla y las fechas fijas, **cada carga genera exactamente los
  mismos datos**, y los resultados esperados de la evaluación son estables.
- Se verifica que existan los casos que piden los criterios de aceptación (§9.4).
- El encabezado del script se actualiza para describir las fechas fijas.

### 4.7 Semántica derivada que usa el agente

Como los datos son fijos y el agente usa la fecha real (DC-07), **el estado guardado de una
membresía no alcanza** para saber si está vigente. Estas definiciones se documentan en los
catálogos (§6.1) y en las validaciones (§7):

| Concepto | Definición |
|---|---|
| Membresía vigente | `estado IN ('activa','congelada') AND CURRENT_DATE BETWEEN fecha_inicio AND fecha_fin` |
| Socio moroso | Socio `activo` sin membresía vigente cuya última membresía terminó hace 30 días o menos |
| Apto vigente | Existe un `apto_medico` con `fecha_vencimiento >= CURRENT_DATE` |
| Pago válido para recaudación | `pago.estado = 'aprobado'` |
| Asistencia | `reserva.estado = 'confirmada' AND asistio = TRUE` |

---

## 5. Agente (LangGraph)

### 5.1 Estado

```python
class Usuario(BaseModel):                 # sale de auth.py, nunca del LLM (RF-55)
    perfil: Literal["socio", "admin"]
    id: int                               # socio.id o empleado.id
    nombre: str
    sede_alcance: int | None              # admin: sede_id o None = todas; socio: sede principal
    seudonimo: str                        # HMAC-SHA256(dni, PSEUDONIMO_SECRET)[:12] (RF-71)

class EstadoAgente(TypedDict):
    usuario: Usuario
    mensaje: str
    fecha_hoy: date                       # fecha real (RF-06)
    clasificacion: Clasificacion | None   # salida de `clasificar`
    sql: str | None
    sql_error: str | None
    intentos_sql: int
    filas: list[dict] | None
    columnas: list[str] | None
    total_filas: int | None
    fragmentos: list[Fragmento]
    operacion: OperacionSocio | None      # salida de `extraer_operacion`
    validacion: ResultadoValidacion | None
    propuesta: PropuestaCambio | None
    decision: Literal["confirmar", "cancelar"] | None
    herramientas: Annotated[list[str], operator.add]   # "consulta_sql", "busqueda_documentos", "operacion_socio"
    respuesta: str | None
    fuentes: list[str]
```

El DNI del usuario **no** forma parte del estado; solo su `id` y su seudónimo (RF-71).

### 5.2 Nodos

| Nodo | Tipo | Qué hace | Requisitos |
|---|---|---|---|
| `clasificar` | LLM rápido, salida estructurada | Devuelve `Clasificacion` (§5.4). Si la ruta es `directa` o `fuera_dominio`, también devuelve la respuesta, así esos mensajes se resuelven con **una sola llamada al LLM y ninguna herramienta**. | RF-14 a RF-17, RF-20, RF-21 |
| `responder_directo` | Determinístico | Copia la respuesta de la clasificación al estado. No invoca herramientas. | RF-15, RF-16 |
| `rechazar_permiso` | Determinístico | Respuesta fija (en el idioma del mensaje) para un socio que pide escribir o información interna. | RF-40, RF-53 |
| `generar_sql` | LLM principal | Genera una sola sentencia `SELECT` a partir del catálogo del perfil, la fecha de hoy y, si hubo un intento anterior, el error obtenido. | RF-01, RF-06, RF-07, RF-08 |
| `ejecutar_sql` | Herramienta `consulta_sql` | `validar_sql()` y `ejecutar_lectura()` con el rol del perfil (§6). Si falla, vuelve a `generar_sql` hasta 2 veces más. | RF-02, RF-04, RF-05, RNF-05 |
| `buscar_documentos` | Herramienta `busqueda_documentos` | Busca en Chroma con la consulta en castellano que armó `clasificar` (§8.3). | RF-09, RF-12, RF-13 |
| `sintetizar` | LLM principal | Redacta la respuesta con las filas o los fragmentos disponibles, en el idioma del mensaje, con citas y fuentes. | RF-10, RF-11, RF-18, RF-19, RF-22 |
| `extraer_operacion` | LLM principal, salida estructurada | Convierte el mensaje en una `OperacionSocio` (§7.1). | §5.5 funcional |
| `validar_operacion` | Determinístico (herramienta `operacion_socio`) | Resuelve el socio, verifica alcance y reglas (§7.2) y arma la `PropuestaCambio`. | RF-41 a RF-45 |
| `confirmar` | `interrupt()` | Pausa el grafo y devuelve la propuesta a la interfaz. Se reanuda con `Command(resume={"decision": ...})`. | RF-46, RF-47 |
| `ejecutar_operacion` | Determinístico | Revalida, ejecuta en una transacción y registra la auditoría (§7.3). | RF-48, RF-49, RNF-03 |
| `responder_operacion` | Determinístico, con plantillas es/en | Arma el mensaje del rechazo, la cancelación o el resultado. | RF-45, RF-48 |

### 5.3 Grafo

```
START → clasificar ─┬─ directa | fuera_dominio | necesita_contexto ─▶ responder_directo ─▶ END
                    ├─ (socio ∧ escritura) | (socio ∧ pide_info_interna) ─▶ rechazar_permiso ─▶ END
                    ├─ datos ─────▶ generar_sql ⇄ ejecutar_sql ─────────────▶ sintetizar ─▶ END
                    ├─ documentos ─▶ buscar_documentos ─────────────────────▶ sintetizar ─▶ END
                    ├─ hibrida ───▶ [generar_sql ⇄ ejecutar_sql] ∥ [buscar_documentos] ─▶ sintetizar ─▶ END
                    └─ escritura (admin) ─▶ extraer_operacion ─▶ validar_operacion
                                              ├─ inválida ─▶ responder_operacion ─▶ END
                                              └─ válida ──▶ confirmar (interrupt)
                                                              ├─ cancelar ─▶ responder_operacion ─▶ END
                                                              └─ confirmar ─▶ ejecutar_operacion ─▶ responder_operacion ─▶ END
```

- **Las aristas condicionales que dependen del perfil se evalúan en código** con `usuario.perfil`
  (RNF-01). Aunque el LLM clasifique un mensaje de un socio como `escritura`, el socio no llega
  nunca a `extraer_operacion`.
- La ruta `hibrida` usa *fan-out* de LangGraph: la rama SQL y la de documentos corren en paralelo y
  `sintetizar` espera a las dos.
- `rechazar_permiso` por información interna es una primera barrera. La barrera real es la base:
  aunque el clasificador no lo detecte, el rol `gym_lector_socio` no puede leer datos de otros
  socios, la consulta falla o vuelve vacía y `sintetizar` responde que no hay información
  disponible para ese perfil.

**Llamadas al LLM por mensaje (RNF-06):**

| Ruta | Llamadas | Herramientas |
|---|---|---|
| directa / fuera de dominio | 1 | ninguna |
| documentos | 2 + 1 embedding | `busqueda_documentos` |
| datos | 3 (hasta 5 con reintentos) | `consulta_sql` |
| híbrida | 3 (hasta 5) + 1 embedding | `consulta_sql`, `busqueda_documentos` |
| escritura | 2 | `operacion_socio` |

### 5.4 Salida de `clasificar`

```python
class Clasificacion(BaseModel):
    ruta: Literal["directa", "fuera_dominio", "necesita_contexto",
                  "datos", "documentos", "hibrida", "escritura"]
    idioma: Literal["es", "en"]
    pide_info_interna: bool          # p. ej. datos de otros socios o métricas del negocio
    pregunta_datos: str | None       # parte del mensaje que se resuelve con la base
    consulta_documentos: str | None  # consulta de búsqueda reformulada en castellano (RF-13)
    respuesta_directa: str | None    # solo para directa / fuera_dominio / necesita_contexto
```

El prompt de `clasificar` incluye el perfil (para adaptar la respuesta a "¿qué podés hacer?"),
la regla de fuente de verdad (RF-18), ejemplos de mensajes mixtos (RF-17) y de mensajes que
dependen de un contexto previo (RF-21).

### 5.5 Independencia entre mensajes y confirmación

- **Cada mensaje se ejecuta en un `thread_id` nuevo** (UUID). El grafo no recibe mensajes
  anteriores, lo que garantiza RF-21.
- El checkpointer es `MemorySaver` (en memoria). Solo se usa para sostener el `interrupt()` de una
  propuesta pendiente dentro de su propio hilo.
- **"Confirmar" y "Cancelar" son botones** que reanudan el hilo de la propuesta. Escribir "sí"
  crea un hilo nuevo, que la clasificación resuelve como `necesita_contexto` (RF-46, CA-57).
- Si llega un mensaje nuevo con una propuesta pendiente, la interfaz descarta ese hilo y registra
  la auditoría con resultado `descartada` (RF-47, CA-56).

### 5.6 Respuesta hacia la interfaz

```python
class RespuestaAgente(BaseModel):
    texto: str
    ruta: str
    herramientas: list[str]
    fuentes: list[str]
    sql: str | None                  # None si el perfil es socio (RF-56): se filtra en código
    columnas: list[str] | None
    filas: list[dict] | None         # hasta 50 (RF-04)
    total_filas: int | None
    fragmentos: list[Fragmento]      # doc_id, seccion, pagina, texto
    propuesta: PropuestaCambio | None
    thread_id: str
    trace_id: str | None
```

La tabla de la interfaz se arma con `filas`, no con el texto del LLM (RF-03, RF-64).

---

## 6. Lectura de la base (text-to-SQL)

### 6.1 Catálogos para el prompt

`app/db/catalogos/admin.md` y `app/db/catalogos/socio.md` describen, para cada tabla o vista, sus
columnas, los valores posibles de los campos enumerados y las relaciones. Incluyen además la
semántica del §4.7, sinónimos del dominio (RF-08: "moroso", "MP", "la sede del centro") y entre 6 y
8 ejemplos de pregunta → SQL. Los catálogos se escriben a mano y no se generan del esquema, porque
la semántica es lo que más impacta en la calidad de la SQL. El esquema completo entra en el prompt:
son 17 tablas.

Reglas que se le piden a `generar_sql`:

- Una sola sentencia `SELECT` (se permite `WITH`).
- Cuando la consulta filtra personas por nombre, devolver también el DNI y el nombre completo, para
  que `sintetizar` detecte la ambigüedad (RF-07).
- Usar la fecha de hoy que viene en el prompt para resolver expresiones relativas (RF-06), con la
  semana de lunes a domingo.

### 6.2 Validación (`validar_sql`)

Con sqlglot, antes de ejecutar:

| Regla | Admin | Socio |
|---|:-:|:-:|
| Exactamente una sentencia, de tipo `SELECT` (o `WITH … SELECT`) | ✅ | ✅ |
| Sin `INSERT`, `UPDATE`, `DELETE`, `MERGE`, DDL, `COPY`, `SET`, `CALL`, `DO`, ni `SELECT … INTO` | ✅ | ✅ |
| Solo relaciones permitidas: tablas de `public` y `agente.auditoria` para el admin; vistas de `socio_api` para el socio | ✅ | ✅ |
| Funciones solo de una **lista permitida**: agregados, fecha y hora, texto, matemáticas, `coalesce`, `nullif`, `greatest`, `least`, `cast`, `to_char`, `date_trunc`, `extract` y `age`. Quedan fuera `set_config`, `current_setting`, `pg_*`, `lo_*` y `dblink*`. | ✅ | ✅ |

Si la validación falla, el error vuelve a `generar_sql` como si fuera un error de ejecución.

### 6.3 Ejecución (`ejecutar_lectura`)

1. Abre una transacción `READ ONLY` con el rol del perfil.
2. Si el perfil es socio, ejecuta `SELECT set_config('app.socio_id', %s, true)` con el id de la sesión.
3. Ejecuta `SELECT * FROM (<sql>) AS q LIMIT 51`.
4. Si vuelven 51 filas, ejecuta `SELECT count(*) FROM (<sql>) AS q` para informar el total y
   devuelve las primeras 50 (RF-04).
5. Convierte los tipos para la salida: `Decimal` a `float` redondeado, fechas a ISO.

Hay tres barreras independientes: el validador, el rol de solo lectura y la transacción `READ ONLY`.

---

## 7. Operaciones de escritura

### 7.1 Modelos (salida de `extraer_operacion`)

```python
class ReferenciaSocio(BaseModel):
    dni: str | None
    nombre: str | None               # si no hay DNI

class ContactoEmergencia(BaseModel):
    nombre: str
    parentesco: str
    telefono: str

class AltaSocio(BaseModel):
    tipo: Literal["alta_socio"]
    dni: str | None; nombre: str | None; apellido: str | None
    fecha_nacimiento: date | None
    contacto_emergencia: ContactoEmergencia | None
    email: str | None; telefono: str | None
    sede: str | None

class ModificacionSocio(BaseModel):
    tipo: Literal["modificacion_socio"]
    socio: ReferenciaSocio
    cambios: dict[Literal["nombre", "apellido", "email", "telefono",
                          "fecha_nacimiento", "contacto_emergencia", "sede_principal"], str]

class Suspension(BaseModel):   tipo: Literal["suspension"];   socio: ReferenciaSocio; motivo: str | None
class Reactivacion(BaseModel): tipo: Literal["reactivacion"]; socio: ReferenciaSocio
class Baja(BaseModel):         tipo: Literal["baja"];         socio: ReferenciaSocio; motivo: str | None

class FueraDeCatalogo(BaseModel):
    tipo: Literal["fuera_de_catalogo"]
    descripcion: str
    es_masiva: bool                  # "todos los morosos" (RF-43)

OperacionSocio = Annotated[AltaSocio | ModificacionSocio | Suspension | Reactivacion | Baja
                           | FueraDeCatalogo, Field(discriminator="tipo")]
```

Los campos son opcionales **a propósito**: si falta un dato, el LLM no lo inventa, lo deja vacío
y la validación lo informa (RF-45). El prompt lo indica explícitamente.

### 7.2 Validaciones (`app/ops/validacion.py`)

Todas son código Python con consultas parametrizadas; ninguna depende del LLM.

| Regla | Operaciones | Error o advertencia |
|---|---|---|
| El socio se resuelve a **exactamente uno** (por DNI o, si no hay DNI, por nombre) | OP-02 a OP-05 | Error: no existe, o hay varios (se listan nombre y DNI) (RF-44) |
| El socio está en el alcance de sede del administrador | OP-02 a OP-05 | Error (RF-41) |
| `fuera_de_catalogo` o `es_masiva` | — | Error con la lista de operaciones disponibles (RF-42, RF-43) |
| Obligatorios del alta: DNI, nombre, apellido, fecha de nacimiento y contacto de emergencia (nombre, parentesco y teléfono) | OP-01 | Error con la lista de faltantes |
| DNI: 7 u 8 dígitos y no repetido | OP-01 | Error |
| Email con formato válido y no repetido | OP-01, OP-02 | Error |
| Edad: ≥ 16 años con la fecha real | OP-01, OP-02 | Error si es menor de 16; **advertencia** si tiene 16 o 17 (autorización) |
| Sede: la del administrador; obligatoria si el administrador no tiene sede; debe existir y estar activa | OP-01, OP-02 | Error |
| El cambio de sede deja al socio fuera del alcance del administrador | OP-02 | Advertencia |
| Cambio de sede | OP-02 | Advertencia: una vez por período de membresía (DOC-01 §5) |
| DNI no modificable | OP-02 | Error |
| Estado de origen: suspensión ← `activo`; reactivación ← `suspendido` o `baja`; baja ← `activo` o `suspendido` | OP-03 a OP-05 | Error |
| Motivo obligatorio | OP-03, OP-05 | Error |
| Baja con una membresía `activa` o `congelada` y `fecha_fin >= hoy` | OP-05 | Error con la fecha de fin (DC-05) |
| Membresía `pendiente` | OP-05 | Efecto secundario: pasa a `cancelada` (advertencia) |
| Membresías `activa`, `congelada` o `pendiente` con `fecha_fin >= hoy` | OP-03 | Efecto secundario: pasan a `cancelada`, más la advertencia de reintegro parcial (DOC-01 §7, DOC-02 §11) (DT-03) |
| Reactivación | OP-04 | Advertencia: necesita una membresía nueva; no se cobra reingreso |
| Alta | OP-01 | Advertencia: necesita membresía y apto médico para ingresar |

El resultado es una `PropuestaCambio`: operación, socio (id, nombre, DNI), `antes`, `despues`,
`efectos` (por ejemplo, la lista de membresías que se cancelan), `advertencias` y `errores`.

### 7.3 Ejecución (`app/ops/ejecucion.py`)

1. Abre una transacción con `gym_escritor`.
2. Bloquea la fila del socio con `SELECT … FOR UPDATE`.
3. **Revalida** con las mismas reglas del §7.2 (RF-48). Si algo cambió, hace rollback y registra
   la auditoría con resultado `fallida`.
4. Aplica los cambios y los efectos secundarios con sentencias parametrizadas fijas, escritas en el
   código.
5. Inserta la fila de auditoría `ejecutada`, con los valores de antes y después y el `trace_id`.
6. Hace commit. Todo o nada (RNF-03).

Las propuestas canceladas, descartadas y rechazadas también se registran en la auditoría (en una
transacción propia).

---

## 8. RAG

### 8.1 Ingesta (`scripts/indexar_pdfs.py`)

1. Por cada PDF de `rag-source/`, el `doc_id` sale del nombre (`01_…` → `DOC-01`) y el título, de
   la primera línea.
2. Con PyMuPDF se extrae el texto por página. Las tablas se detectan con `find_tables()` y se
   convierten a Markdown, para que cada fila conserve juntas sus columnas (por ejemplo,
   *motivo | qué significa | cómo se resuelve*).
3. Se quitan los encabezados y pies repetidos (`DOC-0X · Versión 1.0 · Octubre 2026`, `Página N`).
4. **Chunking por sección:** se corta en los títulos numerados (`^\d+\.\s`), además del bloque
   "Resumen" y de cada pregunta de "Preguntas frecuentes" como chunk propio. Si una sección supera
   los 1.500 caracteres, se subdivide con `RecursiveCharacterTextSplitter` (1.000 caracteres, 150 de
   solapamiento).
5. Metadatos por chunk: `doc_id`, `titulo_doc`, `seccion` (por ejemplo, "4. Motivos de rechazo de
   acceso"), `pagina` y `chunk_id`.
6. Se generan los embeddings con `gemini-embedding-001` (`RETRIEVAL_DOCUMENT`) y se guardan en la
   colección `politicas` de Chroma.
7. Se guarda el hash SHA-256 de cada PDF en los metadatos de la colección. `start.py` reindexa
   solo si cambió algún hash o si se pasa `--reindexar` (S-02). Así se evitan llamadas de embeddings
   en cada arranque.

### 8.2 Búsqueda

- `buscar(consulta_es, k=5)` usa la similitud coseno con `RETRIEVAL_QUERY`.
- Se descartan los fragmentos con una distancia mayor a `RAG_DISTANCIA_MAX`, calibrada con los casos
  de evaluación. Si no queda ninguno, `sintetizar` recibe la lista vacía y responde que los
  documentos no cubren el tema (RF-11, CA-24).

### 8.3 Idioma

`clasificar` siempre entrega `consulta_documentos` en castellano, porque los PDFs están en
castellano, y `sintetizar` responde en el idioma del mensaje manteniendo las citas (RF-13).

### 8.4 Citas

`sintetizar` recibe cada fragmento con una etiqueta `[DOC-0X §N, p. P]` y tiene la instrucción de
citar solo con esas etiquetas. Las fuentes de la respuesta se arman en código con los metadatos de
los fragmentos citados, no con texto libre del LLM (RF-10, RF-19).

---

## 9. Observabilidad y evaluación

### 9.1 Trazas (RF-70, RF-71)

- Las trazas se activan con `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` y `LANGSMITH_PROJECT`.
  LangGraph traza cada nodo, cada llamada al LLM y cada herramienta automáticamente.
- Cada invocación lleva `metadata = {perfil, usuario: seudonimo, thread_id}` y `tags = [ruta]`.
  La ruta se agrega como tag apenas se conoce.
- Los nodos de herramienta se decoran con `@traceable(run_type="tool")`, así la herramienta
  invocada queda visible en la traza (CA-84, CA-85).
- El DNI del usuario nunca entra al estado (§5.1). El texto del mensaje se traza tal como lo
  escribió el usuario; puede contener DNIs de terceros, lo que se acepta porque los datos son
  sintéticos (S-04).

### 9.2 Conjunto de evaluación (`eval/casos.yaml`, RF-72)

```yaml
- id: CA-10
  categoria: datos              # directa | fuera_dominio | datos | documentos | hibrida |
                                # privacidad | escritura_valida | escritura_rechazada
  perfil: admin
  dni_usuario: "33890214"       # usuario con el que se ingresa
  mensaje: "¿Cuántos socios activos tiene cada sede?"
  ruta_esperada: datos
  herramientas_esperadas: [consulta_sql]
  depende_fecha: false
  esperado:
    filas:                      # se compara contra RespuestaAgente.filas, sin importar
      - ["Sede Centro", 52]     #   nombres de columna ni orden de las filas
      - ["Sede Norte", 31]
      - ["Sede Sur", 21]
- id: CA-52
  categoria: escritura_valida
  perfil: admin
  dni_usuario: "30123987"
  mensaje: "Suspendé al socio con DNI ... por trato irrespetuoso al personal"
  accion: confirmar             # confirmar | cancelar | escribir_si | otro_mensaje
  verificacion_db:              # consultas de verificación del estado final
    - sql: "SELECT estado FROM socio WHERE dni = '...'"
      esperado: [["suspendido"]]
```

- Los valores esperados se calculan **una vez**, sobre la base recién cargada, y se revisan a mano
  antes de versionarlos (RF-76). Los números del ejemplo son ilustrativos.
- Los casos con `depende_fecha: true` tienen además `valido_hasta` (S-05).
- Hay como mínimo 40 casos: todos los CA del spec funcional que se pueden automatizar (§7.1 a §7.7)
  más variantes en inglés.

### 9.3 Ejecución (`scripts/run_eval.py`, RF-73 a RF-75)

1. `reset_db.py` (base de trabajo limpia desde la plantilla).
2. Sube o actualiza `casos.yaml` como dataset de LangSmith (`gimnasio-eval`).
3. Ejecuta `langsmith.evaluate()` sobre una función objetivo que identifica al usuario, invoca el
   grafo y, si el caso lo pide, reanuda con la acción indicada.
4. Primero corren los casos de lectura y después los de escritura. Antes de **cada** caso de
   escritura se restaura la base desde la plantilla (RF-74).
5. Se respeta `EVAL_RPM` entre casos.
6. Cada corrida queda como un *experiment* de LangSmith (RF-75) y además genera
   `eval/resultados/<fecha>_<commit>.md` con la tabla de métricas y el detalle por caso (RF-77).

### 9.4 Evaluadores (`eval/evaluadores.py`)

| Métrica (§5.8.1 funcional) | Evaluador | Tipo |
|---|---|---|
| Exactitud de ruteo | `ruta == ruta_esperada` | Determinístico |
| Respuesta directa sin herramientas | `herramientas == []` en los casos `directa` y `fuera_dominio` | Determinístico |
| Exactitud de datos | Las filas esperadas están contenidas en `filas` (comparación por valores, sin importar el nombre de las columnas ni el orden; números con tolerancia de 0,01) | Determinístico |
| Fidelidad RAG | Juez LLM: "¿cada afirmación está respaldada por los fragmentos?" | LLM |
| Corrección RAG | Juez LLM contra `esperado.debe_contener` (lista de hechos) | LLM |
| Exactitud de escritura | `verificacion_db` después de confirmar | Determinístico |
| Validación de escritura | No hay propuesta y `verificacion_db` muestra la base sin cambios | Determinístico |
| Escritura sin confirmación | Hash de `socio` y `membresia` antes y después en los casos `cancelar`, `escribir_si`, `otro_mensaje` y los rechazados; debe ser igual | Determinístico |
| Permisos de escritura | Ídem, en los casos de socio y fuera de alcance | Determinístico |
| Privacidad de lectura | `esperado.no_debe_contener` (valores de otros socios) no aparece en el texto ni en las filas, más un juez LLM que confirma que es un rechazo | Mixto |

Verificación de los datos (D-01): `tests/test_datos.py` comprueba sobre la base cargada que existan
los casos que necesitan los CA: socios con membresía activa, congelada y pendiente; suspendidos;
accesos rechazados por cada motivo; aptos vencidos; un nombre de pila repetido (CA-17); y al menos
un socio en cada sede.

---

## 10. Arranque automatizado (`start.py`, RNF-07)

Objetivo: desde un clon recién bajado, **un solo comando** deja todo funcionando: entorno de
Python, configuración, contenedor, base creada y cargada, PDFs indexados e interfaz abierta en el
navegador.

```
python start.py [--reindexar] [--sin-ui] [--evaluar] [--mantener-db] [--reinstalar]
```

El script está en la **raíz** del repositorio. La parte de preparación (fase A) usa solo la
biblioteca estándar, así que se puede ejecutar con el Python del sistema antes de que exista el
entorno virtual.

### 10.1 Lo único que se hace a mano

| Requisito | Por qué no se automatiza |
|---|---|
| Python 3.12 o superior | Es el intérprete que ejecuta el script. |
| Docker Desktop (Windows o macOS) o Docker Engine (Linux), instalado | Instalarlo requiere permisos de administrador. Sí se automatiza **iniciarlo** si está apagado (paso 5). |
| API key de Gemini y, si se quieren trazas, de LangSmith | Son credenciales personales. El script las pide la primera vez y las guarda en `.env`. |

### 10.2 Pasos

**Fase A: preparación (solo biblioteca estándar)**

1. **Python:** verifica que la versión sea 3.12 o superior; si no, termina con un mensaje que dice
   qué versión se necesita.
2. **Entorno virtual:** si no existe `.venv`, o si cambió el hash de `requirements.txt` (guardado
   en `.venv/.requirements.sha256`), o si se pasa `--reinstalar`, crea `.venv` e instala las
   dependencias. Después **se vuelve a ejecutar a sí mismo** con el Python de `.venv`, pasando los
   mismos argumentos.
3. **Configuración:** si no existe `.env`, lo crea a partir de `.env.example`. Los secretos internos
   que estén vacíos (`POSTGRES_PASSWORD`, `DB_PASS_*` y `PSEUDONIMO_SECRET`) se generan con
   `secrets.token_urlsafe()`. Nunca se pisa un valor que ya existe.
4. **API keys:** si falta `GOOGLE_API_KEY` y la terminal es interactiva, la pide con `getpass` y la
   guarda en `.env`; si la terminal no es interactiva, termina indicando qué variable falta. Con
   `LANGSMITH_API_KEY` hace lo mismo, pero se puede dejar vacía: en ese caso pone
   `LANGSMITH_TRACING=false` y avisa que la app funciona sin trazas.

**Fase B: infraestructura**

5. **Docker:** ejecuta `docker info`. Si el daemon no responde:
   - en Windows, inicia Docker Desktop (ruta por defecto en `Program Files`);
   - en macOS, ejecuta `open -a Docker`;
   - en Linux, indica el comando para iniciarlo (`sudo systemctl start docker`), porque requiere permisos.

   Espera hasta 120 segundos a que responda. Si `docker` no está instalado, termina con un link a la
   instalación.
6. **Puerto:** si `DB_PORT` está ocupado por otro proceso, usa el siguiente puerto libre durante
   esta ejecución (se lo pasa como variable de entorno a los procesos hijos) y lo informa.
7. **Contenedor desde cero:** `docker compose down --remove-orphans` y después
   `docker compose up -d --wait db`. `--wait` espera a que el `healthcheck` esté en `healthy`
   (timeout de 90 segundos).
8. **Base de datos:** ejecuta `reset_db` (§4.2): crea `gimnasio_template`, corre los 4 scripts SQL
   en orden y clona `gimnasio`. Al terminar imprime la cantidad de registros por tabla, como
   verificación rápida de R-03.
9. **RAG:** indexa los PDFs si cambió algún hash o si se pasa `--reindexar` (§8.1).

**Fase C: ejecución**

10. Depende del flag:
    - **por defecto:** `streamlit run app/ui/app.py`, que abre el navegador automáticamente.
      Antes imprime la URL y **un DNI de ejemplo por perfil** (un socio, un administrador central
      y un administrador de sede), consultados en la base, para poder ingresar enseguida;
    - con `--evaluar`: ejecuta la evaluación completa (§9.3) en lugar de la interfaz;
    - con `--sin-ui`: termina dejando la base levantada (para correr tests o consultas a mano).
11. **Salida:** al cerrar con Ctrl+C, o si falla un paso posterior al 7, ejecuta `docker compose down`,
    salvo que se pase `--mantener-db`.

Cada paso imprime `[n/11] <qué hace> … OK (x,y s)`. Si un paso falla, se corta con un mensaje que
dice qué falló y cómo resolverlo, sin traceback (salvo con la variable `DEBUG=1`).

La segunda ejecución y las siguientes saltean los pasos 2 a 4 y el 9 si no hubo cambios, así
que el arranque normal tarda lo que tardan el contenedor y la carga de datos.

### 10.3 Configuración (`.env.example`, RNF-06)

```
GOOGLE_API_KEY=             # start.py la pide la primera vez
GEMINI_MODEL_FAST=gemini-3.5-flash-lite
GEMINI_MODEL_MAIN=gemini-3.8-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001

POSTGRES_USER=postgres
POSTGRES_PASSWORD=          # se genera si está vacío
DB_HOST=localhost
DB_PORT=5433
DB_NAME=gimnasio
DB_PASS_LECTOR_ADMIN=       # se genera si está vacío
DB_PASS_LECTOR_SOCIO=       # se genera si está vacío
DB_PASS_ESCRITOR=           # se genera si está vacío

CHROMA_DIR=.chroma
RAG_TOP_K=5
RAG_DISTANCIA_MAX=0.6

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=          # opcional: vacía = sin trazas
LANGSMITH_PROJECT=gimnasio-agente

PSEUDONIMO_SECRET=          # se genera si está vacío
EVAL_RPM=10
```

---

## 11. Interfaz (Streamlit)

| Elemento | Implementación | Requisitos |
|---|---|---|
| Ingreso | `st.radio` (perfil), `st.text_input` (DNI) y botón; llama a `auth.identificar()`. Si falla, mensaje genérico. | RF-50, RF-51, RF-61 |
| Sesión | `st.session_state`: `usuario`, `historial` (solo visual), `propuesta_pendiente` (con su `thread_id`) | RF-21, RF-62 |
| Chat | `st.chat_message` y `st.chat_input`; `st.status("Procesando…")` mientras corre el grafo | RF-62 |
| Detalle | `st.expander("Detalle")` con la ruta, las herramientas, la SQL (solo admin) y los fragmentos con su cita | RF-63, RF-56 |
| Tablas | `st.dataframe` con `filas` y la leyenda "Mostrando 50 de N" | RF-03, RF-64 |
| Propuesta | `st.container(border=True)` con una tabla antes/después, las advertencias y los botones Confirmar y Cancelar (`key` = `thread_id`). Después de decidir, se muestra el resultado y los botones se deshabilitan. | RF-65 |
| Barra lateral | Perfil, nombre y alcance de sede; preguntas de ejemplo por perfil (botones); limpiar historial; cerrar sesión | RF-62, RF-66, RF-67 |

El grafo se compila una sola vez con `@st.cache_resource`.

---

## 12. Tests automáticos

| Archivo | Qué prueba | Requiere |
|---|---|---|
| `test_validar_sql.py` | Acepta un `SELECT` válido; rechaza escrituras, varias sentencias, `set_config`, `pg_sleep`, tablas de `public` con perfil socio | — |
| `test_permisos_db.py` | `gym_lector_socio` no puede leer `public.socio`; `mis_pagos` solo devuelve filas del socio fijado; `gym_lector_admin` no puede escribir; `gym_escritor` no puede borrar | Docker |
| `test_validacion_ops.py` | Cada regla del §7.2, con casos que pasan y casos que fallan | Docker |
| `test_ejecucion_ops.py` | Atomicidad (falla a mitad → sin cambios), revalidación y auditoría | Docker |
| `test_auth.py` | Socio válido, admin válido (los 3 roles), instructor, empleado inactivo, DNI cruzado entre perfiles | Docker |
| `test_ingesta.py` | Chunking por sección, metadatos, tablas convertidas a Markdown | — |
| `test_grafo_rutas.py` | Con el LLM simulado: cada ruta recorre los nodos esperados; con perfil socio nunca se llega a `extraer_operacion` | — |
| `test_datos.py` | Los casos necesarios de los datos (§9.4) | Docker |

Los tests que requieren Docker se marcan con `@pytest.mark.db`.

---

## 13. Trazabilidad funcional → técnico

| Funcional | Técnico |
|---|---|
| RF-01 a RF-08 | §5.2 `generar_sql` / `ejecutar_sql`, §6 |
| RF-09 a RF-13 | §8 |
| RF-14 a RF-19 | §5.2 a §5.4 |
| RF-20 a RF-23 | §5.4, §5.5, prompts de `sintetizar` |
| RF-40 a RF-49, OP-01 a OP-05 | §5.3, §7, §4.5 |
| RF-50 a RF-57 | `app/auth.py`, §4.3, §4.4, §5.6 |
| RF-60 a RF-67 | §11 |
| RF-70 a RF-77 | §9 |
| RNF-01, RNF-02 | §4.3, §4.4, §6.2, §6.3, §7.3 |
| RNF-03 | §7.3 |
| RNF-04 a RNF-06 | §1.1, §5.3, §10.1 |
| RNF-07, RNF-08 | §10 (`start.py`), README, docstrings y prompts en `app/agent/prompts/` |
| D-01 | §4.6, `test_datos.py` |

---

## 14. Decisiones técnicas

| ID | Pregunta | Decisión | Estado |
|---|---|---|---|
| DT-01 | Fecha base de los datos fijos | `2026-10-16` (día de la entrega). Los eventos quedan entre agosto y el 16/10, y las sesiones de "la próxima semana" llegan al 23/10 (primer coloquio). Mientras se desarrolla antes de esa fecha, hay datos posteriores a la fecha real; es un efecto aceptado. | Cerrada |
| DT-02 | ¿Qué DNI se usa en cada caso de evaluación y en las preguntas de ejemplo? | Se eligen de los datos cargados, un socio representativo de cada situación, y quedan documentados en `casos.yaml`. | Se resuelve al implementar |
| DT-03 | En la **suspensión**, ¿qué membresías se cancelan? | La `activa`, la `congelada` y la `pendiente` con `fecha_fin >= hoy`. Un socio suspendido no puede usar ninguna. Se refleja en OP-03 del spec funcional (v0.4). | Cerrada |
| DT-04 | Valor de `RAG_DISTANCIA_MAX` | Se calibra con los casos de documentos y de "no cubierto" en la primera corrida. | Se resuelve al implementar |

---

## 15. Historial de cambios

| Versión | Fecha | Cambio |
|---|---|---|
| 0.1 | 2026-10-03 | Borrador inicial: Gemini, LangGraph, Streamlit, LangSmith, Chroma y Postgres en Docker con arranque por `scripts/start.py`. |
| 0.2 | 2026-10-03 | Se cierran DT-01 (fecha base 16/10/2026) y DT-03 (la suspensión cancela las membresías activa, congelada y pendiente). |
| 0.3 | 2026-10-03 | §10: arranque totalmente automatizado con `start.py` en la raíz (entorno virtual, `.env` con secretos generados, pedido de API keys, inicio de Docker, puerto libre, `--wait`, DNI de ejemplo y `--evaluar`). |
