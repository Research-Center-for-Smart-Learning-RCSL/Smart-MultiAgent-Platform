"""`smap.bootstrap neo4j-init` — database + constraints + indexes (B.8).

Neo4j **Community** supports exactly one user database plus `system`. If the
operator asked for a non-default `settings.neo4j.database`, we try
`CREATE DATABASE` and downgrade gracefully to `neo4j` when Community refuses;
the report surfaces a `skipped` entry so it is visible in CI output.

The schema must match the properties the GraphRAG driver actually writes
(``contexts.knowledge.infrastructure.neo4j_driver``): an ``:Entity`` is
MERGEd on the composite key ``(graphrag_config_id, name)`` and a ``:REL`` on
``(graphrag_config_id, relation)``. An earlier schema indexed ``id`` /
``canonical_name`` / ``:REL(type)`` — properties that are never set, so the
constraint was inert and the indexes dead, leaving every apply/snapshot/
traverse/delete to do a full label scan (audit C5). The composite range
indexes below back the real MERGE/MATCH keys.

Community edition cannot enforce a composite node-key uniqueness constraint
(Enterprise-only). Build serialization (the per-config Redis build lock)
keeps concurrent MERGE on the same key from racing, so a range index — which
both editions support — is sufficient and correct here.
"""

from __future__ import annotations

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError, DatabaseError

from app.config.settings import Settings

from ._common import BootstrapReport

_CONSTRAINTS: tuple[str, ...] = ()

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX entity_config_name IF NOT EXISTS " "FOR (e:Entity) ON (e.graphrag_config_id, e.name)",
    "CREATE INDEX entity_config_build IF NOT EXISTS " "FOR (e:Entity) ON (e.graphrag_config_id, e.build_id)",
    "CREATE INDEX rel_config_relation IF NOT EXISTS "
    "FOR ()-[r:REL]-() ON (r.graphrag_config_id, r.relation)",
    "CREATE INDEX rel_config_build IF NOT EXISTS " "FOR ()-[r:REL]-() ON (r.graphrag_config_id, r.build_id)",
)


def run(settings: Settings) -> BootstrapReport:
    report = BootstrapReport(subcommand="neo4j-init")
    driver = GraphDatabase.driver(settings.neo4j.url, auth=(settings.neo4j.user, settings.neo4j.password))
    target_db = settings.neo4j.database
    try:
        with driver.session(database="system") as sys_sess:
            existing = {row["name"] for row in sys_sess.run("SHOW DATABASES YIELD name RETURN name").data()}
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
