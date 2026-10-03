-- =====================================================================
--  gimnasio_datos.sql  -  Datos sintéticos para gimnasio_schema.sql
--  Motor: PostgreSQL 13+
--
--  Uso:
--     psql -d <tu_base> -f gimnasio_schema.sql
--     psql -d <tu_base> -f gimnasio_datos.sql
--
--  Qué hace:
--   * Catálogos chicos cargados a mano (sedes, salas, actividades, planes,
--     empleados, clases, ejercicios).
--   * Tablas grandes generadas programáticamente (socios, aptos, membresías,
--     pagos, sesiones, reservas, accesos, rutinas) con reglas de negocio
--     coherentes entre sí:
--       - un socio activo tiene una membresía vigente; los "morosos" tienen
--         la membresía vencida hace pocos días (y accesos rechazados por
--         "cuota vencida"); los de baja dejaron de renovar hace más de 30 días.
--       - los accesos solo se permiten con membresía vigente y apto médico.
--       - solo reservan clases socios cuyo plan habilita esa actividad, en su
--         sede (o con plan multisede), respetando clases_por_semana.
--       - hay listas de espera en las clases más concurridas.
--   * FECHAS FIJAS (spec funcional DC-07, spec técnico §4.6): todas las fechas
--     se calculan respecto de una fecha base fija, el 16/10/2026, guardada en
--     el parámetro de sesión gym.fecha_base (ver set_config más abajo). El
--     "ahora" de la carga es la fecha base a las 12:00. No se usa la fecha del
--     sistema en ningún lado, así que los datos no dependen del día de carga:
--     membresías vigentes al 16/10, clases de las 6 semanas anteriores y de la
--     semana siguiente (hasta el 23/10) y accesos de los 60 días anteriores.
--   * setseed() fija la semilla: con la semilla y la fecha base fijas, cada
--     ejecución genera exactamente los mismos datos.
--   * Casos que necesitan los criterios de aceptación del agente (D-01):
--     algunos morosos tienen una renovación con demora pendiente de pago
--     (membresía 'pendiente' sin ninguna activa), ver sección 6.
--
--  ATENCIÓN: el TRUNCATE de abajo borra TODO el contenido de las tablas
--  (y reinicia los ids) para que el script sea re-ejecutable.
-- =====================================================================

-- Fecha base de los datos (DT-01). Para moverla, cambiar solo este valor.
SELECT set_config('gym.fecha_base', '2026-10-16', false);

BEGIN;

SELECT setseed(0.42);

TRUNCATE TABLE rutina_ejercicio, rutina, ejercicio, acceso, reserva,
               sesion_clase, clase, pago, membresia, plan_actividad, plan,
               actividad, apto_medico, empleado, socio, sala, sede
RESTART IDENTITY CASCADE;

-- =====================================================================
-- 1) SEDES Y SALAS  (3 sedes, 11 salas)
-- =====================================================================
INSERT INTO sede (nombre, direccion, telefono, activa) VALUES
('Sede Centro', 'Av. Belgrano 1250',    '+54 341 4210001', TRUE),   -- id 1
('Sede Norte',  'Av. San Martín 3400',  '+54 341 4210002', TRUE),   -- id 2
('Sede Sur',    'Calle Mitre 780',      '+54 341 4210003', TRUE);   -- id 3

INSERT INTO sala (sede_id, nombre, capacidad) VALUES
(1, 'Sala Spinning',      16),   -- 1
(1, 'Salón Multiuso',     30),   -- 2
(1, 'Sala Funcional',     20),   -- 3
(2, 'Sala Spinning',      14),   -- 4
(2, 'Salón Multiuso',     25),   -- 5
(2, 'Sala Funcional',     18),   -- 6
(3, 'Salón Multiuso',     20),   -- 7
(3, 'Sala Funcional',     15),   -- 8
(1, 'Sala de Musculación', 60),  -- 9
(2, 'Sala de Musculación', 45),  -- 10
(3, 'Sala de Musculación', 30);  -- 11

-- =====================================================================
-- 2) ACTIVIDADES, PLANES Y PLAN_ACTIVIDAD
-- =====================================================================
INSERT INTO actividad (nombre, descripcion) VALUES
('Spinning',   'Ciclismo indoor grupal con música, de intensidad media a alta.'),            -- 1
('Yoga',       'Posturas, respiración y relajación para flexibilidad y control mental.'),    -- 2
('Funcional',  'Circuitos de ejercicios funcionales con el propio peso y elementos.'),       -- 3
('Pilates',    'Fortalecimiento del core, postura y control del movimiento.'),               -- 4
('Zumba',      'Baile fitness con ritmos latinos, de alta energía.'),                        -- 5
('Crossfit',   'Entrenamiento de alta intensidad con levantamientos y gimnasia.'),           -- 6
('Stretching', 'Elongación y movilidad articular, ideal como complemento.'),                 -- 7
('Boxeo',      'Boxeo recreativo sin contacto: técnica, bolsas y cardio.'),                  -- 8
('GAP',        'Trabajo localizado de glúteos, abdomen y piernas.'),                         -- 9
('HIIT',       'Intervalos de alta intensidad, 40 minutos de trabajo metabólico.');          -- 10

INSERT INTO plan (nombre, precio, duracion_dias, clases_por_semana, incluye_musculacion, todas_las_sedes, activo) VALUES
('Musculación Mensual',        28000,  30, NULL, TRUE,  FALSE, TRUE),   -- 1
('Musculación Trimestral',     72000,  90, NULL, TRUE,  FALSE, TRUE),   -- 2
('Full Mensual',               38000,  30, NULL, TRUE,  FALSE, TRUE),   -- 3
('Full Trimestral',           102000,  90, NULL, TRUE,  TRUE,  TRUE),   -- 4
('Full Anual',                360000, 365, NULL, TRUE,  TRUE,  TRUE),   -- 5
('Solo Clases 2x semana',      26000,  30, 2,    FALSE, FALSE, TRUE),   -- 6
('Clases 3x semana + Sala',    32000,  30, 3,    TRUE,  FALSE, TRUE),   -- 7
('Pase Libre Multisede',       46000,  30, NULL, TRUE,  TRUE,  TRUE),   -- 8
('Plan Estudiante',            22000,  30, 2,    TRUE,  FALSE, TRUE),   -- 9
('Promo Verano',               20000,  30, NULL, TRUE,  FALSE, FALSE);  -- 10 (discontinuado)

-- Planes "full": todas las actividades
INSERT INTO plan_actividad (plan_id, actividad_id)
SELECT p.id, a.id FROM plan p CROSS JOIN actividad a WHERE p.id IN (3, 4, 5, 8);

