"""Stable, sanitized API errors."""

from starlette.responses import JSONResponse

ERRORS = {
    "INVALID_CURSOR": (400, "The pagination cursor is invalid for this list.", False),
    "INVALID_IDEMPOTENCY_KEY": (400, "Provide a valid Idempotency-Key.", False),
    "UNAUTHORIZED": (401, "Local bearer token required.", False),
    "WORKFLOW_NOT_FOUND": (404, "Workflow not found.", False),
    "NOT_FOUND": (404, "Route not found.", False),
    "METHOD_NOT_ALLOWED": (405, "Method not allowed.", False),
    "IDEMPOTENCY_KEY_CONFLICT": (409, "This key is bound to another request.", False),
    "WORKFLOW_BUSY": (409, "Another executor is advancing this workflow.", True),
    "STAGE_NOT_READY": (409, "This stage has not completed.", True),
    "STAGE_FAILED": (409, "This stage failed. Read workflow status for recovery.", False),
    "STAGE_INTERRUPTED": (409, "This stage needs to be resumed.", True),
    "STAGE_NOT_RETRYABLE": (409, "Stage cannot be retried under this identity.", False),
    "ARTIFACT_UNAVAILABLE": (409, "An approved fixture or profile artifact is unavailable.", True),
    "REQUEST_TOO_LARGE": (413, "Request exceeds the local API limit.", False),
    "UNSUPPORTED_MEDIA_TYPE": (415, "Use application/json.", False),
    "INVALID_REQUEST": (422, "Request does not match the API schema.", False),
    "UNKNOWN_FIXTURE": (422, "Select a fixture from the catalog.", False),
    "UNKNOWN_PROFILE": (422, "Select a profile from the catalog.", False),
    "SAME_SOURCE_NOT_SUPPORTED": (422, "Select different source namespaces.", False),
    "DATABASE_UNAVAILABLE": (503, "Check durable workflow status before retrying.", True),
    "SERVICE_NOT_READY": (503, "The API schema, catalog or database is not ready.", True),
    "EXECUTION_CAPACITY_BUSY": (503, "The local execution slot is occupied.", True),
    "INTERNAL_ERROR": (500, "Unexpected error. Check durable workflow status.", True),
}


def response(scope, code):
    status, message, retryable = ERRORS[code]
    request_id = scope["state"]["request_id"]
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    if status == 503:
        headers["Retry-After"] = "1"
    return JSONResponse({"error": {"code": code, "message": message, "retryable": retryable},
                         "request_id": request_id}, status_code=status, headers=headers)
