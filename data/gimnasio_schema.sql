CREATE TABLE sede (
    id          BIGSERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    direccion   VARCHAR(200) NOT NULL,
    telefono    VARCHAR(30),
    activa      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE sala (
    id          BIGSERIAL PRIMARY KEY,
    sede_id     BIGINT NOT NULL REFERENCES sede(id),
    nombre      VARCHAR(100) NOT NULL,
    capacidad   INT NOT NULL CHECK (capacidad > 0),
    UNIQUE (sede_id, nombre)
);

CREATE TABLE socio (
    id                     BIGSERIAL PRIMARY KEY,
    dni                    VARCHAR(15) NOT NULL UNIQUE,
    nombre                 VARCHAR(80) NOT NULL,
    apellido               VARCHAR(80) NOT NULL,
    email                  VARCHAR(120) UNIQUE,
    telefono               VARCHAR(30),
    fecha_nacimiento       DATE,
    contacto_emergencia    VARCHAR(150),
    sede_principal_id      BIGINT REFERENCES sede(id),
    fecha_alta             DATE NOT NULL DEFAULT CURRENT_DATE,
    estado                 VARCHAR(20) NOT NULL DEFAULT 'activo'
                           CHECK (estado IN ('activo','suspendido','baja'))
);

CREATE TABLE empleado (
    id          BIGSERIAL PRIMARY KEY,
    dni         VARCHAR(15) NOT NULL UNIQUE,
    nombre      VARCHAR(80) NOT NULL,
    apellido    VARCHAR(80) NOT NULL,
    email       VARCHAR(120) UNIQUE,
    rol         VARCHAR(20) NOT NULL
                CHECK (rol IN ('instructor','recepcion','administracion','gerente')),
    sede_id     BIGINT REFERENCES sede(id),
    activo      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE apto_medico (
    id                  BIGSERIAL PRIMARY KEY,
    socio_id            BIGINT NOT NULL REFERENCES socio(id),
    fecha_emision       DATE NOT NULL,
    fecha_vencimiento   DATE NOT NULL,
    medico              VARCHAR(120),
    archivo_url         VARCHAR(300),
    observaciones       TEXT,
    CHECK (fecha_vencimiento > fecha_emision)
);

CREATE TABLE actividad (
    id          BIGSERIAL PRIMARY KEY,
    nombre      VARCHAR(80) NOT NULL UNIQUE,  
    descripcion TEXT
);

CREATE TABLE plan (
    id                   BIGSERIAL PRIMARY KEY,
    nombre               VARCHAR(80) NOT NULL,
    precio               NUMERIC(12,2) NOT NULL CHECK (precio >= 0),
    duracion_dias        INT NOT NULL CHECK (duracion_dias > 0),
    clases_por_semana    INT,             
    incluye_musculacion  BOOLEAN NOT NULL DEFAULT TRUE,
    todas_las_sedes      BOOLEAN NOT NULL DEFAULT FALSE,
    activo               BOOLEAN NOT NULL DEFAULT TRUE
);

-- Qué actividades habilita cada plan (N:M)
CREATE TABLE plan_actividad (
    plan_id      BIGINT NOT NULL REFERENCES plan(id),
    actividad_id BIGINT NOT NULL REFERENCES actividad(id),
    PRIMARY KEY (plan_id, actividad_id)
);

CREATE TABLE membresia (
    id              BIGSERIAL PRIMARY KEY,
    socio_id        BIGINT NOT NULL REFERENCES socio(id),
    plan_id         BIGINT NOT NULL REFERENCES plan(id),
    fecha_inicio    DATE NOT NULL,
    fecha_fin       DATE NOT NULL,
    precio_pactado  NUMERIC(12,2) NOT NULL, 
    estado          VARCHAR(20) NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','activa','vencida','cancelada','congelada')),
    CHECK (fecha_fin >= fecha_inicio)
);

CREATE TABLE pago (
    id              BIGSERIAL PRIMARY KEY,
    membresia_id    BIGINT NOT NULL REFERENCES membresia(id),
    monto           NUMERIC(12,2) NOT NULL CHECK (monto > 0),
    fecha_pago      TIMESTAMP NOT NULL DEFAULT NOW(),
    metodo          VARCHAR(20) NOT NULL
                    CHECK (metodo IN ('efectivo','debito','credito','transferencia','mercadopago')),
    estado          VARCHAR(20) NOT NULL DEFAULT 'aprobado'
                    CHECK (estado IN ('pendiente','aprobado','rechazado','reintegrado')),
    referencia_ext  VARCHAR(100),   
    registrado_por  BIGINT REFERENCES empleado(id)
);


CREATE TABLE clase (
    id              BIGSERIAL PRIMARY KEY,
    actividad_id    BIGINT NOT NULL REFERENCES actividad(id),
    sala_id         BIGINT NOT NULL REFERENCES sala(id),
    instructor_id   BIGINT NOT NULL REFERENCES empleado(id),
    dia_semana      SMALLINT NOT NULL CHECK (dia_semana BETWEEN 1 AND 7),
    hora_inicio     TIME NOT NULL,
    duracion_min    INT NOT NULL CHECK (duracion_min > 0),
    cupo            INT NOT NULL CHECK (cupo > 0),
    vigente_desde   DATE NOT NULL DEFAULT CURRENT_DATE,
    vigente_hasta   DATE
);

CREATE TABLE sesion_clase (
    id                  BIGSERIAL PRIMARY KEY,
    clase_id            BIGINT NOT NULL REFERENCES clase(id),
    fecha               DATE NOT NULL,
    instructor_id       BIGINT REFERENCES empleado(id),  -- reemplazo, si hay
    estado              VARCHAR(20) NOT NULL DEFAULT 'programada'
                        CHECK (estado IN ('programada','cancelada','realizada')),
    UNIQUE (clase_id, fecha)
);

CREATE TABLE reserva (
    id              BIGSERIAL PRIMARY KEY,
    sesion_id       BIGINT NOT NULL REFERENCES sesion_clase(id),
    socio_id        BIGINT NOT NULL REFERENCES socio(id),
    creada_en       TIMESTAMP NOT NULL DEFAULT NOW(),
    estado          VARCHAR(20) NOT NULL DEFAULT 'confirmada'
                    CHECK (estado IN ('confirmada','lista_espera','cancelada')),
    asistio         BOOLEAN,
    UNIQUE (sesion_id, socio_id)
);

CREATE TABLE acceso (
    id          BIGSERIAL PRIMARY KEY,
    socio_id    BIGINT NOT NULL REFERENCES socio(id),
    sede_id     BIGINT NOT NULL REFERENCES sede(id),
    ingreso     TIMESTAMP NOT NULL DEFAULT NOW(),
    egreso      TIMESTAMP,
    metodo      VARCHAR(20) NOT NULL DEFAULT 'qr'
                CHECK (metodo IN ('qr','tarjeta','huella','manual')),
    permitido   BOOLEAN NOT NULL,
    motivo_rechazo VARCHAR(100)       
);


CREATE TABLE ejercicio (
    id              BIGSERIAL PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL UNIQUE,
    grupo_muscular  VARCHAR(50),
    descripcion     TEXT
);

CREATE TABLE rutina (
    id              BIGSERIAL PRIMARY KEY,
    socio_id        BIGINT NOT NULL REFERENCES socio(id),
    instructor_id   BIGINT REFERENCES empleado(id),
    nombre          VARCHAR(100) NOT NULL,
    objetivo        VARCHAR(100),
    fecha_inicio    DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_fin       DATE,
    activa          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE rutina_ejercicio (
    rutina_id       BIGINT NOT NULL REFERENCES rutina(id) ON DELETE CASCADE,
    ejercicio_id    BIGINT NOT NULL REFERENCES ejercicio(id),
    dia             SMALLINT NOT NULL DEFAULT 1,  
    orden           SMALLINT NOT NULL,
    series          SMALLINT NOT NULL,
    repeticiones    VARCHAR(20) NOT NULL,       
    peso_kg         NUMERIC(6,2),
    descanso_seg    INT,
    PRIMARY KEY (rutina_id, dia, orden)
);


CREATE INDEX idx_membresia_socio_vig ON membresia (socio_id, fecha_fin);
CREATE INDEX idx_pago_membresia      ON pago (membresia_id);
CREATE INDEX idx_sesion_fecha        ON sesion_clase (fecha);
CREATE INDEX idx_reserva_socio       ON reserva (socio_id);
CREATE INDEX idx_acceso_socio_fecha  ON acceso (socio_id, ingreso);
CREATE INDEX idx_apto_socio_venc     ON apto_medico (socio_id, fecha_vencimiento);
