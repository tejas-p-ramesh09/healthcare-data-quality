"""Bound and authenticate local HTTP requests before dispatch."""

import hmac
import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders

from .errors import response

LOG = logging.getLogger(__name__)
BODY_LIMIT = 16 * 1024


class LocalBoundary:
    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        start = perf_counter()
        headers = Headers(scope=scope)

        sent = False

        async def tagged(message):
            nonlocal sent
            if message["type"] == "http.response.start":
                sent = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                route = getattr(scope.get("route"), "path", None)
                LOG.info("http_request", extra={"request_id": request_id, "route": route,
                                                "http_status": message["status"],
                                                "duration_ms": round((perf_counter() - start) * 1000, 3)})
            await send(message)

        if scope["path"].startswith("/v1/"):
            authorization = headers.get("authorization", "")
            if (not authorization.startswith("Bearer ") or
                    not hmac.compare_digest(authorization[7:].encode(), self.token)):
                await response(scope, "UNAUTHORIZED")(scope, receive, tagged)
                return
        if scope["method"] == "POST":
            if (scope["path"] == "/v1/workflows" and
                    headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
                await response(scope, "UNSUPPORTED_MEDIA_TYPE")(scope, receive, tagged)
                return
            body = bytearray()
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                data = chunk.get("body", b"")
                if len(body) + len(data) > BODY_LIMIT:
                    await response(scope, "REQUEST_TOO_LARGE")(scope, receive, tagged)
                    return
                body.extend(data)
                if not chunk.get("more_body"):
                    break
            if body and scope["path"].endswith("/advance"):
                await response(scope, "INVALID_REQUEST")(scope, receive, tagged)
                return
            original = receive
            first = True

            async def buffered():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original()

            receive = buffered
        try:
            await self.app(scope, receive, tagged)
        except Exception as error:  # noqa: BLE001 - keep driver details out of HTTP and access logs
            LOG.error("request_failed", extra={"request_id": request_id,
                                               "error_type": type(error).__name__})
            if not sent:
                await response(scope, "INTERNAL_ERROR")(scope, receive, tagged)
