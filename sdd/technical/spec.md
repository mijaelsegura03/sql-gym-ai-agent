# Spec técnico — Agente de consultas y gestión del gimnasio

| Campo | Valor |
|---|---|
| Versión | 0.7 |
| Estado | Implementado (ajustes de implementación registrados en el historial) |
| Fecha | 2026-10-04 |
| Spec funcional de referencia | `sdd/functional/spec.md` v0.6 |
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
| T-05 | Evaluación | **Runner propio** (`scripts/run_eval.py`) con reportes versionados en `eval/resultados/` (RF-72 a RF-76). El registro en LangSmith es opcional y queda fuera del SDD. | `langsmith.evaluate()` como ejecución: el orden de los casos y la restauración de la base dependerían de cómo recorre los ejemplos (§9.3). |
| T-06 | Base relacional | **PostgreSQL 16** en Docker, sin volumen (DC-08) | — |
| T-07 | Base vectorial | **Chroma** embebida (`chromadb.PersistentClient`) en `.chroma/` (no se versiona) | pgvector: se descartó a favor de un componente separado de la base relacional. |
| T-08 | Embeddings | **`gemini-embedding-001`**, con `task_type` `RETRIEVAL_DOCUMENT` al indexar y `RETRIEVAL_QUERY` al buscar | Es el modelo estable de embeddings de Gemini; `gemini-embedding-2-preview` está en preview. |
| T-09 | Extracción de PDF | **PyMuPDF** (`pymupdf`), con `find_tables()` para las tablas | `pypdf` y `pdftotext` mezclan las columnas de las tablas (por ejemplo, la de motivos de rechazo de DOC-01 §4). |
| T-10 | Validación de SQL | **sqlglot** (dialecto `postgres`) | Validar con expresiones regulares es frágil. |
| T-11 | Driver de Postgres | **psycopg 3** (`psycopg[binary]`) | — |
| T-12 | Esquemas de datos | **Pydantic v2** para el estado, las salidas estructuradas del LLM y las operaciones | — |
| T-13 | Tests | **pytest** | — |

### 1.1 Modelos de Gemini

Verificado en la documentación de Google el 03/10/2026 (ai.google.dev, páginas *Models* y *Pricing*):

| Variable | Valor por defecto | Uso | Plan gratuito |
|---|---|---|---|
| `GEMINI_MODEL_FAST` | `gemini-3.1-flash-lite` | Nodo `clasificar` | Sí (15 RPM, 500 RPD) |
| `GEMINI_MODEL_MAIN` | `gemini-3.5-flash-lite` | `generar_sql`, `extraer_operacion` y `sintetizar` | Sí (15 RPM, 500 RPD) |
| `GEMINI_MODEL_JUEZ` | `gemini-3.1-flash-lite` | Juez de la evaluación | Sí (15 RPM, 500 RPD) |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Indexación y búsqueda | Sí |

- **Límites del plan gratuito:** Google no los publica en la documentación; se ven en AI Studio (`aistudio.google.com/rate-limit`). Relevados el 03/10/2026 para la key del proyecto: los modelos *flash* (`gemini-3.8-flash`, `3.7`, `3.6`, `3.5`, `2.5`) tienen **5 RPM y 20 RPD**; `gemini-3.5-flash-lite` y `gemini-3.1-flash-lite`, **15 RPM y 500 RPD**. Con 20 requests por día el modelo principal original (`gemini-3.8-flash`) no alcanza ni para una evaluación (~180 llamadas al principal), así que se eligieron los dos *lite* de 500 RPD y se reparten los roles entre ellos (la cuota es por modelo).
- **Sin costo:** el proyecto de la key no tiene facturación (plan *Free*): al superar un límite, Gemini devuelve 429 y no cobra. Todo llamado tiene reintentos con *backoff* exponencial ante 429 por minuto y ante 500/503/504 (saturación de Google, frecuente en estos modelos); el 429 por **límite diario** (`…PerDay…`) no se reintenta: corta con `CuotaDiariaAgotada`, la evaluación se detiene y la interfaz muestra un mensaje claro. La evaluación limita los requests por minuto con `EVAL_RPM` (12).
- **Razonamiento:** el modelo rápido usa `thinking_budget=0` (`GEMINI_THINKING_FAST`), porque clasificar no lo necesita. `gemini-3.5-flash-lite` rechaza `thinking_budget=0` (400 `INVALID_ARGUMENT`) y con un presupuesto bajo no cambia la latencia, así que el principal queda con su valor por defecto.
- **Uso de los datos:** en el plan gratuito, Google puede usar el contenido para mejorar sus productos. Se acepta porque los datos son sintéticos (S-04).
- **Parámetros:** `temperature=0` en todos los nodos (los modelos 3.x *lite* usan valores de muestreo fijos e ignoran la temperatura). Timeout de 30 s por llamada, para que una llamada colgada se reintente pronto.