-- Planes con actividades acotadas (los planes 1 y 2 son solo musculación)
INSERT INTO plan_actividad (plan_id, actividad_id) VALUES
(6, 1), (6, 2), (6, 3), (6, 4), (6, 5), (6, 7),
(7, 1), (7, 2), (7, 3), (7, 4), (7, 5), (7, 7), (7, 9),
(9, 1), (9, 2), (9, 3), (9, 5), (9, 7),
(10, 1), (10, 3), (10, 6);

-- =====================================================================
-- 3) EMPLEADOS (20)   ids 1-10 instructores, 11-14 recepción,
--                     15-17 administración, 18-20 gerentes
-- =====================================================================
INSERT INTO empleado (dni, nombre, apellido, email, rol, sede_id, activo) VALUES
('28345671', 'Martín',    'Sosa',       'martin.sosa@gimnasio.example',       'instructor',     1, TRUE),
('31220458', 'Lucía',     'Fernández',  'lucia.fernandez@gimnasio.example',   'instructor',     1, TRUE),
('30987123', 'Diego',     'Romero',     'diego.romero@gimnasio.example',      'instructor',     1, TRUE),
('33445890', 'Camila',    'Acosta',     'camila.acosta@gimnasio.example',     'instructor',     2, TRUE),
('29876543', 'Nicolás',   'Benítez',    'nicolas.benitez@gimnasio.example',   'instructor',     2, TRUE),
('34120987', 'Julieta',   'Molina',     'julieta.molina@gimnasio.example',    'instructor',     2, TRUE),
('27654321', 'Federico',  'Giménez',    'federico.gimenez@gimnasio.example',  'instructor',     3, TRUE),
('35210876', 'Paula',     'Herrera',    'paula.herrera@gimnasio.example',     'instructor',     3, TRUE),
('32765410', 'Sebastián', 'Ríos',       'sebastian.rios@gimnasio.example',    'instructor',     1, TRUE),
('36098712', 'Valentina', 'Castro',     'valentina.castro@gimnasio.example',  'instructor',     3, TRUE),
('37451230', 'Carolina',  'Peralta',    'carolina.peralta@gimnasio.example',  'recepcion',      1, TRUE),
('38120456', 'Gonzalo',   'Vega',       'gonzalo.vega@gimnasio.example',      'recepcion',      2, TRUE),
('39654120', 'Agustina',  'Ledesma',    'agustina.ledesma@gimnasio.example',  'recepcion',      3, TRUE),
('36789012', 'Matías',    'Ortiz',      'matias.ortiz@gimnasio.example',      'recepcion',      1, FALSE),
('30123987', 'Romina',    'Aguirre',    'romina.aguirre@gimnasio.example',    'administracion', NULL, TRUE),
('29345810', 'Hernán',    'Cabrera',    'hernan.cabrera@gimnasio.example',    'administracion', NULL, TRUE),
('33890214', 'Daniela',   'Pereyra',    'daniela.pereyra@gimnasio.example',   'administracion', 1, TRUE),
('25678901', 'Ricardo',   'Domínguez',  'ricardo.dominguez@gimnasio.example', 'gerente',        1, TRUE),
('26789012', 'Marcela',   'Suárez',     'marcela.suarez@gimnasio.example',    'gerente',        2, TRUE),
('27890123', 'Leandro',   'Núñez',      'leandro.nunez@gimnasio.example',     'gerente',        3, TRUE);

-- =====================================================================
-- 4) SOCIOS (120)
--    - ~10% de baja (id % 10 = 0), ~4% suspendidos (id % 25 = 7)
--    - los de baja y los "morosos" (id % 9 = 4) tienen alta de hace
--      más de 160 días para que tengan historial de membresías.
-- =====================================================================
WITH listas AS (
  SELECT
    ARRAY['Lucía','Mateo','Sofía','Santiago','Valentina','Benjamín','Martina','Juan','Camila','Tomás',
          'Julieta','Lautaro','Agustina','Facundo','Florencia','Joaquín','Micaela','Ignacio','Carolina','Franco',
          'Antonella','Bruno','Rocío','Thiago','Daniela','Gonzalo','Paula','Emiliano','Luciana','Nicolás',
          'Belén','Maximiliano','Candela','Leandro','Natalia','Ezequiel','Victoria','Damián','Milagros','Pablo'] AS nombres,
    ARRAY['González','Rodríguez','Gómez','Fernández','López','Díaz','Martínez','Pérez','García','Sánchez',
          'Romero','Sosa','Álvarez','Torres','Ruiz','Ramírez','Flores','Benítez','Acosta','Medina',
          'Herrera','Suárez','Aguirre','Giménez','Gutiérrez','Pereyra','Rojas','Molina','Castro','Ortiz',
          'Silva','Núñez','Luna','Juárez','Cabrera','Ríos','Morales','Domínguez','Vega','Peralta'] AS apellidos,
    ARRAY['Madre','Padre','Pareja','Hermano','Hermana','Amigo','Amiga'] AS parentescos
),
base AS (
  SELECT
    i,
    random() AS r_sede,
    random() AS r_alta,
    random() AS r_nac,
    l.nombres[1 + (i * 7) % cardinality(l.nombres)]                      AS nombre,
    l.apellidos[1 + (i * 11 + i / 40) % cardinality(l.apellidos)]        AS apellido,
    l.nombres[1 + (i * 13 + 5) % cardinality(l.nombres)]                 AS nom_emerg,
    l.apellidos[1 + (i * 17 + 3) % cardinality(l.apellidos)]             AS ape_emerg,
    l.parentescos[1 + i % cardinality(l.parentescos)]                    AS parentesco
  FROM generate_series(1, 120) AS i
  CROSS JOIN listas l
)
INSERT INTO socio (dni, nombre, apellido, email, telefono, fecha_nacimiento,
                   contacto_emergencia, sede_principal_id, fecha_alta, estado)
