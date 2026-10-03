# Catálogo de la base — perfil Socio

El usuario es un **socio** del gimnasio. Solo podés consultar las vistas de esta lista (se usan con
el nombre simple, sin esquema). Las vistas cuyo nombre empieza con `mi_` o `mis_` ya están
filtradas: devuelven **solo** las filas del socio que pregunta, así que **nunca** hace falta
filtrar por su id, DNI ni nombre. "Mi", "mis", "yo", "tengo" se refieren siempre a esas vistas.

No existen datos de otros socios, de los empleados (salvo el nombre del instructor), de pagos de
terceros ni métricas del negocio. Si la pregunta los pide, no hay ninguna vista que los tenga.

## Vistas públicas (información del gimnasio)

### sede — sedes (3)
- `id` · `nombre` ('Sede Centro' id 1, 'Sede Norte' id 2, 'Sede Sur' id 3) · `direccion` · `telefono` · `activa`

### sala
- `id` · `sede_id` → sede · `nombre` · `capacidad`

### actividad — actividades grupales (10)
- `id` · `nombre` ('Spinning', 'Yoga', 'Funcional', 'Pilates', 'Zumba', 'Crossfit', 'Stretching', 'Boxeo', 'GAP', 'HIIT') · `descripcion`

### plan — planes que se venden
- `id` · `nombre` ('Musculación Mensual', 'Musculación Trimestral', 'Full Mensual', 'Full Trimestral', 'Full Anual',
  'Solo Clases 2x semana', 'Clases 3x semana + Sala', 'Pase Libre Multisede', 'Plan Estudiante', 'Promo Verano')
- `precio` (precio de lista vigente) · `duracion_dias` · `clases_por_semana` (NULL = sin tope fijo)
- `incluye_musculacion` · `todas_las_sedes` (multisede) · `activo` (FALSE = discontinuado)

### plan_actividad — actividades que habilita cada plan
- `plan_id` → plan · `actividad_id` → actividad (los planes de musculación no tienen filas: no incluyen clases)

### ejercicio — catálogo de ejercicios
- `id` · `nombre` · `grupo_muscular` ('Pecho', 'Espalda', 'Hombros', 'Brazos', 'Piernas', 'Glúteos', 'Core') · `descripcion`

### clase — grilla semanal
- `id` · `actividad_id` → actividad · `sala_id` → sala (la sede sale de sala.sede_id) · `instructor_nombre`
- `dia_semana` 1=lunes … 7=domingo (igual que `extract(isodow …)`) · `hora_inicio` TIME · `duracion_min` · `cupo`
- `vigente_desde` · `vigente_hasta` (NULL = vigente)

### sesion_clase — cada fecha en que se dicta una clase
- `id` · `clase_id` → clase · `fecha` DATE · `estado` ('programada' | 'realizada' | 'cancelada')
- `con_reemplazo` BOOLEAN · `instructor_nombre` (el que dicta esa fecha)

### ocupacion_sesion — ocupación agregada de cada sesión (sin datos de personas)
- `sesion_id` → sesion_clase · `cupo` · `confirmadas` · `en_lista_espera` · `lugares_disponibles`

## Vistas propias (solo del socio que pregunta)

### mi_socio — sus datos personales (una fila)
- `id` · `dni` · `nombre` · `apellido` · `email` · `telefono` · `fecha_nacimiento` · `contacto_emergencia`
- `sede_principal_id` → sede · `fecha_alta` · `estado` ('activo' | 'suspendido' | 'baja')

### mis_aptos — sus aptos médicos (vale el de mayor fecha de vencimiento)
- `id` · `fecha_emision` · `fecha_vencimiento` · `medico` · `archivo_url`
- `observaciones`: NULL | 'Apto sin restricciones' | 'Apto con control cardiológico anual' | 'Evitar ejercicios de alto impacto'

### mis_membresias — sus membresías
- `id` · `plan_id` → plan · `fecha_inicio` · `fecha_fin` (inclusive) · `precio_pactado`
- `estado`: 'pendiente' | 'activa' | 'vencida' | 'cancelada' | 'congelada'

### mis_pagos — sus pagos
- `id` · `membresia_id` → mis_membresias · `monto` · `fecha_pago` · `metodo` ('efectivo' | 'debito' | 'credito' | 'transferencia' | 'mercadopago')
- `estado` ('pendiente' | 'aprobado' | 'rechazado' | 'reintegrado') · `referencia_ext`

### mis_reservas — sus reservas de clases
- `id` · `sesion_id` → sesion_clase · `creada_en` · `estado` ('confirmada' | 'lista_espera' | 'cancelada') · `asistio`

### mis_accesos — sus ingresos al gimnasio
- `id` · `sede_id` → sede · `ingreso` · `egreso` · `metodo` ('qr' | 'tarjeta' | 'huella' | 'manual') · `permitido`
- `motivo_rechazo`: 'cuota vencida' | 'membresía congelada' | 'membresía cancelada' | 'sin apto médico'

### mis_rutinas — sus rutinas de musculación
- `id` · `nombre` (p. ej. 'Rutina 2 días - Fuerza') · `objetivo` · `fecha_inicio` · `fecha_fin` · `activa` · `instructor_nombre`

