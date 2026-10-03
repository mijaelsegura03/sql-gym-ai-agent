# Catálogo de la base — perfil Administrador

Base PostgreSQL 16 de un gimnasio con tres sedes. El administrador puede leer **todas** las tablas
del esquema `public` y la tabla `agente.auditoria`. Todas las consultas son de solo lectura.

## Tablas

### sede — sedes del gimnasio (3)
- `id` BIGINT PK · `nombre` ('Sede Centro' id 1, 'Sede Norte' id 2, 'Sede Sur' id 3) · `direccion` · `telefono` · `activa` BOOLEAN

### sala — salas de cada sede
- `id` PK · `sede_id` → sede · `nombre` ('Sala Spinning', 'Salón Multiuso', 'Sala Funcional', 'Sala de Musculación') · `capacidad` INT

### socio — socios del gimnasio
- `id` PK · `dni` VARCHAR único · `nombre` · `apellido` · `email` único (puede ser NULL) · `telefono`
- `fecha_nacimiento` DATE · `contacto_emergencia` texto libre "Nombre Apellido (Parentesco) - teléfono" (puede ser NULL)
- `sede_principal_id` → sede · `fecha_alta` DATE
- `estado`: 'activo' | 'suspendido' | 'baja'

### empleado — personal
- `id` PK · `dni` · `nombre` · `apellido` · `email` · `sede_id` → sede (NULL = administración central, todas las sedes)
- `rol`: 'instructor' | 'recepcion' | 'administracion' | 'gerente' · `activo` BOOLEAN

### apto_medico — certificados médicos (un socio puede tener varios; vale el último)
- `id` PK · `socio_id` → socio · `fecha_emision` DATE · `fecha_vencimiento` DATE (emisión + 12 meses)
- `medico` · `archivo_url`
- `observaciones`: NULL | 'Apto sin restricciones' | 'Apto con control cardiológico anual' | 'Evitar ejercicios de alto impacto'

### actividad — actividades grupales (10)
- `id` PK · `nombre` único ('Spinning', 'Yoga', 'Funcional', 'Pilates', 'Zumba', 'Crossfit', 'Stretching', 'Boxeo', 'GAP', 'HIIT') · `descripcion`

### plan — planes que se venden
- `id` PK · `nombre` ('Musculación Mensual', 'Musculación Trimestral', 'Full Mensual', 'Full Trimestral', 'Full Anual',
  'Solo Clases 2x semana', 'Clases 3x semana + Sala', 'Pase Libre Multisede', 'Plan Estudiante', 'Promo Verano')
- `precio` NUMERIC: **precio de lista vigente** · `duracion_dias` INT · `clases_por_semana` INT (NULL = sin tope fijo)
- `incluye_musculacion` BOOLEAN · `todas_las_sedes` BOOLEAN (multisede) · `activo` BOOLEAN (FALSE = discontinuado; 'Promo Verano')

### plan_actividad — actividades que habilita cada plan (N:M)
- `plan_id` → plan · `actividad_id` → actividad. Los planes de musculación no tienen filas (no incluyen clases).

### membresia — contratación de un plan por un socio
- `id` PK · `socio_id` → socio · `plan_id` → plan · `fecha_inicio` DATE · `fecha_fin` DATE (inclusive)
- `precio_pactado` NUMERIC (precio de lista del día en que se contrató, con descuento si hubo)
- `estado`: 'pendiente' | 'activa' | 'vencida' | 'cancelada' | 'congelada'

### pago — pagos de una membresía
- `id` PK · `membresia_id` → membresia · `monto` NUMERIC · `fecha_pago` TIMESTAMP
- `metodo`: 'efectivo' | 'debito' | 'credito' | 'transferencia' | 'mercadopago'
- `estado`: 'pendiente' | 'aprobado' | 'rechazado' | 'reintegrado'
- `referencia_ext` · `registrado_por` → empleado (NULL en los pagos de MercadoPago, que son online)

