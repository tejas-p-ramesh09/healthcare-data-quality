"""Read model over restricted PostgreSQL views and keyset pages."""

import base64
import binascii
import json
from datetime import datetime

from psycopg import sql
from psycopg.rows import dict_row

from healthcare_dq.workflow.service import WorkflowError
from healthcare_dq.workflow.stages import NAMES


def one(conn, query, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, args).fetchone()


def summary(conn, wid):
    row = one(conn, """SELECT w.*, (SELECT jsonb_agg(to_jsonb(s) ORDER BY s.step)
        FROM workflow_stage_report s WHERE s.workflow_id=w.id) AS stages
        FROM workflow_report w WHERE id=%s""", (wid,))
    if row is None:
        raise WorkflowError("WORKFLOW_NOT_FOUND")
    stages = [{
        "name": NAMES[s["step"]], "status": s["status"], "batch_id": s["batch_id"],
        "run_id": s["quality_id"] or s["matching_id"], "attempt_count": s["attempt_count"],
        "updated_at": s["updated_at"], "counts": s["counts"],
        "failure": {"code": s["code"], "retryable": bool(s["retryable"])} if s["code"] else None,
    } for s in row["stages"]]
    state = next((s["status"] for s in stages if s["status"] != "completed"), "completed")
    return {**row["spec"], "id": wid, "created_at": row["created_at"], "status": state,
            "updated_at": max(datetime.fromisoformat(s["updated_at"]) for s in stages), "stages": stages}


def completed(conn, wid, step):
    status = summary(conn, wid)["stages"][step]["status"]
    if status != "completed":
        code = {"failed": "STAGE_FAILED", "interrupted": "STAGE_INTERRUPTED"}.get(status, "STAGE_NOT_READY")
        raise WorkflowError(code)


def decode(cursor, scope, kinds):
    if cursor is None:
        return None
    try:
        if len(cursor) > 4096:
            raise ValueError
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(data, list) or len(data) != 2 or data[0] != scope
                or not isinstance(data[1], list) or len(data[1]) != len(kinds)):
            raise ValueError
        for val, kind in zip(data[1], kinds, strict=True):
            if type(val) is not (str if kind is datetime else kind):
                raise ValueError
            if kind is int and not 0 <= val <= 9223372036854775807:
                raise ValueError
            if kind is str and (len(val) > 1024 or "\x00" in val or
                                any(0xD800 <= ord(c) <= 0xDFFF for c in val)):
                raise ValueError
        values = [datetime.fromisoformat(v) if k is datetime else v for v, k in zip(data[1], kinds, strict=True)]
        if any(k is datetime and v.tzinfo is None for v, k in zip(values, kinds, strict=True)):
            raise ValueError
        return values
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise WorkflowError("INVALID_CURSOR") from None


def page(conn, table, keys, kinds, filters, limit, cursor):
    # Identifiers are route constants, and all external values are bound parameters.
    scope = [table, *[list(item) for item in sorted(filters.items())]]
    values = decode(cursor, scope, kinds)
    cols = sql.SQL(",").join(map(sql.Identifier, keys))
    where = [sql.SQL("{}=%s").format(sql.Identifier(k)) for k in filters]
    params = list(filters.values())
    if values is not None:
        where.append(sql.SQL("({}) > ({})").format(cols, sql.SQL(",").join(sql.Placeholder() for _ in keys)))
        params.extend(values)
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL("")
    query = sql.SQL("SELECT * FROM {}{} ORDER BY {} LIMIT %s").format(sql.Identifier(table), clause, cols)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query, (*params, limit + 1)).fetchall()
    token = None
    if len(rows) > limit:
        rows.pop()
        data = json.dumps([scope, [rows[-1][k] for k in keys]], default=str).encode()
        token = base64.urlsafe_b64encode(data).decode()
    return {"items": rows, "next_cursor": token}