---

## 2. Arquitectura

```
                         ┌──────────────────────────────────────────────┐
                         │ Cliente del agente (interfaz, fuera del SDD) │
                         └───────┬───────────────────────▲──────────────┘
                                 │ invoke / resume        │ RespuestaAgente
                         ┌───────▼───────────────────────┴──────────────┐
                         │ Grafo LangGraph (app/agent)                  │
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
│   └── agent/
│       ├── estado.py             # EstadoAgente (TypedDict) y RespuestaAgente
│       ├── grafo.py              # Construcción del StateGraph
│       ├── nodos/                # Nodos agrupados por ruta (§5.2)
│       └── prompts/              # Prompts versionados en .md
├── data/
│   ├── gimnasio_schema.sql       # Existente (punto 1)
│   ├── gimnasio_datos.sql        # Existente (punto 2) — se modifica (§4.4)
│   ├── agente_schema.sql         # Nuevo: esquemas socio_api y agente
│   └── roles.sql                 # Nuevo: roles y permisos de la app
├── eval/
│   ├── casos.yaml                # Conjunto de evaluación versionado (RF-72)
│   ├── evaluadores.py
│   └── resultados/               # Reportes .md por corrida (RF-75)
├── scripts/
│   ├── reset_db.py               # Recrear la base (usado por la evaluación)
│   ├── indexar_pdfs.py
│   └── run_eval.py
├── tests/
├── rag-source/                   # PDFs existentes
├── requirements.txt
└── .env.example                  # Configuración (§10)
```

---

## 4. Base de datos

### 4.1 Contenedor

Fuera del SDD: la base corre en un contenedor de Docker (`docker-compose.yml`, PostgreSQL 16 sin volumen), que se levanta con el arranque de la aplicación.

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
| `gym_escritor` | Solo `app/ops/ejecucion.py` | `SELECT` sobre las tablas necesarias para validar; `INSERT` y `UPDATE` sobre `socio`; `UPDATE (estado)` sobre `membresia`; `INSERT` sobre `agente.auditoria` (sin `SELECT`: la auditoría se inserta sin `RETURNING`). **Sin `DELETE` en ninguna tabla.** | `statement_timeout = 5s` |

Se revoca todo permiso de `PUBLIC` sobre los esquemas `public`, `socio_api` y `agente`. La extensión `unaccent`
(para comparar nombres sin acentos) se instala en un esquema propio `ext`, con `USAGE` para los tres roles y en
el `search_path` de los lectores (`public, ext` y `socio_api, ext`), así el socio la puede usar sin tener acceso a
`public`. Todas las conexiones fijan `TimeZone = America/Argentina/Buenos_Aires`.

El LLM **nunca** recibe una herramienta que ejecute SQL libre con `gym_escritor`.

### 4.4 Esquema `socio_api` (vistas del perfil Socio)

Las vistas propias filtran por `socio_api.socio_sesion()`, que devuelve `current_setting('app.socio_id', true)::bigint`
(o `NULL` si no se fijó, y entonces las vistas vuelven vacías). La aplicación fija ese
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
| `sesion_clase` | Sesiones con `instructor_nombre` (el del reemplazo si lo hubo, si no el titular) y `con_reemplazo` | Pública |
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
    trace_id        VARCHAR(64)             -- vínculo con la traza, si hay (fuera del SDD)
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
- Se verifica que existan los casos que piden los criterios de aceptación (§9.4). Para CA-58 se agregó una regla
  determinística: los morosos con `id % 4 = 1` tienen una renovación con demora `pendiente` (Full Mensual desde el
  día anterior a la fecha base, con un pago por transferencia pendiente), sin ninguna membresía activa.
