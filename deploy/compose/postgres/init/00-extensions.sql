-- Phase A seeds only; Phase B's Alembic baseline runs the authoritative DDL.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- pg_cron requires shared_preload_libraries=pg_cron, set only in the prod
-- overlay (docker-compose.prod.yml). Wrap so a missing preload skips cleanly
-- in dev/test instead of aborting the whole init script and exiting non-zero.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_cron;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'skipping pg_cron: %', SQLERRM;
END
$$;