SELECT
  ((i * 1299709) % 28000000 + 18000000)::text,
  nombre,
  apellido,
  CASE WHEN i % 15 = 0 THEN NULL
       ELSE translate(lower(nombre), 'áéíóúüñ', 'aeiouun') || '.' ||
            translate(lower(apellido), 'áéíóúüñ', 'aeiouun') || i || '@example.com' END,
  '+54 9 341 ' || (4000000 + (i * 48271) % 5000000)::text,
  current_setting('gym.fecha_base')::date - (16 * 365 + floor(r_nac * 46 * 365)::int),
  CASE WHEN i % 19 = 0 THEN NULL
       ELSE nom_emerg || ' ' || ape_emerg || ' (' || parentesco || ') - +54 9 341 ' ||
            (5000000 + (i * 91) % 4000000)::text END,
  CASE WHEN r_sede < 0.50 THEN 1 WHEN r_sede < 0.80 THEN 2 ELSE 3 END,
  current_setting('gym.fecha_base')::date - (CASE WHEN i % 10 = 0 OR i % 9 = 4
                       THEN 160 + floor(r_alta * 260)
                       ELSE floor(r_alta * 420) END)::int,
  CASE WHEN i % 10 = 0 THEN 'baja'
       WHEN i % 25 = 7 THEN 'suspendido'
       ELSE 'activo' END
FROM base
ORDER BY i;

-- =====================================================================
-- 5) APTOS MÉDICOS
--    - 1er apto cerca del alta (salvo ~4% de socios que no presentó)
--    - renovación anual para ~85% de los que no están de baja
-- =====================================================================
WITH a1 AS (
  SELECT s.id AS socio_id,
         s.fecha_alta - floor(random() * 13)::int AS emision,
         random() AS r_med,
         random() AS r_obs
  FROM socio s
  WHERE s.id % 25 <> 3
)
INSERT INTO apto_medico (socio_id, fecha_emision, fecha_vencimiento, medico, archivo_url, observaciones)
SELECT socio_id, emision, emision + 365,
       (ARRAY['Dr. Alejandro Funes','Dra. Mariana Costa','Dr. Roberto Salinas',
              'Dra. Silvia Paredes','Dr. Gustavo Lescano','Dra. Verónica Arias'])[1 + floor(r_med * 6)::int],
       'https://archivos.gimnasio.example/aptos/' || socio_id || '_1.pdf',
       CASE WHEN r_obs < 0.70 THEN NULL
            WHEN r_obs < 0.85 THEN 'Apto sin restricciones'
            WHEN r_obs < 0.93 THEN 'Apto con control cardiológico anual'
            ELSE 'Evitar ejercicios de alto impacto' END
FROM a1;

WITH r AS (
  SELECT a.socio_id,
         a.fecha_vencimiento - ((random() * 20)::int - 5) AS emision,
         random() AS r_ren,
         random() AS r_med,
         random() AS r_obs
  FROM apto_medico a
  JOIN socio s ON s.id = a.socio_id
  WHERE s.estado <> 'baja'
)
INSERT INTO apto_medico (socio_id, fecha_emision, fecha_vencimiento, medico, archivo_url, observaciones)
SELECT socio_id, emision, emision + 365,
       (ARRAY['Dr. Alejandro Funes','Dra. Mariana Costa','Dr. Roberto Salinas',
              'Dra. Silvia Paredes','Dr. Gustavo Lescano','Dra. Verónica Arias'])[1 + floor(r_med * 6)::int],
       'https://archivos.gimnasio.example/aptos/' || socio_id || '_2.pdf',
       CASE WHEN r_obs < 0.75 THEN NULL
            WHEN r_obs < 0.90 THEN 'Apto sin restricciones'
            ELSE 'Apto con control cardiológico anual' END
FROM r
WHERE r_ren < 0.85 AND emision <= current_setting('gym.fecha_base')::date;

