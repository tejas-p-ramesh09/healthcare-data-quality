"""Local REST interface for the durable workflow."""

import logging
import re
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from healthcare_dq.workflow import service

from . import reports, schemas
from .config import Settings
from .errors import ERRORS, response
from .middleware import LocalBoundary

Wid = Annotated[int, Path(ge=1, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query()]
KeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]
LOG = logging.getLogger(__name__)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Healthcare Data Quality Workflow", version="1",
                  responses={v[0]: {"model": schemas.Error} for v in ERRORS.values()})
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(LocalBoundary, token=settings.token)

    @app.exception_handler(service.WorkflowError)
    async def workflow_error(req, error):
        return response(req.scope, error.code if error.code in ERRORS else "INTERNAL_ERROR")

    @app.exception_handler(RequestValidationError)
    async def invalid(req, _error):
        return response(req.scope, "INVALID_REQUEST")

    @app.exception_handler(HTTPException)
    async def http_error(req, error):
        return response(req.scope, {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            error.status_code, "INVALID_REQUEST"))

    @app.exception_handler(psycopg.OperationalError)
    async def database_error(req, _error):
        return response(req.scope, "DATABASE_UNAVAILABLE")

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        try:
            catalog, resolve = settings.catalog()
            keys = list(catalog["fixtures"])
            for profile in catalog["profiles"]:
                resolve({"left_fixture": keys[0], "right_fixture": keys[1], "profile": profile})
            for connection in (settings.report_connection, settings.execute_connection):
                with connection() as conn:
                    if not reports.one(conn, "SELECT ready FROM workflow_schema_ready")["ready"]:
                        raise ValueError
        except (OSError, ValueError, KeyError, IndexError, psycopg.Error):
            raise service.WorkflowError("SERVICE_NOT_READY") from None
        return {"status": "ready"}

    @app.get("/v1/catalog", response_model=schemas.Catalog)
    def catalog():
        data, _ = settings.catalog()
        return {"fixtures": [{"key": k, **v} for k, v in data["fixtures"].items()],
                "profiles": sorted(data["profiles"])}

    def get_summary(wid):
        with settings.report_connection() as conn:
            return reports.summary(conn, wid)

    @app.post("/v1/workflows", response_model=schemas.Summary, status_code=201,
              responses={200: {"model": schemas.Summary}})
    def submit(payload: schemas.Submission, res: Response, request_key: KeyHeader = None):
        if request_key is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", request_key):
            raise service.WorkflowError("INVALID_IDEMPOTENCY_KEY")

        def resolve(request):
            return settings.catalog()[1](request)

        with settings.execute_connection() as conn:
            wid, created = service.register(conn, payload.model_dump(), request_key, resolve)
        res.status_code = 201 if created else 200
        res.headers["Location"] = f"/v1/workflows/{wid}"
        return get_summary(wid)

    @app.get("/v1/workflows", response_model=schemas.Page[schemas.Summary])
    def workflows(limit: Limit = 25, cursor: Cursor = None):
        with settings.report_connection() as conn:
            out = reports.page(conn, "workflow_report", ("created_at", "id"), (datetime, int),
                               {}, limit, cursor)
            return {**out, "items": [reports.summary(conn, row["id"]) for row in out["items"]]}

    @app.get("/v1/workflows/{wid}", response_model=schemas.Summary)
    def workflow(wid: Wid):
        return get_summary(wid)

    @app.post("/v1/workflows/{wid}/advance", response_model=schemas.Summary)
    def advance(wid: Wid, request: Request):
        LOG.info("workflow_advance", extra={"request_id": request.state.request_id, "workflow_id": wid})
        service.advance(wid, lambda request: settings.catalog()[1](request), settings.execute_connection)
        return get_summary(wid)

    @app.get("/v1/workflows/{wid}/quality-findings", response_model=schemas.Page[schemas.Finding])
    def findings(wid: Wid, side: schemas.Side, limit: Limit = 25, cursor: Cursor = None):
        step = 2 if side == "left" else 3
        with settings.report_connection() as conn:
            reports.completed(conn, wid, step)
            return reports.page(conn, "workflow_finding_report",
                                ("file_label", "entry_index", "rule_id", "rule_version", "pointer"),
                                (str, int, str, int, str), {"workflow_id": wid, "step": step}, limit, cursor)

    @app.get("/v1/workflows/{wid}/recommendations", response_model=schemas.Page[schemas.Recommendation])
    def recommendations(wid: Wid, method: schemas.Method, limit: Limit = 25, cursor: Cursor = None):
        with settings.report_connection() as conn:
            reports.completed(conn, wid, 4)
            return reports.page(conn, "workflow_recommendation_report", ("left_id", "right_id"), (str, str),
                                {"workflow_id": wid, "method": method}, limit, cursor)

    @app.get("/v1/workflows/{wid}/batches/{side}", response_model=schemas.Batch)
    def batch(wid: Wid, side: schemas.Side):
        step = 0 if side == "left" else 1
        with settings.report_connection() as conn:
            reports.completed(conn, wid, step)
            return reports.one(conn, "SELECT * FROM workflow_batch_report WHERE workflow_id=%s AND step=%s",
                               (wid, step))

    @app.get("/v1/workflows/{wid}/batches/{side}/files", response_model=schemas.Page[schemas.File])
    def files(wid: Wid, side: schemas.Side, limit: Limit = 25, cursor: Cursor = None):
        step = 0 if side == "left" else 1
        with settings.report_connection() as conn:
            reports.completed(conn, wid, step)
            return reports.page(conn, "workflow_file_report", ("file_label",), (str,),
                                {"workflow_id": wid, "step": step}, limit, cursor)

    # Document the bearer requirement for the interactive OpenAPI client.
    original = app.openapi

    def openapi():
        definition = original()
        definition.setdefault("components", {}).setdefault("securitySchemes", {})["LocalBearer"] = {
            "type": "http", "scheme": "bearer",
        }
        for path, routes in definition["paths"].items():
            if path.startswith("/v1/"):
                for operation in routes.values():
                    operation["security"] = [{"LocalBearer": []}]
        return definition

    app.openapi = openapi
    return app