### clase — grilla semanal (cada clase se repite un día fijo a una hora fija)
- `id` PK · `actividad_id` → actividad · `sala_id` → sala (la sede sale de sala.sede_id) · `instructor_id` → empleado (titular)
- `dia_semana` SMALLINT 1=lunes … 7=domingo (igual que `extract(isodow …)`) · `hora_inicio` TIME · `duracion_min` · `cupo`
- `vigente_desde` DATE · `vigente_hasta` DATE (NULL = vigente; con fecha = discontinuada)

### sesion_clase — cada fecha en que se dicta una clase
- `id` PK · `clase_id` → clase · `fecha` DATE · `instructor_id` → empleado (reemplazante; NULL = dicta el titular)
- `estado`: 'programada' | 'realizada' | 'cancelada'

### reserva — reservas de socios a sesiones
- `id` PK · `sesion_id` → sesion_clase · `socio_id` → socio · `creada_en` TIMESTAMP
- `estado`: 'confirmada' | 'lista_espera' | 'cancelada' · `asistio` BOOLEAN (NULL si no aplica)

### acceso — intentos de ingreso por el molinete
- `id` PK · `socio_id` → socio · `sede_id` → sede · `ingreso` TIMESTAMP · `egreso` TIMESTAMP (puede ser NULL)
- `metodo`: 'qr' | 'tarjeta' | 'huella' | 'manual' · `permitido` BOOLEAN
- `motivo_rechazo` (solo si permitido = FALSE): 'cuota vencida' | 'membresía congelada' | 'membresía cancelada' | 'sin apto médico'

### ejercicio — catálogo de ejercicios
- `id` PK · `nombre` · `grupo_muscular` ('Pecho', 'Espalda', 'Hombros', 'Brazos', 'Piernas', 'Glúteos', 'Core') · `descripcion`

### rutina — rutinas de musculación de los socios
- `id` PK · `socio_id` → socio · `instructor_id` → empleado (puede ser NULL) · `nombre` (p. ej. 'Rutina 2 días - Fuerza')
- `objetivo` ('Hipertrofia', 'Fuerza', 'Pérdida de peso', 'Tonificación', 'Resistencia', 'Salud general', 'Rehabilitación')
- `fecha_inicio` · `fecha_fin` (NULL si sigue) · `activa` BOOLEAN (una sola activa por socio)

### rutina_ejercicio — ejercicios de cada rutina
- `rutina_id` → rutina · `ejercicio_id` → ejercicio · `dia` (1, 2 o 3) · `orden` · `series` · `repeticiones` (texto: '12', '8-10', '30s')
- `peso_kg` NUMERIC (NULL en core) · `descanso_seg`

### agente.auditoria — operaciones de escritura hechas con el agente
- `id` · `fecha` · `empleado_id` → empleado · `operacion` ('alta_socio', 'modificacion_socio', 'suspension', 'reactivacion', 'baja')
- `socio_id` → socio · `resultado` ('ejecutada', 'cancelada', 'descartada', 'rechazada', 'fallida') · `motivo` · `valores_antes` JSONB · `valores_despues` JSONB · `detalle`

## Semántica del dominio (usar SIEMPRE estas definiciones)

El estado guardado de una membresía no alcanza para saber si está vigente hoy: hay que mirar las fechas.

