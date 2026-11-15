"""Apply checksummed migrations and provision local-only workflow role passwords."""

import os

from psycopg import sql

from healthcare_dq.db import connect, migrate


def main():
    roles = (
        ("ingest", "INGEST_PASSWORD"),
        ("workflow_executor", "WORKFLOW_EXEC_PASSWORD"),
        ("workflow_reporter", "WORKFLOW_REPORT_PASSWORD"),
    )
    values = [(role, os.environ.get(key, "")) for role, key in roles]
    if any(len(value) < 24 for _, value in values) or len({value for _, value in values}) != len(roles):
        raise SystemExit("Set distinct local role passwords of at least 24 characters.")
    with connect("MIGRATION_DATABASE_URL") as conn:
        migrate(conn)
        with conn.transaction():
            for role, value in values:
                conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(value)))
    print("Workflow schema and local roles ready.")


if __name__ == "__main__":
    main()
