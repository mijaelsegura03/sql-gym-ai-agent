-- =====================================================================
--  agente_schema.sql  -  Esquemas que agrega el agente (spec técnico §4.4 y §4.5)
--
--  * socio_api: vistas que lee el perfil Socio. Las públicas copian las tablas
--    de catálogo (sin datos de personas, salvo el nombre del instructor); las
--    propias ("mi_", "mis_") filtran por el socio de la sesión, que la aplicación
--    fija con set_config('app.socio_id', <id>, true) dentro de la transacción.
--    Sin ese valor, current_setting(..., true) devuelve NULL y las vistas propias
--    vuelven vacías.
--  * agente: tabla de auditoría de las operaciones de escritura (RF-49).
--
--  Las vistas son propiedad del superusuario y se crean con security_barrier:
--  el rol gym_lector_socio lee a través de ellas sin permisos sobre public.
--  Se ejecuta después de gimnasio_schema.sql y gimnasio_datos.sql.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS socio_api;
CREATE SCHEMA IF NOT EXISTS agente;

-- unaccent: comparar nombres sin acentos ("Lucia" = "Lucía"). Va en un esquema propio para
-- que el socio pueda usarla sin tener acceso a public.
CREATE SCHEMA IF NOT EXISTS ext;
CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA ext;

-- ---------------------------------------------------------------------
-- Socio de la sesión (NULL si la aplicación no lo fijó)
-- ---------------------------------------------------------------------
CREATE FUNCTION socio_api.socio_sesion() RETURNS BIGINT
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.socio_id', true), '')::bigint
$$;

-- ---------------------------------------------------------------------
-- Vistas públicas (copia directa de los catálogos)
-- ---------------------------------------------------------------------
CREATE VIEW socio_api.sede WITH (security_barrier) AS
  SELECT id, nombre, direccion, telefono, activa FROM public.sede;

CREATE VIEW socio_api.sala WITH (security_barrier) AS
  SELECT id, sede_id, nombre, capacidad FROM public.sala;

CREATE VIEW socio_api.actividad WITH (security_barrier) AS
  SELECT id, nombre, descripcion FROM public.actividad;

CREATE VIEW socio_api.plan WITH (security_barrier) AS
  SELECT id, nombre, precio, duracion_dias, clases_por_semana,
         incluye_musculacion, todas_las_sedes, activo
  FROM public.plan;

CREATE VIEW socio_api.plan_actividad WITH (security_barrier) AS
  SELECT plan_id, actividad_id FROM public.plan_actividad;

CREATE VIEW socio_api.ejercicio WITH (security_barrier) AS
  SELECT id, nombre, grupo_muscular, descripcion FROM public.ejercicio;

-- Grilla de clases: del instructor solo se expone el nombre (sin DNI, email ni rol)
CREATE VIEW socio_api.clase WITH (security_barrier) AS
  SELECT c.id, c.actividad_id, c.sala_id, c.dia_semana, c.hora_inicio, c.duracion_min,
         c.cupo, c.vigente_desde, c.vigente_hasta,
         e.nombre || ' ' || e.apellido AS instructor_nombre
  FROM public.clase c
  JOIN public.empleado e ON e.id = c.instructor_id;

-- Sesiones: instructor_nombre es el del reemplazo si lo hubo, si no el titular
CREATE VIEW socio_api.sesion_clase WITH (security_barrier) AS
  SELECT s.id, s.clase_id, s.fecha, s.estado,
         (s.instructor_id IS NOT NULL) AS con_reemplazo,
         COALESCE(er.nombre || ' ' || er.apellido, et.nombre || ' ' || et.apellido) AS instructor_nombre
  FROM public.sesion_clase s
  JOIN public.clase c         ON c.id = s.clase_id
  JOIN public.empleado et     ON et.id = c.instructor_id
  LEFT JOIN public.empleado er ON er.id = s.instructor_id;

