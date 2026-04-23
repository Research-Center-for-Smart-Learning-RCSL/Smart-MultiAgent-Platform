"""`smap.bootstrap neo4j-init` — database + constraints + indexes (B.8).

Neo4j **Community** supports exactly one user database plus `system`. If the
operator asked for a non-default `settings.neo4j.database`, we try
`CREATE DATABASE` and downgrade gracefully to `neo4j` when Community refuses;
the report surfaces a `skipped` entry so it is visible in CI output.

Constraints/indexes match §21.3:
  * `(:Entity {id})` unique
  * index on `:Entity(canonical_name)`
  * relationship index on `:REL(type)`

Per-project subgraph labels (`:P_{project_id}`) are created at runtime by the
GraphRAG builder; bootstrap only lays down the type-level schema.
"""

from __future__ import annotations

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError, DatabaseError

from app.config.settings import Settings

from ._common import BootstrapReport

_CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.id IS UNIQUE",
)

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX entity_canonical_name IF NOT EXISTS FOR (e:Entity) ON (e.canonical_name)",
    "CREATE INDEX rel_type IF NOT EXISTS FOR ()-[r:REL]-() ON (r.type)",
)


def run(settings: Settings) -> BootstrapReport:
    report = BootstrapReport(subcommand="neo4j-init")
    driver = GraphDatabase.driver(
        settings.neo4j.url, auth=(settings.neo4j.user, settings.neo4j.password)
    )
    target_db = settings.neo4j.database
    try:
        with driver.session(database="system") as sys_sess:
            existing = {
                row["name"]
                for row in sys_sess.run("SHOW DATABASES YIELD name RETURN name").data()
            }
            if target_db in existing:
                report.already(f"database:{target_db}")
            else:
                try:
                    sys_sess.run(f"CREATE DATABASE `{target_db}` IF NOT EXISTS").consume()
                    report.did(f"database:{target_db}")
                except (ClientError, DatabaseError) as exc:
                    # Community edition rejects additional databases.
                    report.skipped(
                        f"database:{target_db}",
                        f"falling back to `neo4j`: {exc.message}",
                    )
                    target_db = "neo4j"

        with driver.session(database=target_db) as db_sess:
            for stmt in _CONSTRAINTS:
                db_sess.run(stmt).consume()
                report.did(f"constraint:{stmt.split(' ', 3)[2]}")
            for stmt in _INDEXES:
                db_sess.run(stmt).consume()
                report.did(f"index:{stmt.split(' ', 3)[2]}")
    finally:
        driver.close()

    return report