- `LOCALTIMESTAMP` se reemplaza por la fecha base a las 12:00.
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
    seudonimo: str                        # HMAC-SHA256(dni, PSEUDONIMO_SECRET)[:12]

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
    citas: list[str]                      # "DOC-0X §N" citados en el texto (RF-10)
    # Agregados en la implementación:
    trace_id: str | None                  # run_id de la traza raíz, para la auditoría
    ejecucion: ResultadoEjecucion | None
    nodos: Annotated[list[str], operator.add]   # recorrido, para el detalle y los tests
    ruta_final: str | None
```

El DNI del usuario **no** forma parte del estado; solo su `id` y su seudónimo.

### 5.2 Nodos

| Nodo | Tipo | Qué hace | Requisitos |
|---|---|---|---|
| `clasificar` | LLM rápido, salida estructurada | Devuelve `Clasificacion` (§5.4). Si la ruta es `directa` o `fuera_dominio`, también devuelve la respuesta, así esos mensajes se resuelven con **una sola llamada al LLM y ninguna herramienta**. | RF-14 a RF-17, RF-20, RF-21 |
| `responder_directo` | Determinístico | Copia la respuesta de la clasificación al estado. No invoca herramientas. | RF-15, RF-16 |
| `rechazar_permiso` | Determinístico | Respuesta fija (en el idioma del mensaje) para un socio que pide escribir o información interna. | RF-40, RF-53 |
| `generar_sql` | LLM principal | Genera una sola sentencia `SELECT` a partir del catálogo del perfil, la fecha de hoy y, si hubo un intento anterior, el error obtenido. | RF-01, RF-06, RF-07, RF-08 |
| `ejecutar_sql` | Herramienta `consulta_sql` | `validar_sql()` y `ejecutar_lectura()` con el rol del perfil (§6). Si falla, vuelve a `generar_sql` hasta 2 veces más. | RF-02, RF-04, RF-05, RNF-05 |
| `buscar_documentos` | Herramienta `busqueda_documentos` | Busca en Chroma con la consulta en castellano que armó `clasificar` (§8.3). | RF-09, RF-12, RF-13 |
| `sintetizar` | LLM principal | Redacta la respuesta con las filas o los fragmentos disponibles, en el idioma del mensaje. Saca las citas del texto y arma las citas y las fuentes en código (§8.4). | RF-10, RF-11, RF-18, RF-19, RF-22 |
| `extraer_operacion` | LLM principal, salida estructurada | Convierte el mensaje en una `OperacionSocio` (§7.1). | §5.5 funcional |
| `validar_operacion` | Determinístico (herramienta `operacion_socio`) | Resuelve el socio, verifica alcance y reglas (§7.2) y arma la `PropuestaCambio`. | RF-41 a RF-45 |
| `confirmar` | `interrupt()` | Pausa el grafo y devuelve la propuesta a quien llamó al agente. Se reanuda con `Command(resume={"decision": ...})`. | RF-46, RF-47 |
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
  `sintetizar` espera a las dos. Como la rama SQL puede reintentar (y tener más pasos que la de documentos),
  `sintetizar` se registra como nodo diferido (`defer=True`): corre una sola vez, cuando no quedan otras tareas.
- `responder_directo` también atiende la ruta `necesita_contexto`. `rechazar_permiso` conserva la ruta que eligió
  el clasificador (por ejemplo `datos` o `escritura`), así la exactitud de ruteo se mide igual en esos casos.
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
- **Confirmar y cancelar son acciones explícitas** (`reanudar()`) que reanudan el hilo de la propuesta. Escribir "sí"
  crea un hilo nuevo, que la clasificación resuelve como `necesita_contexto` (RF-46, CA-57).
- Si llega un mensaje nuevo con una propuesta pendiente, quien llama al agente descarta ese hilo (`descartar()`) y se registra
  la auditoría con resultado `descartada` (RF-47, CA-56).

### 5.6 Respuesta del agente

```python
class RespuestaAgente(BaseModel):
    texto: str
    ruta: str
    herramientas: list[str]
    fuentes: list[str]               # "base de datos" y/o el título de cada documento citado (RF-19)
    citas: list[str]                 # "DOC-0X §N" citados, para el detalle (RF-10)
    sql: str | None                  # None si el perfil es socio (RF-56): se filtra en código
    columnas: list[str] | None
    filas: list[dict] | None         # hasta 50 (RF-04)
    total_filas: int | None
    fragmentos: list[Fragmento]      # doc_id, titulo_doc, seccion, pagina, texto
    propuesta: PropuestaCambio | None
    thread_id: str
    trace_id: str | None
    # Agregados en la implementación:
    idioma: str
    propuesta_pendiente: bool        # el grafo quedó esperando confirmar o cancelar
    resultado_operacion: ResultadoEjecucion | None
    nodos: list[str]
    duracion_seg: float | None
    error: bool
