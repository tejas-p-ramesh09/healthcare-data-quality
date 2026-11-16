"""Emit the versioned API contract without credentials or database access."""

import json

from healthcare_dq.api.app import create_app
from healthcare_dq.api.config import Settings

if __name__ == "__main__":
    print(json.dumps(create_app(Settings(token="schema-generation-placeholder-only")).openapi(), sort_keys=True))
