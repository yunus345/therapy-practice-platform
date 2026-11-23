#!/bin/sh
# Creates the restricted application role and the test database.
# Docker runs this once, on an empty data volume. CI runs it directly.
set -eu

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v app_role="$APP_DB_USER" \
  -v app_password="$APP_DB_PASSWORD" \
  -v main_db="$POSTGRES_DB" \
  -v test_db="$POSTGRES_TEST_DB" <<'SQL'
CREATE ROLE :"app_role" LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE DATABASE :"test_db";

REVOKE ALL ON DATABASE :"main_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"test_db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"main_db" TO :"app_role";
GRANT CONNECT ON DATABASE :"test_db" TO :"app_role";
SQL