-- =====================================================================
-- 6) MEMBRESÍAS Y PAGOS
--    Para cada socio se genera la cadena de membresías desde su alta hasta hoy.
--    Perfiles: activo / moroso / congelado / suspendido / baja
-- =====================================================================
CREATE FUNCTION pg_temp.nuevo_pago(p_membresia BIGINT, p_monto NUMERIC, p_fecha TIMESTAMP,
                                   p_metodo TEXT, p_estado TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
  v_ref TEXT;
  v_reg BIGINT;
BEGIN
  v_ref := CASE p_metodo
             WHEN 'mercadopago'   THEN 'MP-'  || lpad(floor(random() * 1e10)::bigint::text, 10, '0')
             WHEN 'transferencia' THEN 'TRF-' || lpad(floor(random() * 1e8)::bigint::text, 8, '0')
             WHEN 'efectivo'      THEN 'REC-' || lpad(floor(random() * 1e6)::bigint::text, 6, '0')
             ELSE                      'POS-' || lpad(floor(random() * 1e8)::bigint::text, 8, '0')
           END;
  -- MercadoPago es online (sin empleado); el resto lo registra recepción/administración
  v_reg := CASE WHEN p_metodo = 'mercadopago' THEN NULL
                ELSE (ARRAY[11, 12, 13, 17])[1 + floor(random() * 4)::int] END;
  INSERT INTO pago (membresia_id, monto, fecha_pago, metodo, estado, referencia_ext, registrado_por)
  VALUES (p_membresia, p_monto, p_fecha, p_metodo, p_estado, v_ref, v_reg);
END $$;

DO $$
DECLARE
  v_s          RECORD;
  v_plan       RECORD;
  v_perfil     TEXT;
  v_planes     INT[];
  v_plan_id    INT;
  v_hoy        DATE := current_setting('gym.fecha_base')::date;
  v_inicio     DATE;
  v_fin        DATE;
  v_ultimo_fin DATE;
  v_precio     NUMERIC(12,2);
  v_estado     TEXT;
  v_mid        BIGINT;
  v_r          DOUBLE PRECISION;
  v_metodo     TEXT;
  v_ts         TIMESTAMP;
  v_m1         NUMERIC(12,2);
BEGIN
  FOR v_s IN SELECT id, fecha_alta, estado FROM socio ORDER BY id LOOP

    v_perfil := CASE
                  WHEN v_s.estado = 'baja'       THEN 'baja'
                  WHEN v_s.estado = 'suspendido' THEN 'suspendido'
                  WHEN v_s.id % 9  = 4           THEN 'moroso'
                  WHEN v_s.id % 17 = 3           THEN 'congelado'
                  ELSE 'activo'
                END;

    -- Planes posibles según perfil (morosos/bajas: planes cortos)
    v_planes := CASE v_perfil
                  WHEN 'moroso' THEN ARRAY[1,3,6,7,8,9]
                  WHEN 'baja'   THEN ARRAY[1,1,2,3,3,4,6,7,9]
                  ELSE               ARRAY[1,1,1,2,3,3,3,4,5,6,7,7,8,9]
                END;
    v_plan_id := v_planes[1 + floor(random() * array_length(v_planes, 1))::int];

    -- Quienes se asociaron hace 300-400 días entraron con la promo de verano (ya discontinuada)
    IF v_s.fecha_alta BETWEEN v_hoy - 400 AND v_hoy - 300 AND random() < 0.5 THEN
      v_plan_id := 10;
    END IF;

    v_inicio := v_s.fecha_alta;
    v_ultimo_fin := NULL;

    LOOP
      EXIT WHEN v_inicio > v_hoy;

      SELECT * INTO v_plan FROM plan WHERE id = v_plan_id;
      v_fin := v_inicio + v_plan.duracion_dias - 1;

      -- Cortes de la cadena para bajas y morosos
      EXIT WHEN v_perfil = 'baja'   AND v_fin >= v_hoy - 30;
      EXIT WHEN v_perfil = 'moroso' AND v_fin >= v_hoy - 2 - (v_s.id % 20)::int;

      -- Precio pactado: el precio de lista "de esa época" (inflación ~2,5% mensual) y 10% con descuento
      v_precio := round(v_plan.precio * power(0.975, (v_hoy - v_inicio) / 30.0) / 100) * 100;
      IF random() < 0.10 THEN
        v_precio := round(v_precio * 0.9 / 100) * 100;
      END IF;

      IF v_fin < v_hoy THEN
        v_estado := 'vencida';
      ELSE
        v_estado := CASE v_perfil WHEN 'suspendido' THEN 'cancelada'
                                  WHEN 'congelado'  THEN 'congelada'
                                  ELSE 'activa' END;
      END IF;

      INSERT INTO membresia (socio_id, plan_id, fecha_inicio, fecha_fin, precio_pactado, estado)
      VALUES (v_s.id, v_plan_id, v_inicio, v_fin, v_precio, v_estado)
      RETURNING id INTO v_mid;

      -- ----- Pagos de la membresía -----
      v_r := random();
      v_metodo := CASE WHEN v_r < 0.20 THEN 'efectivo'
                       WHEN v_r < 0.40 THEN 'debito'
                       WHEN v_r < 0.55 THEN 'credito'
                       WHEN v_r < 0.75 THEN 'transferencia'
                       ELSE 'mercadopago' END;
      v_ts := LEAST((v_inicio - (random() * 2)::int)::timestamp
                      + make_interval(hours => 9 + (random() * 11)::int, mins => (random() * 59)::int),
                    (current_setting('gym.fecha_base')::date + TIME '12:00') - interval '5 minutes');

      -- ~6% de los pagos electrónicos tuvo un intento rechazado antes
      IF v_metodo IN ('credito', 'debito', 'mercadopago') AND random() < 0.06 THEN
        PERFORM pg_temp.nuevo_pago(v_mid, v_precio, v_ts - interval '15 minutes', v_metodo, 'rechazado');
      END IF;

      IF v_estado <> 'cancelada' AND v_inicio < v_hoy - 20 AND random() < 0.10 THEN
        -- Pago en 2 cuotas (50% al inicio, 50% unos días después)
        v_m1 := round(v_precio / 2, 2);
        PERFORM pg_temp.nuevo_pago(v_mid, v_m1, v_ts, v_metodo, 'aprobado');
        PERFORM pg_temp.nuevo_pago(v_mid, v_precio - v_m1,
                                   v_ts + make_interval(days => 10 + (random() * 5)::int),
                                   v_metodo, 'aprobado');
      ELSE
        PERFORM pg_temp.nuevo_pago(v_mid, v_precio, v_ts, v_metodo,
                                   CASE WHEN v_estado = 'cancelada' AND random() < 0.30
                                        THEN 'reintegrado' ELSE 'aprobado' END);
      END IF;

      v_ultimo_fin := v_fin;

      -- Siguiente membresía: a veces con un hueco de unos días y a veces con cambio de plan
      v_inicio := v_fin + 1;
      IF v_fin < v_hoy - 45 AND random() < 0.08 THEN
        v_inicio := v_inicio + 3 + (random() * 17)::int;
      END IF;
      -- La promo de verano dura una sola membresía: después pasan a un plan regular
      IF v_plan_id = 10 OR random() < 0.15 THEN
        v_plan_id := v_planes[1 + floor(random() * array_length(v_planes, 1))::int];
      END IF;
    END LOOP;

    -- Renovación anticipada pendiente de pago para algunos socios activos que están por vencer
    IF v_perfil = 'activo' AND v_ultimo_fin IS NOT NULL
       AND v_ultimo_fin BETWEEN v_hoy AND v_hoy + 10 AND random() < 0.40 THEN
      IF NOT v_plan.activo THEN
        SELECT * INTO v_plan FROM plan WHERE id = 3;
      END IF;
      INSERT INTO membresia (socio_id, plan_id, fecha_inicio, fecha_fin, precio_pactado, estado)
      VALUES (v_s.id, v_plan.id, v_ultimo_fin + 1, v_ultimo_fin + v_plan.duracion_dias,
              v_plan.precio, 'pendiente')
      RETURNING id INTO v_mid;
      PERFORM pg_temp.nuevo_pago(v_mid, v_plan.precio, (current_setting('gym.fecha_base')::date + TIME '12:00'), 'transferencia', 'pendiente');
    END IF;

    -- Renovación con demora pendiente de pago para algunos morosos (id % 4 = 1):
    -- contrataron ayer por transferencia y el pago todavía no se acreditó, así que su
    -- única membresía en curso está 'pendiente' (caso CA-58 del spec funcional).
    IF v_perfil = 'moroso' AND v_s.id % 4 = 1 AND v_ultimo_fin IS NOT NULL THEN
      SELECT * INTO v_plan FROM plan WHERE id = 3;
      INSERT INTO membresia (socio_id, plan_id, fecha_inicio, fecha_fin, precio_pactado, estado)
      VALUES (v_s.id, v_plan.id, v_hoy - 1, v_hoy - 1 + v_plan.duracion_dias - 1,
              v_plan.precio, 'pendiente')
      RETURNING id INTO v_mid;
      PERFORM pg_temp.nuevo_pago(v_mid, v_plan.precio, (v_hoy - 1) + TIME '18:30', 'transferencia', 'pendiente');
    END IF;

  END LOOP;
END $$;

DROP FUNCTION pg_temp.nuevo_pago(BIGINT, NUMERIC, TIMESTAMP, TEXT, TEXT);

-- =====================================================================
-- 7) CLASES (grilla semanal, 34)  dia_semana: 1=lunes ... 7=domingo
--    ids 1-13 Sede Centro, 14-24 Sede Norte, 25-33 Sede Sur, 34 nueva (Centro)
-- =====================================================================
INSERT INTO clase (actividad_id, sala_id, instructor_id, dia_semana, hora_inicio, duracion_min, cupo, vigente_desde, vigente_hasta) VALUES
-- Sede Centro
(1, 1, 1, 1, '19:00', 45, 12, current_setting('gym.fecha_base')::date - 365, NULL),   --  1 Spinning lun
(1, 1, 1, 3, '19:00', 45, 12, current_setting('gym.fecha_base')::date - 365, NULL),   --  2 Spinning mié
(1, 1, 1, 5, '08:00', 45, 12, current_setting('gym.fecha_base')::date - 365, NULL),   --  3 Spinning vie
(2, 2, 2, 2, '18:30', 60, 25, current_setting('gym.fecha_base')::date - 365, NULL),   --  4 Yoga mar
(2, 2, 2, 4, '18:30', 60, 25, current_setting('gym.fecha_base')::date - 365, NULL),   --  5 Yoga jue
(4, 2, 2, 1, '10:00', 55, 15, current_setting('gym.fecha_base')::date - 365, NULL),   --  6 Pilates lun
(3, 3, 3, 1, '20:00', 50, 18, current_setting('gym.fecha_base')::date - 365, NULL),   --  7 Funcional lun
(3, 3, 3, 3, '20:00', 50, 18, current_setting('gym.fecha_base')::date - 365, NULL),   --  8 Funcional mié
(6, 3, 3, 2, '07:30', 60, 15, current_setting('gym.fecha_base')::date - 365, NULL),   --  9 Crossfit mar
(5, 2, 9, 3, '19:30', 60, 28, current_setting('gym.fecha_base')::date - 365, NULL),   -- 10 Zumba mié
(5, 2, 9, 6, '10:30', 60, 28, current_setting('gym.fecha_base')::date - 365, NULL),   -- 11 Zumba sáb
(9, 3, 9, 5, '19:00', 45, 18, current_setting('gym.fecha_base')::date - 365, NULL),   -- 12 GAP vie
(8, 3, 3, 4, '20:00', 60, 15, current_setting('gym.fecha_base')::date - 300, current_setting('gym.fecha_base')::date - 35),  -- 13 Boxeo jue (discontinuada)
-- Sede Norte
(1, 4, 4, 2, '19:00', 45, 8, current_setting('gym.fecha_base')::date - 365, NULL),    -- 14 Spinning mar
(1, 4, 4, 4, '19:00', 45, 8, current_setting('gym.fecha_base')::date - 365, NULL),    -- 15 Spinning jue
(1, 4, 4, 6, '09:30', 45, 8, current_setting('gym.fecha_base')::date - 365, NULL),    -- 16 Spinning sáb
(5, 5, 4, 1, '19:30', 60, 22, current_setting('gym.fecha_base')::date - 365, NULL),   -- 17 Zumba lun
(10, 6, 5, 1, '07:00', 40, 16, current_setting('gym.fecha_base')::date - 365, NULL),  -- 18 HIIT lun
(10, 6, 5, 3, '07:00', 40, 16, current_setting('gym.fecha_base')::date - 365, NULL),  -- 19 HIIT mié
(3, 6, 5, 2, '20:00', 50, 16, current_setting('gym.fecha_base')::date - 365, NULL),   -- 20 Funcional mar
(8, 6, 5, 5, '19:30', 60, 14, current_setting('gym.fecha_base')::date - 365, NULL),   -- 21 Boxeo vie
(2, 5, 6, 3, '18:30', 60, 22, current_setting('gym.fecha_base')::date - 365, NULL),   -- 22 Yoga mié
(4, 5, 6, 5, '10:00', 55, 15, current_setting('gym.fecha_base')::date - 365, NULL),   -- 23 Pilates vie
(7, 5, 6, 2, '12:30', 40, 20, current_setting('gym.fecha_base')::date - 365, NULL),   -- 24 Stretching mar
-- Sede Sur
(3, 8, 7, 1, '19:00', 50, 15, current_setting('gym.fecha_base')::date - 365, NULL),   -- 25 Funcional lun
(3, 8, 7, 4, '19:00', 50, 15, current_setting('gym.fecha_base')::date - 365, NULL),   -- 26 Funcional jue
(6, 8, 7, 6, '10:00', 60, 12, current_setting('gym.fecha_base')::date - 365, NULL),   -- 27 Crossfit sáb
(2, 7, 8, 2, '19:00', 60, 18, current_setting('gym.fecha_base')::date - 365, NULL),   -- 28 Yoga mar
(5, 7, 8, 4, '20:00', 60, 20, current_setting('gym.fecha_base')::date - 365, NULL),   -- 29 Zumba jue
(9, 7, 8, 3, '19:00', 45, 18, current_setting('gym.fecha_base')::date - 365, NULL),   -- 30 GAP mié
(4, 7, 10, 1, '18:00', 55, 15, current_setting('gym.fecha_base')::date - 365, NULL),  -- 31 Pilates lun
(7, 7, 10, 5, '18:30', 40, 18, current_setting('gym.fecha_base')::date - 365, NULL),  -- 32 Stretching vie
(4, 7, 10, 6, '09:00', 55, 15, current_setting('gym.fecha_base')::date - 365, NULL),  -- 33 Pilates sáb
-- Clase nueva (arrancó hace 4 semanas)
(10, 3, 1, 6, '09:00', 40, 16, current_setting('gym.fecha_base')::date - 28, NULL);   -- 34 HIIT sáb