```

Funciones de entrada (`app/agent/grafo.py`): `responder(usuario, mensaje)`, `reanudar(usuario, thread_id,
decision)` (solo si el hilo tiene una propuesta pendiente del mismo usuario; si no, no ejecuta nada, lo que evita
reejecutar al recargar la página) y `descartar(usuario, thread_id)`.

Las filas se devuelven en `filas`, tomadas del resultado de la consulta y no del texto del LLM (RF-03).

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

class CambiosSocio(BaseModel):     # todos opcionales: solo se completan los que se piden cambiar
    nombre, apellido, email, telefono: str | None
    fecha_nacimiento: date | None
    contacto_emergencia, sede_principal: str | None
    dni: str | None                  # solo para detectar el pedido de cambiar el DNI (no permitido)

class ModificacionSocio(BaseModel):
    tipo: Literal["modificacion_socio"]
    socio: ReferenciaSocio
    cambios: CambiosSocio

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

Los campos son opcionales **a propósito** (también los de `ContactoEmergencia`): si falta un dato, el LLM no lo
inventa, lo deja vacío y la validación lo informa (RF-45). El prompt lo indica explícitamente. `cambios` es un
modelo con campos opcionales en lugar de un `dict` porque la salida estructurada de Gemini no admite diccionarios
con claves libres. La salida del LLM se envuelve en `ExtraccionOperacion(operacion: OperacionSocio)`.

Los errores y advertencias de la validación son `Aviso(codigo, params)`; el texto en castellano o inglés lo arman
las plantillas de `app/ops/mensajes.py` (nodo `responder_operacion`), sin pasar por el LLM.

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
| Sede: la del administrador; obligatoria si el administrador no tiene sede; debe existir y estar activa. Un administrador con sede no puede dar de alta en otra sede | OP-01, OP-02 | Error |
| El cambio de sede deja al socio fuera del alcance del administrador | OP-02 | Advertencia |
| Cambio de sede | OP-02 | Advertencia: una vez por período de membresía (DOC-01 §5) |
| DNI no modificable | OP-02 | Error |
| Estado de origen: suspensión ← `activo`; reactivación ← `suspendido` o `baja`; baja ← `activo` o `suspendido` | OP-03 a OP-05 | Error |
| Motivo obligatorio | OP-03, OP-05 | Error |
| Baja con una membresía `activa` o `congelada` y `fecha_fin >= hoy` | OP-05 | Error con la fecha de fin (DC-05) |
| Membresía `pendiente` con `fecha_fin >= hoy` | OP-05 | Efecto secundario: pasa a `cancelada` (advertencia) |
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
7. Se guarda el hash SHA-256 de cada PDF en los metadatos de la colección. La ingesta reindexa
   solo si cambió algún hash o si se fuerza (S-02). Así se evitan llamadas de embeddings
   innecesarias.

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
citar solo con esas etiquetas. Con la respuesta del LLM, el código:

1. Extrae las citas (`DOC-0X §N`) que corresponden a fragmentos recuperados; las inventadas se descartan.
   Van en `citas` (RF-10).
2. Saca las etiquetas del texto: el usuario no las ve en la respuesta, sino en el detalle (RF-10).
3. Arma `fuentes` con "base de datos", si la consulta se ejecutó, y el título de cada documento citado
   (`titulo_doc` de los metadatos, por ejemplo "Manual de Salud, Apto Médico y Rutinas"), sin repetir y en
   el orden en que se citaron. Las agrega al final del texto como "Fuente: …" (RF-19).

Nada de esto sale de texto libre del LLM.

---

## 9. Evaluación

### 9.1 Trazas

Fuera del SDD (criterio de observabilidad de la consigna). El agente funciona sin trazas; si LangSmith está configurado, cada invocación se traza con el seudónimo del usuario (nunca el DNI) y la auditoría guarda el `trace_id`.

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
2. Si LangSmith está configurado (opcional, fuera del SDD), sube o actualiza `casos.yaml` como dataset (`gimnasio-eval`).
3. Ejecuta, en orden y en el mismo proceso, una función objetivo que identifica al usuario, invoca el grafo y,
   si el caso lo pide, reanuda con la acción indicada; calcula los hashes de `socio` y `membresia` y corre las
   consultas de verificación. Si LangSmith está configurado, después registra la corrida con
   `langsmith.evaluate()` sobre el dataset, con las salidas y las métricas ya calculadas (así el orden y la
   restauración de la base no dependen de cómo `evaluate()` recorre los ejemplos).
4. Primero corren los casos de lectura y después los de escritura. Antes y después de **cada** caso de
   escritura se restaura la base desde la plantilla (RF-74).
5. `EVAL_RPM` limita los requests por minuto a Gemini (un limitador global en `app/llm.py` que solo activa la
   evaluación). Los casos con `valido_hasta` vencido se omiten y se informan en el reporte.
6. Cada corrida genera `eval/resultados/<fecha>_<commit>.md` con la tabla de métricas y el detalle por caso
   (RF-73), y un `.json` con el detalle completo. Los dos se versionan, para comparar corridas (RF-75).

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

## 10. Configuración (`.env.example`, RNF-06)

El arranque automatizado (`start.py`, entorno virtual, Docker) y el README quedan fuera del SDD: responden al criterio de aprobación "código documentado y reproducible", no al agente. Acá queda solo la configuración que lee el agente.

```
GOOGLE_API_KEY=
GEMINI_MODEL_FAST=gemini-3.1-flash-lite
GEMINI_MODEL_MAIN=gemini-3.5-flash-lite
GEMINI_MODEL_JUEZ=gemini-3.1-flash-lite
GEMINI_THINKING_FAST=0
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
RAG_DISTANCIA_MAX=0.45

