-- Run with psql in periplus_lake as an administrator, after catalogue setup.
-- CNPG/platform creates the LOGIN role and supplies its password separately.
-- Intended for a fresh, dedicated reader without other memberships/grants.
\set ON_ERROR_STOP on
BEGIN;
ALTER ROLE periplus_lake_reader NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE periplus_lake_reader SET default_transaction_read_only = on;
ALTER ROLE periplus_lake_reader SET statement_timeout = '20s';
GRANT CONNECT ON DATABASE periplus_lake TO periplus_lake_reader;
GRANT USAGE ON SCHEMA ducklake TO periplus_lake_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ducklake TO periplus_lake_reader;
-- Defaults belong to the object creator, not the administrator running this file.
ALTER DEFAULT PRIVILEGES FOR ROLE periplus_lake IN SCHEMA ducklake
  GRANT SELECT ON TABLES TO periplus_lake_reader;
COMMIT;
