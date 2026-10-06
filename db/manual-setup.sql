-- Manual (non-Docker) database setup for FinSense AI.
--
-- Run as a PostgreSQL superuser, supplying your own passwords:
--
--   psql -U postgres -v owner_password='CHOOSE-A-PASSWORD' \
--        -v app_password='CHOOSE-ANOTHER-PASSWORD' -f db/manual-setup.sql
--
-- Requires the pgvector extension to be installed on the server
-- (e.g. `apt install postgresql-16-pgvector`, or see
-- https://github.com/pgvector/pgvector#installation for Windows/macOS).

\set ON_ERROR_STOP on

CREATE ROLE finsense_owner LOGIN CREATEDB PASSWORD :'owner_password';
CREATE ROLE finsense_app LOGIN PASSWORD :'app_password';
CREATE DATABASE finsense OWNER finsense_owner;

-- Lets the owner create a throwaway database for the pytest suite.
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;

\connect finsense
CREATE EXTENSION IF NOT EXISTS vector;
GRANT CONNECT ON DATABASE finsense TO finsense_app;
GRANT USAGE ON SCHEMA public TO finsense_app;
ALTER SCHEMA public OWNER TO finsense_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE finsense_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO finsense_app;
ALTER DEFAULT PRIVILEGES FOR ROLE finsense_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO finsense_app;