-- =====================================================================
-- 8) SESIONES DE CLASE: últimas 6 semanas + próxima semana
--    ~4% canceladas, ~6% con instructor reemplazante (de la misma sede)
-- =====================================================================
WITH cand AS (
  SELECT c.id AS clase_id, c.instructor_id, c.hora_inicio, c.duracion_min, f.fecha,
         random() AS r_rep, random() AS r_can
  FROM clase c
  CROSS JOIN LATERAL (SELECT current_setting('gym.fecha_base')::date - 42 + k AS fecha FROM generate_series(0, 49) AS k) f
  WHERE EXTRACT(ISODOW FROM f.fecha) = c.dia_semana
    AND f.fecha >= c.vigente_desde
    AND f.fecha <= COALESCE(c.vigente_hasta, current_setting('gym.fecha_base')::date + 7)
)
INSERT INTO sesion_clase (clase_id, fecha, instructor_id, estado)
SELECT cand.clase_id, cand.fecha,
       CASE WHEN r_rep < 0.06 THEN
         (SELECT e2.id FROM empleado e2
          WHERE e2.rol = 'instructor' AND e2.activo
            AND e2.sede_id = (SELECT e1.sede_id FROM empleado e1 WHERE e1.id = cand.instructor_id)
            AND e2.id <> cand.instructor_id
          ORDER BY random() LIMIT 1)
       END,
       CASE WHEN r_can < 0.04 THEN 'cancelada'
            WHEN cand.fecha < current_setting('gym.fecha_base')::date THEN 'realizada'
            WHEN cand.fecha = current_setting('gym.fecha_base')::date
                 AND (cand.fecha + cand.hora_inicio + cand.duracion_min * interval '1 minute') < (current_setting('gym.fecha_base')::date + TIME '12:00')
                 THEN 'realizada'
            ELSE 'programada' END