| Concepto | Definición SQL |
|---|---|
| Membresía vigente | `m.estado IN ('activa','congelada') AND CURRENT_DATE BETWEEN m.fecha_inicio AND m.fecha_fin` |
| Membresía actual de un socio | la vigente; si no tiene, la de mayor `fecha_fin` |
| Socio moroso (con la cuota vencida) | socio `estado = 'activo'` **sin** membresía vigente cuya última membresía (excluyendo 'pendiente') terminó hace 30 días o menos (`fecha_fin >= CURRENT_DATE - 30` y `fecha_fin < CURRENT_DATE`) |
| Apto vigente | existe un `apto_medico` con `fecha_vencimiento >= CURRENT_DATE` |
| Apto vencido | el socio no tiene apto vigente: `max(fecha_vencimiento) < CURRENT_DATE` (o no tiene ningún apto) |
| Pago válido para recaudación / facturación | `pago.estado = 'aprobado'` (se suma `monto` por `fecha_pago`) |
| Asistencia a una clase | `reserva.estado = 'confirmada' AND reserva.asistio = TRUE` |
| Lugares disponibles en una sesión | `clase.cupo - count(reservas 'confirmada' de la sesión)` |
| Grilla vigente | `clase.vigente_hasta IS NULL OR clase.vigente_hasta >= CURRENT_DATE` |
| Sede de una clase | `clase → sala → sede` |

Fechas relativas: "hoy" = `CURRENT_DATE`; "este mes" = desde `date_trunc('month', CURRENT_DATE)`;
"el mes pasado" = `[date_trunc('month', CURRENT_DATE) - INTERVAL '1 month', date_trunc('month', CURRENT_DATE))`;
"esta semana" = de lunes a domingo: `date_trunc('week', CURRENT_DATE)` (PostgreSQL empieza la semana el lunes);
"los últimos 30 días" = `>= CURRENT_DATE - 30`. Para TIMESTAMP comparar con rangos semiabiertos (`>= inicio AND < fin`).

## Sinónimos del dominio

- "moroso", "deudor", "con la cuota vencida", "debe la cuota" → socio moroso (definición de arriba).
- "MP", "Mercado Pago" → `metodo = 'mercadopago'`; "tarjeta" → 'debito' o 'credito'; "transferencia bancaria" → 'transferencia'.
- "la sede del centro", "Centro" → 'Sede Centro'; "Norte" → 'Sede Norte'; "Sur" → 'Sede Sur'.
- "cuota", "abono", "suscripción" → membresía. "facturó", "recaudó", "ingresos" → suma de pagos aprobados.
- "profe", "profesor" → empleado con rol 'instructor'. "clase" puede ser la grilla (`clase`) o una fecha concreta (`sesion_clase`).
- "ingresos", "entradas", "visitas" al gimnasio → `acceso` con `permitido = TRUE`. "rechazos" → `permitido = FALSE`.
- Comparar nombres sin distinguir mayúsculas ni acentos: `unaccent(lower(nombre)) = unaccent(lower('...'))` o `ILIKE`.

## Ejemplos

