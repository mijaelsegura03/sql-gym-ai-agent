-- =====================================================================
--  roles.sql  -  Roles y permisos de la aplicación (spec técnico §4.3)
--
--  Las contraseñas NO están en este archivo: scripts/reset_db.py las fija antes
--  de ejecutarlo como parámetros de sesión (gym.pass_lector_admin,
--  gym.pass_lector_socio y gym.pass_escritor), tomados del .env.
--
--  Los roles son globales del servidor (se crean una vez y se les actualiza la
--  contraseña); los GRANT son de la base actual (gimnasio_template) y pasan a
--  la base de trabajo cuando se la clona con CREATE DATABASE ... TEMPLATE.
--
--  | Rol              | Permisos                                                    |
--  |------------------|-------------------------------------------------------------|
--  | gym_lector_admin | SELECT en public y en agente.auditoria; solo lectura        |
--  | gym_lector_socio | SELECT en las vistas de socio_api; sin acceso a public      |
--  | gym_escritor     | SELECT para validar; INSERT/UPDATE en socio; UPDATE(estado) |
--  |                  | en membresia; INSERT en agente.auditoria. Sin DELETE.       |
-- =====================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('gym_lector_admin', current_setting('gym.pass_lector_admin')),
      ('gym_lector_socio', current_setting('gym.pass_lector_socio')),
      ('gym_escritor',     current_setting('gym.pass_escritor'))) AS t(rol, pass)
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.rol) THEN
      EXECUTE format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', r.rol, r.pass);
    ELSE
      EXECUTE format('CREATE ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', r.rol, r.pass);
    END IF;
  END LOOP;
END $$;

-- Configuración por rol (se aplica en cada conexión nueva)
ALTER ROLE gym_lector_admin SET default_transaction_read_only = on;
ALTER ROLE gym_lector_admin SET statement_timeout = '5s';
ALTER ROLE gym_lector_admin SET search_path = public, ext;
ALTER ROLE gym_lector_socio SET default_transaction_read_only = on;
ALTER ROLE gym_lector_socio SET statement_timeout = '5s';
ALTER ROLE gym_lector_socio SET search_path = socio_api, ext;
ALTER ROLE gym_escritor     SET statement_timeout = '5s';

-- Nadie usa public por defecto: cada rol recibe solo lo que necesita
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA socio_api, agente FROM PUBLIC;
GRANT USAGE ON SCHEMA ext TO gym_lector_admin, gym_lector_socio, gym_escritor;

-- ---------------------------------------------------------------------
-- gym_lector_admin: lectura de todo (perfil Administrador y auth.py)
-- ---------------------------------------------------------------------
GRANT USAGE  ON SCHEMA public, agente        TO gym_lector_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public  TO gym_lector_admin;
GRANT SELECT ON agente.auditoria             TO gym_lector_admin;

-- ---------------------------------------------------------------------
-- gym_lector_socio: solo las vistas de socio_api (sin acceso a public)
-- ---------------------------------------------------------------------
GRANT USAGE   ON SCHEMA socio_api                     TO gym_lector_socio;
GRANT SELECT  ON ALL TABLES IN SCHEMA socio_api       TO gym_lector_socio;
GRANT EXECUTE ON FUNCTION socio_api.socio_sesion()    TO gym_lector_socio;

-- ---------------------------------------------------------------------
-- gym_escritor: solo app/ops/ejecucion.py, con sentencias fijas
-- ---------------------------------------------------------------------
GRANT USAGE  ON SCHEMA public, agente TO gym_escritor;
GRANT SELECT ON public.socio, public.membresia, public.plan, public.sede, public.empleado
             TO gym_escritor;
GRANT INSERT, UPDATE ON public.socio            TO gym_escritor;
GRANT UPDATE (estado) ON public.membresia       TO gym_escritor;
GRANT USAGE  ON SEQUENCE public.socio_id_seq    TO gym_escritor;
GRANT INSERT ON agente.auditoria                TO gym_escritor;
GRANT USAGE  ON SEQUENCE agente.auditoria_id_seq TO gym_escritor;
