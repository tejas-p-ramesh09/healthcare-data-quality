"""Local credentials and server-owned input catalog."""

import os
from dataclasses import dataclass

from healthcare_dq.db import ROOT, connect
from healthcare_dq.workflow.catalog import load_catalog


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
        return self.reader() if self.reader else connect("WORKFLOW_REPORT_DATABASE_URL")

    def execute_connection(self):
        return self.executor() if self.executor else connect("WORKFLOW_EXEC_DATABASE_URL")

    def catalog(self):
        return load_catalog(self.root)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("WORKFLOW_TOKEN", ""))
