-- Application database role `mars_app`: what the running API may do. Idempotent.
--
-- Run by the postgres image on the first start (mounted into docker-entrypoint-initdb.d) and by
-- update.sh / restore.sh through `psql -f -` on an existing volume, after every migration and
-- after a restore (a restored dump does not carry these grants: backups use --no-acl).
-- Connect as the owner `mars` to the database `mars`. The password comes from the environment
-- of the db container (APP_DB_PASSWORD, from .env) via \getenv, so it is on no command line.
--
-- The role may SELECT/INSERT/UPDATE/DELETE on tables and use sequences. It cannot create
-- objects, truncate, change the schema, create roles or databases. A runtime check of app/
-- found no DDL, TRUNCATE, VACUUM/ANALYZE or REFRESH MATERIALIZED VIEW (advisory locks need no
-- privilege).

\set ON_ERROR_STOP on
\getenv app_password APP_DB_PASSWORD

-- Create the role once; refresh its attributes and password every time.
SELECT format('CREATE ROLE mars_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mars_app')
\gexec

SELECT format(
  'ALTER ROLE mars_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'app_password')
\gexec

-- Connect only through this role; nobody else gets in by PUBLIC.
REVOKE ALL ON DATABASE mars FROM PUBLIC;
GRANT CONNECT ON DATABASE mars TO mars_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO mars_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mars_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mars_app;

-- Objects created later by migrations (run as `mars`) are usable by the app at once.
ALTER DEFAULT PRIVILEGES FOR ROLE mars IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mars_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mars IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO mars_app;

-- Alembic's bookkeeping is not the app's business.
SELECT 'REVOKE ALL ON TABLE public.alembic_version FROM mars_app'
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec
