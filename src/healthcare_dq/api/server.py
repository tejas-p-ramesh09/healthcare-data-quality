"""Local server entry point with metadata-only JSON logging."""

import json
import logging

import uvicorn

FIELDS = ("request_id", "route", "http_status", "duration_ms", "workflow_id",
          "stage", "attempt", "code", "error_type", "outcome")


class EventFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"event": record.getMessage(), "level": record.levelname,
                           **{k: getattr(record, k) for k in FIELDS if hasattr(record, k)}})


def main():
    handler = logging.StreamHandler()
    handler.setFormatter(EventFormatter())
    logger = logging.getLogger("healthcare_dq")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    uvicorn.run("healthcare_dq.api.app:create_app", factory=True, host="127.0.0.1", port=8000,
                access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