FROM cand
ORDER BY cand.fecha, cand.clase_id;

-- =====================================================================
-- 9) RESERVAS
--    Elegibles: socios con membresía vigente ese día, cuyo plan habilita la
--    actividad y que entrenan en esa sede (o tienen plan multisede).
--    Se respeta clases_por_semana (planes ilimitados: 2 a 5 por semana).
--    Las clases de la tarde/noche, Spinning y Zumba son más pedidas;
--    si se supera el cupo, el excedente (hasta 4) queda en lista de espera.
-- =====================================================================
WITH elegibles AS (
  SELECT se.id AS sesion_id, se.fecha, se.estado AS est_sesion, c.hora_inicio, c.cupo,
         so.id AS socio_id,
         CASE WHEN p.clases_por_semana IS NOT NULL THEN p.clases_por_semana
              ELSE 2 + (so.id % 4)::int END AS cuota,
         (CASE WHEN c.hora_inicio >= TIME '18:00' THEN 4.0 ELSE 1.0 END) *
         (CASE WHEN a.nombre IN ('Spinning', 'Zumba') THEN 3.0 ELSE 1.0 END) AS peso
  FROM sesion_clase se
  JOIN clase c            ON c.id = se.clase_id
  JOIN actividad a        ON a.id = c.actividad_id
  JOIN sala sa            ON sa.id = c.sala_id
  JOIN membresia m        ON se.fecha BETWEEN m.fecha_inicio AND m.fecha_fin
                         AND m.estado IN ('activa', 'vencida')
  JOIN plan p             ON p.id = m.plan_id
  JOIN plan_actividad pa  ON pa.plan_id = p.id AND pa.actividad_id = c.actividad_id
  JOIN socio so           ON so.id = m.socio_id
  WHERE se.estado <> 'cancelada'
    AND (p.todas_las_sedes OR so.sede_principal_id = sa.sede_id)
    AND (so.id % 4 <> 3 OR p.clases_por_semana IS NOT NULL)   -- ~25% de los socios no va a clases
),
semanal AS (
  SELECT e.*,
         ROW_NUMBER() OVER (PARTITION BY e.socio_id, date_trunc('week', e.fecha::timestamp)
                            ORDER BY random() / e.peso) AS rn_sem
  FROM elegibles e
),
por_sesion AS (
  SELECT s.*,
         ROW_NUMBER() OVER (PARTITION BY s.sesion_id ORDER BY random()) AS rn_ses
  FROM semanal s
  WHERE s.rn_sem <= s.cuota
),
resultado AS (
  SELECT ps.*, random() AS r_can, random() AS r_asis, random() AS r_cre
  FROM por_sesion ps
  WHERE ps.rn_ses <= ps.cupo + 4
)
INSERT INTO reserva (sesion_id, socio_id, creada_en, estado, asistio)
SELECT sesion_id, socio_id,
       LEAST(fecha + hora_inicio - (1 + r_cre * 71) * interval '1 hour',
             (current_setting('gym.fecha_base')::date + TIME '12:00') - interval '10 minutes'),
       CASE WHEN rn_ses > cupo THEN 'lista_espera'
            WHEN r_can < 0.08  THEN 'cancelada'
            ELSE 'confirmada' END,
       CASE WHEN rn_ses > cupo OR r_can < 0.08 THEN NULL
            WHEN est_sesion = 'realizada'      THEN (r_asis < 0.82)
            ELSE NULL END
FROM resultado
ORDER BY fecha, sesion_id, rn_ses;

-- =====================================================================
-- 10) ACCESOS (últimos 60 días)
--     Frecuencia distinta por socio. Se permite el ingreso solo con membresía
--     vigente (activa/vencida ese día) y apto médico válido. También hay
--     intentos rechazados: cuota vencida (hasta 14 días después), membresía
--     congelada/cancelada y sin apto médico.
-- =====================================================================
WITH dias AS (
  SELECT s.id AS socio_id, s.sede_principal_id, (s.id % 7)::int AS k, d.dia,
         random() AS r_vis, random() AS r_hora, random() AS r_min, random() AS r_dur,
         random() AS r_met, random() AS r_sede, random() AS r_sede2, random() AS r_out
  FROM socio s
  CROSS JOIN LATERAL (SELECT current_setting('gym.fecha_base')::date - 59 + n AS dia FROM generate_series(0, 59) AS n) d
  WHERE d.dia >= s.fecha_alta
),
eval AS (
  SELECT x.*,
         cov.estado AS est_cubre,
         cov.todas_las_sedes AS multisede,
         (SELECT max(m.fecha_fin) FROM membresia m
           WHERE m.socio_id = x.socio_id AND m.estado <> 'pendiente' AND m.fecha_fin < x.dia) AS ult_fin,
         EXISTS (SELECT 1 FROM apto_medico a
                  WHERE a.socio_id = x.socio_id
                    AND x.dia BETWEEN a.fecha_emision AND a.fecha_vencimiento) AS apto_ok,
         (0.15 + x.k * 0.07) AS freq
  FROM dias x
  LEFT JOIN LATERAL (
    SELECT m.estado, p.todas_las_sedes
    FROM membresia m JOIN plan p ON p.id = m.plan_id
    WHERE m.socio_id = x.socio_id
      AND x.dia BETWEEN m.fecha_inicio AND m.fecha_fin
      AND m.estado <> 'pendiente'
    ORDER BY m.fecha_inicio DESC
    LIMIT 1
  ) cov ON TRUE
),
intentos AS (
  SELECT v.socio_id, v.sede_principal_id, v.multisede, v.r_sede, v.r_sede2, v.r_met, v.r_dur, v.r_out,
         v.dia + make_interval(
                   hours => (ARRAY[6,7,7,8,8,9,10,12,13,17,18,18,19,19,20,21])[1 + floor(v.r_hora * 16)::int],
                   mins  => floor(v.r_min * 60)::int) AS ts_ingreso,
         COALESCE(v.est_cubre IN ('activa', 'vencida'), FALSE) AND v.apto_ok AS permitido,
         CASE WHEN v.est_cubre IS NULL        THEN 'cuota vencida'
              WHEN v.est_cubre = 'congelada'  THEN 'membresía congelada'
              WHEN v.est_cubre = 'cancelada'  THEN 'membresía cancelada'
              WHEN NOT v.apto_ok              THEN 'sin apto médico'
         END AS motivo
  FROM eval v
  WHERE (v.est_cubre IN ('activa', 'vencida')      AND v.r_vis < v.freq)
     OR (v.est_cubre IN ('congelada', 'cancelada') AND v.r_vis < v.freq * 0.2)
     OR (v.est_cubre IS NULL AND v.ult_fin IS NOT NULL AND v.dia - v.ult_fin <= 14 AND v.r_vis < v.freq * 0.6)
)
INSERT INTO acceso (socio_id, sede_id, ingreso, egreso, metodo, permitido, motivo_rechazo)
SELECT socio_id,
       CASE WHEN multisede AND r_sede < 0.25 THEN 1 + floor(r_sede2 * 3)::int
            ELSE sede_principal_id END,
       ts_ingreso,
       CASE WHEN permitido AND r_out >= 0.03
                 AND ts_ingreso + (45 + floor(r_dur * 76)::int) * interval '1 minute' <= (current_setting('gym.fecha_base')::date + TIME '12:00')
            THEN ts_ingreso + (45 + floor(r_dur * 76)::int) * interval '1 minute' END,
       CASE WHEN r_met < 0.60 THEN 'qr'
            WHEN r_met < 0.80 THEN 'huella'
            WHEN r_met < 0.95 THEN 'tarjeta'
            ELSE 'manual' END,
       permitido,
       motivo