-- Ocupación agregada por sesión, sin identidades (RF-54)
CREATE VIEW socio_api.ocupacion_sesion WITH (security_barrier) AS
  SELECT s.id AS sesion_id,
         c.cupo,
         count(r.id) FILTER (WHERE r.estado = 'confirmada')   AS confirmadas,
         count(r.id) FILTER (WHERE r.estado = 'lista_espera') AS en_lista_espera,
         GREATEST(c.cupo - count(r.id) FILTER (WHERE r.estado = 'confirmada'), 0) AS lugares_disponibles
  FROM public.sesion_clase s
  JOIN public.clase c       ON c.id = s.clase_id
  LEFT JOIN public.reserva r ON r.sesion_id = s.id
  GROUP BY s.id, c.cupo;

-- ---------------------------------------------------------------------
-- Vistas propias (solo filas del socio de la sesión)
-- ---------------------------------------------------------------------
CREATE VIEW socio_api.mi_socio WITH (security_barrier) AS
  SELECT id, dni, nombre, apellido, email, telefono, fecha_nacimiento,
         contacto_emergencia, sede_principal_id, fecha_alta, estado
  FROM public.socio
  WHERE id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_aptos WITH (security_barrier) AS
  SELECT id, socio_id, fecha_emision, fecha_vencimiento, medico, archivo_url, observaciones
  FROM public.apto_medico
  WHERE socio_id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_membresias WITH (security_barrier) AS
  SELECT id, socio_id, plan_id, fecha_inicio, fecha_fin, precio_pactado, estado
  FROM public.membresia
  WHERE socio_id = socio_api.socio_sesion();

-- Sin registrado_por: quién registró el pago es información interna
CREATE VIEW socio_api.mis_pagos WITH (security_barrier) AS
  SELECT p.id, p.membresia_id, p.monto, p.fecha_pago, p.metodo, p.estado, p.referencia_ext
  FROM public.pago p
  JOIN public.membresia m ON m.id = p.membresia_id
  WHERE m.socio_id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_reservas WITH (security_barrier) AS
  SELECT id, sesion_id, socio_id, creada_en, estado, asistio
  FROM public.reserva
  WHERE socio_id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_accesos WITH (security_barrier) AS
  SELECT id, socio_id, sede_id, ingreso, egreso, metodo, permitido, motivo_rechazo
  FROM public.acceso
  WHERE socio_id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_rutinas WITH (security_barrier) AS
  SELECT r.id, r.socio_id, r.nombre, r.objetivo, r.fecha_inicio, r.fecha_fin, r.activa,
         e.nombre || ' ' || e.apellido AS instructor_nombre
  FROM public.rutina r
  LEFT JOIN public.empleado e ON e.id = r.instructor_id
  WHERE r.socio_id = socio_api.socio_sesion();

CREATE VIEW socio_api.mis_rutina_ejercicios WITH (security_barrier) AS
  SELECT re.rutina_id, re.ejercicio_id, re.dia, re.orden, re.series, re.repeticiones,
         re.peso_kg, re.descanso_seg
  FROM public.rutina_ejercicio re
  JOIN public.rutina r ON r.id = re.rutina_id
  WHERE r.socio_id = socio_api.socio_sesion();

-- ---------------------------------------------------------------------
-- Auditoría de operaciones de escritura (RF-49, §4.5)
-- ---------------------------------------------------------------------
CREATE TABLE agente.auditoria (
    id              BIGSERIAL PRIMARY KEY,
    fecha           TIMESTAMP NOT NULL DEFAULT now(),
    empleado_id     BIGINT NOT NULL REFERENCES public.empleado(id),
    operacion       VARCHAR(30) NOT NULL,   -- alta_socio | modificacion_socio | suspension | reactivacion | baja | fuera_de_catalogo
    socio_id        BIGINT REFERENCES public.socio(id),
    resultado       VARCHAR(20) NOT NULL
                    CHECK (resultado IN ('ejecutada','cancelada','descartada','rechazada','fallida')),
    motivo          TEXT,                   -- motivo de suspensión o baja (S-07)
    valores_antes   JSONB,
    valores_despues JSONB,
    detalle         TEXT,                   -- errores de validación o de ejecución
    trace_id        VARCHAR(64)             -- vínculo con la traza de LangSmith
);
