#!/bin/sh
# Runs once, when the Postgres data volume is first initialised by the
# official postgres / pgvector image. It:
#   1. enables pgvector (needs superuser, so it cannot live in a migration)
#   2. creates a least-privilege runtime role used by the API process.
#
# Schema changes are applied by Alembic using the owner role
# ($POSTGRES_USER). The API itself connects as `finsense_app`, which can
# read and write rows but cannot create, alter or drop tables.
# Only -e (not -u): the postgres entrypoint may source this file instead of executing it.
set -e

if [ -z "${APP_DB_PASSWORD:-}" ]; then
  echo "APP_DB_PASSWORD is not set; refusing to create the runtime role without a password." >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v app_password="$APP_DB_PASSWORD" \
  -v owner="$POSTGRES_USER" \
  -v db="$POSTGRES_DB" <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;

-- template1 also gets the extension so a throwaway test database created
-- by the owner role (e.g. for pytest) can use vector columns.
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;
\connect :"db"

CREATE ROLE finsense_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"db" TO finsense_app;
GRANT USAGE ON SCHEMA public TO finsense_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO finsense_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO finsense_app;
EOSQL