FROM intentos
WHERE ts_ingreso <= (current_setting('gym.fecha_base')::date + TIME '12:00')
ORDER BY ts_ingreso, socio_id;

-- =====================================================================
-- 11) EJERCICIOS (39)
-- =====================================================================
INSERT INTO ejercicio (nombre, grupo_muscular, descripcion) VALUES
('Press de banca plano',          'Pecho',    'Press con barra sobre banco plano; pectoral mayor, tríceps y hombro anterior.'),
('Press inclinado con mancuernas','Pecho',    'Press en banco inclinado a 30 grados; enfatiza la parte superior del pecho.'),
('Aperturas con mancuernas',      'Pecho',    'Apertura en banco plano para estirar y contraer el pectoral.'),
('Cruce de poleas',               'Pecho',    'Cruce de poleas altas para trabajar la contracción del pectoral.'),
('Press de pecho en máquina',     'Pecho',    'Press guiado en máquina, ideal para principiantes.'),
('Fondos en paralelas',           'Pecho',    'Descenso y empuje con el propio peso; pecho inferior y tríceps.'),
('Dominadas',                     'Espalda',  'Tracción vertical con agarre prono; dorsal ancho y bíceps.'),
('Jalón al pecho',                'Espalda',  'Tracción en polea alta hacia el pecho.'),
('Remo con barra',                'Espalda',  'Remo inclinado con barra; dorsales y romboides.'),
('Remo en polea baja',            'Espalda',  'Remo sentado con agarre neutro.'),
('Remo con mancuerna',            'Espalda',  'Remo unilateral apoyado en banco.'),
('Pull-over en polea',            'Espalda',  'Extensión de hombro con brazos rectos en polea alta; dorsal.'),
('Press militar con barra',       'Hombros',  'Press vertical de pie o sentado con barra.'),
('Elevaciones laterales',         'Hombros',  'Elevación lateral con mancuernas; deltoides medio.'),
('Elevaciones frontales',         'Hombros',  'Elevación frontal con mancuernas o disco; deltoides anterior.'),
('Pájaros con mancuernas',        'Hombros',  'Apertura inclinada hacia atrás; deltoides posterior.'),
('Press Arnold',                  'Hombros',  'Press con rotación de muñecas; trabajo completo del deltoides.'),
('Curl de bíceps con barra',      'Brazos',   'Flexión de codo con barra recta o Z.'),
('Curl martillo',                 'Brazos',   'Curl con agarre neutro; braquial y antebrazo.'),
('Curl en banco Scott',           'Brazos',   'Curl aislado con apoyo en banco predicador.'),
('Extensión de tríceps en polea', 'Brazos',   'Extensión de codo en polea alta con soga o barra.'),
('Press francés',                 'Brazos',   'Extensión de tríceps acostado con barra Z.'),
('Patada de tríceps',             'Brazos',   'Extensión de codo con mancuerna e inclinación de tronco.'),
('Sentadilla libre',              'Piernas',  'Sentadilla con barra; cuádriceps, glúteos y core.'),
('Prensa 45 grados',              'Piernas',  'Empuje de piernas en máquina inclinada.'),
('Extensión de cuádriceps',       'Piernas',  'Extensión de rodilla en máquina; aislamiento de cuádriceps.'),
('Curl femoral acostado',         'Piernas',  'Flexión de rodilla en máquina; isquiotibiales.'),
('Estocadas con mancuernas',      'Piernas',  'Zancadas alternadas caminando o en el lugar.'),
('Peso muerto rumano',            'Piernas',  'Bisagra de cadera con barra; isquiotibiales y glúteos.'),
('Elevación de talones',          'Piernas',  'Elevación de gemelos de pie o sentado.'),
('Hip thrust',                    'Glúteos',  'Empuje de cadera con espalda apoyada en banco.'),
('Patada de glúteo en polea',     'Glúteos',  'Extensión de cadera en polea baja con tobillera.'),
('Sentadilla sumo',               'Glúteos',  'Sentadilla con apertura amplia de piernas; aductores y glúteos.'),
('Abducción en máquina',          'Glúteos',  'Apertura de piernas sentado; glúteo medio.'),
('Plancha frontal',               'Core',     'Isométrico sobre antebrazos y puntas de pie.'),
('Abdominales en máquina',        'Core',     'Flexión de tronco con carga en máquina.'),
('Elevación de piernas colgado',  'Core',     'Elevación de piernas colgado de barra; abdomen inferior.'),
('Russian twist',                 'Core',     'Rotación de tronco sentado con disco o balón.'),
('Plancha lateral',               'Core',     'Isométrico lateral sobre un antebrazo; oblicuos.');