```sql
-- ¿Cuántos socios activos tiene cada sede?
SELECT se.nombre AS sede, count(*) AS socios_activos
FROM socio s JOIN sede se ON se.id = s.sede_principal_id
WHERE s.estado = 'activo'
GROUP BY se.nombre ORDER BY se.nombre;

-- ¿Cuánto se recaudó con MercadoPago el mes pasado?
SELECT coalesce(sum(p.monto), 0) AS total
FROM pago p
WHERE p.metodo = 'mercadopago' AND p.estado = 'aprobado'
  AND p.fecha_pago >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND p.fecha_pago <  date_trunc('month', CURRENT_DATE);

-- ¿Qué socios tienen el apto médico vencido?
SELECT s.dni, s.nombre || ' ' || s.apellido AS socio, max(a.fecha_vencimiento) AS vencio
FROM socio s LEFT JOIN apto_medico a ON a.socio_id = s.id
WHERE s.estado = 'activo'
GROUP BY s.id, s.dni, s.nombre, s.apellido
HAVING coalesce(max(a.fecha_vencimiento), DATE '1900-01-01') < CURRENT_DATE
ORDER BY vencio NULLS FIRST;

-- ¿Qué clases de Spinning hay en la Sede Norte?
SELECT CASE c.dia_semana WHEN 1 THEN 'Lunes' WHEN 2 THEN 'Martes' WHEN 3 THEN 'Miércoles' WHEN 4 THEN 'Jueves'
            WHEN 5 THEN 'Viernes' WHEN 6 THEN 'Sábado' ELSE 'Domingo' END AS dia,
       to_char(c.hora_inicio, 'HH24:MI') AS hora, sa.nombre AS sala, e.nombre || ' ' || e.apellido AS instructor, c.cupo
FROM clase c
JOIN actividad a ON a.id = c.actividad_id
JOIN sala sa ON sa.id = c.sala_id JOIN sede se ON se.id = sa.sede_id
JOIN empleado e ON e.id = c.instructor_id
WHERE a.nombre = 'Spinning' AND se.nombre = 'Sede Norte'
  AND (c.vigente_hasta IS NULL OR c.vigente_hasta >= CURRENT_DATE)
ORDER BY c.dia_semana, c.hora_inicio;

-- ¿Cuándo vence la membresía de Juan? (filtro por nombre: devolver DNI y nombre completo para detectar homónimos)
SELECT s.dni, s.nombre || ' ' || s.apellido AS socio, p.nombre AS plan, m.estado, m.fecha_fin
FROM socio s
JOIN membresia m ON m.socio_id = s.id
JOIN plan p ON p.id = m.plan_id
WHERE unaccent(lower(s.nombre)) = unaccent(lower('Juan'))
  AND m.id = (SELECT m2.id FROM membresia m2 WHERE m2.socio_id = s.id AND m2.estado <> 'pendiente'
              ORDER BY m2.fecha_fin DESC LIMIT 1)
ORDER BY s.apellido;

-- ¿Quiénes son los socios morosos?
SELECT s.dni, s.nombre || ' ' || s.apellido AS socio, s.telefono, max(m.fecha_fin) AS vencio
FROM socio s JOIN membresia m ON m.socio_id = s.id AND m.estado <> 'pendiente'
WHERE s.estado = 'activo'
  AND NOT EXISTS (SELECT 1 FROM membresia v WHERE v.socio_id = s.id AND v.estado IN ('activa','congelada')
                  AND CURRENT_DATE BETWEEN v.fecha_inicio AND v.fecha_fin)
GROUP BY s.id, s.dni, s.nombre, s.apellido, s.telefono
HAVING max(m.fecha_fin) BETWEEN CURRENT_DATE - 30 AND CURRENT_DATE - 1
ORDER BY vencio;

-- ¿Por qué le rechazaron el ingreso al socio con DNI 34896217? (último acceso rechazado)
SELECT s.dni, s.nombre || ' ' || s.apellido AS socio, a.ingreso, se.nombre AS sede, a.motivo_rechazo
FROM acceso a JOIN socio s ON s.id = a.socio_id JOIN sede se ON se.id = a.sede_id
WHERE s.dni = '34896217' AND NOT a.permitido
ORDER BY a.ingreso DESC LIMIT 1;

-- Ocupación de las sesiones de la semana que viene en la Sede Centro
SELECT sc.fecha, to_char(c.hora_inicio, 'HH24:MI') AS hora, a.nombre AS actividad, c.cupo,
       count(r.id) FILTER (WHERE r.estado = 'confirmada') AS confirmadas,
       c.cupo - count(r.id) FILTER (WHERE r.estado = 'confirmada') AS lugares_disponibles
FROM sesion_clase sc
JOIN clase c ON c.id = sc.clase_id JOIN actividad a ON a.id = c.actividad_id
JOIN sala sa ON sa.id = c.sala_id JOIN sede se ON se.id = sa.sede_id
LEFT JOIN reserva r ON r.sesion_id = sc.id
WHERE se.nombre = 'Sede Centro' AND sc.estado <> 'cancelada'
  AND sc.fecha >= date_trunc('week', CURRENT_DATE)::date + 7 AND sc.fecha < date_trunc('week', CURRENT_DATE)::date + 14
GROUP BY sc.id, sc.fecha, c.hora_inicio, a.nombre, c.cupo
ORDER BY sc.fecha, c.hora_inicio;
```