PSEUDONIMO_SECRET=          # se genera si está vacío
EVAL_RPM=12
```

---

## 11. Interfaz

Fuera del SDD: la consigna pide especificar solo el agente. La interfaz consume `RespuestaAgente` y las funciones de entrada del §5.6.

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
| `test_catalogos.py` | Los catálogos cubren todas las tablas o vistas y sus ejemplos se ejecutan con el rol del perfil | Docker |
| `test_evaluacion.py` | Comparación de valores, evaluadores y runner de escritura con LLM simulado | Docker (parcial) |

Los tests que llaman a Gemini se marcan con `@pytest.mark.llm` y no corren por defecto.

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
| RF-50 a RF-56 | `app/auth.py`, §4.3, §4.4, §5.6 |
| RF-72 a RF-76 | §9 |
| RNF-01, RNF-02 | §4.3, §4.4, §6.2, §6.3, §7.3 |
| RNF-03 | §7.3 |
| RNF-04 a RNF-06 | §1.1, §5.3, §10 |
| D-01 | §4.6, `test_datos.py` |

---

## 14. Decisiones técnicas

| ID | Pregunta | Decisión | Estado |
|---|---|---|---|
| DT-01 | Fecha base de los datos fijos | `2026-10-16` (día de la entrega). Los eventos quedan entre agosto y el 16/10, y las sesiones de "la próxima semana" llegan al 23/10 (primer coloquio). Mientras se desarrolla antes de esa fecha, hay datos posteriores a la fecha real; es un efecto aceptado. | Cerrada |
| DT-02 | ¿Qué DNI se usa en cada caso de evaluación y en las preguntas de ejemplo? | Se eligen de los datos cargados, un socio representativo de cada situación, y quedan documentados en el encabezado de `eval/casos.yaml` y en el README. | Cerrada |
| DT-03 | En la **suspensión**, ¿qué membresías se cancelan? | La `activa`, la `congelada` y la `pendiente` con `fecha_fin >= hoy`. Un socio suspendido no puede usar ninguna. Se refleja en OP-03 del spec funcional (v0.4). | Cerrada |
| DT-04 | Valor de `RAG_DISTANCIA_MAX` | `0.45`. Con `gemini-embedding-001` las distancias coseno están muy juntas: el mejor fragmento relevante queda entre 0,18 y 0,25, pero otros fragmentos necesarios aparecen hasta 0,36 (la tabla de sanciones de DOC-01 §7 para "¿qué pasa si un socio presta su QR?"), y los de un tema no cubierto (mascotas, CA-24) arrancan en 0,27. El umbral no separa por sí solo: se deja permisivo (0,35 cortaba fragmentos necesarios) y `sintetizar` decide si los fragmentos cubren la pregunta (RF-11). | Cerrada |
| DT-05 | ¿Se cumple RNF-04 (p90 < 15 s) con los modelos del plan gratuito? | No. Corrida completa del 03/10/2026 (`eval/resultados/2026-10-03_2046_f00bf93.md`, 58 casos): mediana 11,5 s y p90 40,4 s. La cola la arman las demoras de Gemini, no el grafo: los 6 casos de más de 40 s tuvieron una llamada colgada hasta el timeout de 30 s y un reintento, y hubo 8 timeouts y 7 respuestas 503 en total. Sin los casos con incidentes de Gemini (45 de 58), la mediana es 10,6 s y el p90 24,9 s, todavía por encima del objetivo: los modelos *lite* del plan gratuito tardan de 4 a 8 s por llamada y una consulta de datos o híbrida encadena 3 o 4. Se mantiene el timeout de 30 s: bajarlo recorta la cola, pero hace reintentar respuestas largas que sí iban a llegar. Se acepta como limitación conocida (DC-10 del spec funcional); con un modelo pago o un plan con más cuota, el objetivo es alcanzable sin cambios de diseño. | Cerrada |

---

## 15. Historial de cambios

| Versión | Fecha | Cambio |
|---|---|---|
| 0.1 | 2026-10-03 | Borrador inicial: Gemini, LangGraph, Streamlit, LangSmith, Chroma y Postgres en Docker con arranque por `scripts/start.py`. |
| 0.2 | 2026-10-03 | Se cierran DT-01 (fecha base 16/10/2026) y DT-03 (la suspensión cancela las membresías activa, congelada y pendiente). |
| 0.3 | 2026-10-03 | §10: arranque totalmente automatizado con `start.py` en la raíz (entorno virtual, `.env` con secretos generados, pedido de API keys, inicio de Docker, puerto libre, `--wait`, DNI de ejemplo y `--evaluar`). |
| 0.4 | 2026-10-03 | Ajustes de implementación: extensión `unaccent` en el esquema `ext`, zona horaria fija en las conexiones y auditoría sin `RETURNING` (§4.3); función `socio_api.socio_sesion()` y columna `con_reemplazo` (§4.4); regla de datos para CA-58 (§4.6); claves extra del estado y de `RespuestaAgente` y funciones de entrada (§5.1, §5.6); `sintetizar` diferido y ruta conservada en el rechazo por permisos (§5.3); `CambiosSocio` en lugar de `dict` y avisos con plantillas es/en (§7.1); precisiones de sede y membresía pendiente (§7.2); runner de evaluación local con registro posterior en LangSmith y `EVAL_RPM` como límite de requests a Gemini (§9.3); tests adicionales (§12). |
| 0.5 | 2026-10-03 | §1.1: modelos del plan gratuito según los límites relevados en AI Studio (los *flash* tienen 20 RPD; se pasa a `gemini-3.5-flash-lite` como principal y `gemini-3.1-flash-lite` como rápido y juez, 500 RPD cada uno), modelo juez separado, corte ante la cuota diaria, `thinking_budget=0` solo en el rápido y timeout de 30 s. Se cierran DT-02 y DT-04 (`RAG_DISTANCIA_MAX=0.45`). |
| 0.6 | 2026-10-04 | Cierre: DT-05 (RNF-04 medido en la corrida completa y aceptado como limitación del plan gratuito). §11: la interfaz queda fuera del SDD (la consigna lo pide solo para el agente); se quitan T-04, la tabla de Streamlit, `app/ui/` del §3, `test_ui.py` del §12 y la fila de RF-60 a RF-67 de la trazabilidad, y §2, §5.5 y §5.6 se redactan sin depender de la interfaz. Por el mismo motivo salen el arranque y la infraestructura (T-14, §4.1 y §10 quedan como notas; §10 conserva solo la configuración) y las trazas (§9.1 queda como nota; T-05 pasa a ser el runner propio de evaluación, con LangSmith opcional). Se quitan las referencias a RF-77 (eliminado del spec funcional v0.5). |
| 0.7 | 2026-10-04 | §8.4: las citas se sacan del texto de la respuesta y viajan en `citas` (estado y `RespuestaAgente`); `fuentes` usa el título de cada documento citado (`titulo_doc` en `Fragmento`). Sigue a la v0.6 del spec funcional. |