-- =====================================================================
-- 12) RUTINAS Y SUS EJERCICIOS
--     ~2/3 de los socios con membresía vigente que incluye musculación
--     tienen una rutina activa; ~35% además tiene una anterior ya finalizada.
-- =====================================================================
WITH base AS (
  SELECT so.id AS socio_id, so.sede_principal_id, so.fecha_alta,
         random() AS r_ini, random() AS r_obj, random() AS r_ins
  FROM socio so
  JOIN membresia m ON m.socio_id = so.id
                  AND current_setting('gym.fecha_base')::date BETWEEN m.fecha_inicio AND m.fecha_fin
                  AND m.estado = 'activa'
  JOIN plan p      ON p.id = m.plan_id AND p.incluye_musculacion
  WHERE so.id % 3 <> 0
)
INSERT INTO rutina (socio_id, instructor_id, nombre, objetivo, fecha_inicio, fecha_fin, activa)
SELECT b.socio_id,
       CASE WHEN b.r_ins < 0.10 THEN NULL
            ELSE (SELECT e.id FROM empleado e
                  WHERE e.rol = 'instructor' AND e.sede_id = b.sede_principal_id
                  ORDER BY random() LIMIT 1) END,
       'Rutina ' || (2 + b.socio_id % 2) || ' días - ' ||
         (ARRAY['Hipertrofia','Pérdida de peso','Tonificación','Resistencia','Fuerza','Rehabilitación','Salud general'])[1 + floor(b.r_obj * 7)::int],
       (ARRAY['Hipertrofia','Pérdida de peso','Tonificación','Resistencia','Fuerza','Rehabilitación','Salud general'])[1 + floor(b.r_obj * 7)::int],
       GREATEST(b.fecha_alta, current_setting('gym.fecha_base')::date - (5 + floor(b.r_ini * 85))::int),
       NULL,
       TRUE
FROM base b
ORDER BY b.socio_id;

-- Rutinas anteriores (ya finalizadas), siempre previas a la rutina activa
WITH prev AS (
  SELECT r.socio_id, r.instructor_id, r.fecha_inicio AS ini_activa, s.fecha_alta,
         random() AS r_prev, random() AS r_obj, random() AS r_dur
  FROM rutina r JOIN socio s ON s.id = r.socio_id
  WHERE r.activa
)
INSERT INTO rutina (socio_id, instructor_id, nombre, objetivo, fecha_inicio, fecha_fin, activa)
SELECT p.socio_id, p.instructor_id,
       'Rutina ' || (2 + p.socio_id % 2) || ' días - ' ||
         (ARRAY['Hipertrofia','Pérdida de peso','Tonificación','Resistencia','Fuerza','Rehabilitación','Salud general'])[1 + floor(p.r_obj * 7)::int],
       (ARRAY['Hipertrofia','Pérdida de peso','Tonificación','Resistencia','Fuerza','Rehabilitación','Salud general'])[1 + floor(p.r_obj * 7)::int],
       GREATEST(p.fecha_alta, p.ini_activa - 60 - floor(p.r_dur * 60)::int),
       p.ini_activa - 1,
       FALSE
FROM prev p
WHERE p.r_prev < 0.35 AND p.fecha_alta <= p.ini_activa - 45
ORDER BY p.socio_id;

-- Ejercicios por rutina: día 1 = pecho/hombros, día 2 = espalda/brazos, día 3 = piernas/glúteos/core
WITH cand AS (
  SELECT r.id AS rutina_id, d.dia, e.grupo_muscular, e.id AS ejercicio_id,
         ROW_NUMBER() OVER (PARTITION BY r.id, d.dia
                            ORDER BY (e.grupo_muscular = 'Core'), random()) AS orden,
         random() AS r_s, random() AS r_rep, random() AS r_p, random() AS r_d
  FROM rutina r
  CROSS JOIN LATERAL generate_series(1, 2 + (r.socio_id % 2)::int) AS d(dia)
  JOIN ejercicio e
    ON e.grupo_muscular = ANY (CASE d.dia
                                 WHEN 1 THEN ARRAY['Pecho', 'Hombros']
                                 WHEN 2 THEN ARRAY['Espalda', 'Brazos']
                                 ELSE        ARRAY['Piernas', 'Glúteos', 'Core']
                               END)
)
INSERT INTO rutina_ejercicio (rutina_id, ejercicio_id, dia, orden, series, repeticiones, peso_kg, descanso_seg)
SELECT rutina_id, ejercicio_id, dia, orden,
       CASE WHEN grupo_muscular = 'Core' OR r_s < 0.6 THEN 3 ELSE 4 END,
       CASE WHEN grupo_muscular = 'Core'
            THEN (ARRAY['30s','45s','15-20'])[1 + floor(r_rep * 3)::int]
            ELSE (ARRAY['12','10','8-10','12-15','10-12','8','15'])[1 + floor(r_rep * 7)::int] END,
       CASE WHEN grupo_muscular = 'Core' THEN NULL
            ELSE round((10 + r_p * 70) / 2.5) * 2.5 END,
       CASE WHEN grupo_muscular = 'Core' THEN 30
            ELSE (ARRAY[45,60,60,75,90,90,120])[1 + floor(r_d * 7)::int] END
FROM cand
WHERE orden <= 4
ORDER BY rutina_id, dia, orden;

COMMIT;

ANALYZE;

-- =====================================================================
-- Resumen de filas cargadas
-- =====================================================================
SELECT 'sede' AS tabla, count(*) AS filas FROM sede
UNION ALL SELECT 'sala',             count(*) FROM sala
UNION ALL SELECT 'actividad',        count(*) FROM actividad
UNION ALL SELECT 'plan',             count(*) FROM plan
UNION ALL SELECT 'plan_actividad',   count(*) FROM plan_actividad
UNION ALL SELECT 'empleado',         count(*) FROM empleado
UNION ALL SELECT 'socio',            count(*) FROM socio
UNION ALL SELECT 'apto_medico',      count(*) FROM apto_medico
UNION ALL SELECT 'membresia',        count(*) FROM membresia
UNION ALL SELECT 'pago',             count(*) FROM pago
UNION ALL SELECT 'clase',            count(*) FROM clase
UNION ALL SELECT 'sesion_clase',     count(*) FROM sesion_clase
UNION ALL SELECT 'reserva',          count(*) FROM reserva
UNION ALL SELECT 'acceso',           count(*) FROM acceso
UNION ALL SELECT 'ejercicio',        count(*) FROM ejercicio
UNION ALL SELECT 'rutina',           count(*) FROM rutina
UNION ALL SELECT 'rutina_ejercicio', count(*) FROM rutina_ejercicio;
