"""Local credentials and server-owned input catalog."""

import os
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg

from healthcare_dq.db import ROOT, connect
from healthcare_dq.workflow.catalog import load_catalog

REPORT_VIEWS = [
    "workflow_report", "workflow_stage_report", "workflow_batch_report", "workflow_file_report",
    "workflow_finding_report", "workflow_recommendation_report", "workflow_schema_ready",
]


def verify_connection(conn, role, injected):
    # Factories in integration tests use SET ROLE on a disposable administrator
    # connection. Configured DSNs must also authenticate as the runtime role.
    member_roles = ["ingest"] if role == "workflow_executor" else []
    valid, = conn.execute("""
        SELECT current_user=%s AND (%s OR session_user=current_user)
          AND rolcanlogin AND NOT (rolsuper OR rolcreaterole OR rolcreatedb
                                  OR rolreplication OR rolbypassrls)
          AND NOT pg_has_role(d.datdba,'USAGE')
          AND NOT has_database_privilege(d.oid,'CREATE')
          AND NOT EXISTS (SELECT FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_temp_%%'
                          AND has_schema_privilege(n.oid,'CREATE'))
          AND NOT EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                          WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
                          AND has_function_privilege(p.oid,'EXECUTE'))
          AND ARRAY(SELECT m.rolname::text FROM pg_roles m
                    WHERE m.rolname<>current_user AND pg_has_role(m.oid,'MEMBER')
                    ORDER BY m.rolname)= %s::text[]
        FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
        WHERE r.rolname=current_user
    """, (role, injected, member_roles)).fetchone()
    if not valid:
        raise ValueError("Invalid database privileges")

    if role == "workflow_reporter":
        reads, unexpected = conn.execute("""
            SELECT count(*) FILTER(WHERE n.nspname='public' AND c.relname=ANY(%s)
                                   AND c.relkind='v' AND has_table_privilege(c.oid,'SELECT')),
                   count(*) FILTER(WHERE CASE WHEN c.relkind='S'
                        THEN has_sequence_privilege(c.oid,'SELECT,USAGE,UPDATE')
                        ELSE has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                          OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')
                          OR ((n.nspname<>'public' OR NOT c.relname=ANY(%s) OR c.relkind<>'v')
                              AND has_any_column_privilege(c.oid,'SELECT')) END)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema')
              AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp_%%'
              AND c.relkind IN ('r','p','v','m','f','S')
        """, (REPORT_VIEWS, REPORT_VIEWS)).fetchone()
        if reads != len(REPORT_VIEWS) or unexpected:
            raise ValueError("Invalid database privileges")
    else:
        ready, = conn.execute("""
            SELECT pg_has_role('ingest','USAGE') AND has_table_privilege('raw_resource','SELECT')
              AND has_column_privilege('workflow_stage','status','UPDATE')
              AND has_column_privilege('workflow_stage_attempt','status','UPDATE')
              AND NOT has_table_privilege('workflow_stage','DELETE,TRUNCATE,TRIGGER')
              AND NOT has_table_privilege('raw_resource','UPDATE,DELETE,TRUNCATE,TRIGGER')
        """).fetchone()
        if not ready:
            raise ValueError("Invalid database privileges")


@contextmanager
def runtime_connection(factory, env, role):
    from healthcare_dq.workflow.service import WorkflowError

    try:
        conn = factory() if factory else connect(env)
        try:
            verify_connection(conn, role, factory is not None)
        except (ValueError, psycopg.Error):
            conn.close()
            raise
    except (ValueError, psycopg.Error):
        raise WorkflowError("SERVICE_NOT_READY") from None
    with conn:
        yield conn


@dataclass(frozen=True)
class Settings:
    token: str
    root: object = ROOT
    reader: object = None
    executor: object = None

    def __post_init__(self):
        if (len(self.token) < 32 or not self.token.isascii()
                or any(ch.isspace() for ch in self.token)):
            raise ValueError("Set a strong local WORKFLOW_TOKEN")

    def report_connection(self):
        return runtime_connection(self.reader, "WORKFLOW_REPORT_DATABASE_URL", "workflow_reporter")

    def execute_connection(self):
        return runtime_connection(self.executor, "WORKFLOW_EXEC_DATABASE_URL", "workflow_executor")

    def catalog(self):
        return load_catalog(self.root)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("WORKFLOW_TOKEN", ""))