### mis_rutina_ejercicios — ejercicios de sus rutinas
- `rutina_id` → mis_rutinas · `ejercicio_id` → ejercicio · `dia` · `orden` · `series` · `repeticiones` · `peso_kg` · `descanso_seg`

## Semántica del dominio

| Concepto | Definición SQL |
|---|---|
| Membresía vigente | `estado IN ('activa','congelada') AND CURRENT_DATE BETWEEN fecha_inicio AND fecha_fin` |
| "Mi cuota / mi membresía" | la vigente; si no hay, la de mayor `fecha_fin` (excluyendo 'pendiente'); mencionar también una 'pendiente' si existe |
| Apto vigente | `fecha_vencimiento >= CURRENT_DATE` |
| Asistencia a una clase | `estado = 'confirmada' AND asistio = TRUE` |
| Grilla vigente | `vigente_hasta IS NULL OR vigente_hasta >= CURRENT_DATE` |
| Mi sede | `mi_socio.sede_principal_id` |
| Rutina actual | `mis_rutinas.activa = TRUE`; la cantidad de días es `max(dia)` de sus ejercicios |

Fechas relativas: "hoy" = `CURRENT_DATE`; "este mes" = desde `date_trunc('month', CURRENT_DATE)`;
"la semana" va de lunes a domingo (`date_trunc('week', CURRENT_DATE)`); "próxima clase" = la sesión
con `fecha >= CURRENT_DATE` y `estado = 'programada'` más cercana.

Sinónimos: "cuota", "abono" → membresía; "MP" → 'mercadopago'; "la sede del centro" → 'Sede Centro';
"profe" → instructor. Nombres sin acentos: `unaccent(lower(nombre)) = unaccent(lower('...'))`.

## Ejemplos

```sql
-- ¿Cuándo vence mi membresía?
SELECT p.nombre AS plan, m.estado, m.fecha_inicio, m.fecha_fin
FROM mis_membresias m JOIN plan p ON p.id = m.plan_id
WHERE m.estado <> 'pendiente'
ORDER BY (m.estado IN ('activa','congelada') AND CURRENT_DATE BETWEEN m.fecha_inicio AND m.fecha_fin) DESC, m.fecha_fin DESC
LIMIT 1;

-- ¿A cuántas clases fui este mes?
SELECT count(*) AS clases_asistidas
FROM mis_reservas r JOIN sesion_clase s ON s.id = r.sesion_id
WHERE r.estado = 'confirmada' AND r.asistio
  AND s.fecha >= date_trunc('month', CURRENT_DATE) AND s.fecha < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month';

-- ¿Qué observación tiene mi apto médico y hasta cuándo vale?
SELECT fecha_emision, fecha_vencimiento, observaciones, (fecha_vencimiento >= CURRENT_DATE) AS vigente
FROM mis_aptos ORDER BY fecha_vencimiento DESC LIMIT 1;

-- ¿Puedo hacer Crossfit con mi plan? (actividades del plan de mi membresía actual)
SELECT p.nombre AS plan, a.nombre AS actividad, (pa.actividad_id IS NOT NULL) AS incluida
FROM (SELECT plan_id FROM mis_membresias WHERE estado <> 'pendiente' ORDER BY fecha_fin DESC LIMIT 1) m
JOIN plan p ON p.id = m.plan_id
CROSS JOIN actividad a
LEFT JOIN plan_actividad pa ON pa.plan_id = p.id AND pa.actividad_id = a.id
WHERE a.nombre = 'Crossfit';

-- ¿Cuántos lugares quedan en la próxima clase de Zumba de mi sede?
SELECT s.fecha, to_char(c.hora_inicio, 'HH24:MI') AS hora, se.nombre AS sede, o.cupo, o.confirmadas, o.lugares_disponibles
FROM sesion_clase s
JOIN clase c ON c.id = s.clase_id JOIN actividad a ON a.id = c.actividad_id
JOIN sala sa ON sa.id = c.sala_id JOIN sede se ON se.id = sa.sede_id
JOIN ocupacion_sesion o ON o.sesion_id = s.id
WHERE a.nombre = 'Zumba' AND sa.sede_id = (SELECT sede_principal_id FROM mi_socio)
  AND s.estado = 'programada' AND s.fecha >= CURRENT_DATE
ORDER BY s.fecha, c.hora_inicio LIMIT 1;

-- ¿Cuál es mi rutina y cuántos días tiene?
SELECT r.nombre, r.objetivo, r.instructor_nombre, max(re.dia) AS dias
FROM mis_rutinas r LEFT JOIN mis_rutina_ejercicios re ON re.rutina_id = r.id
WHERE r.activa
GROUP BY r.id, r.nombre, r.objetivo, r.instructor_nombre;

-- ¿Por qué me rechazaron la entrada la última vez?
SELECT a.ingreso, se.nombre AS sede, a.motivo_rechazo
FROM mis_accesos a JOIN sede se ON se.id = a.sede_id
WHERE NOT a.permitido ORDER BY a.ingreso DESC LIMIT 1;
```
