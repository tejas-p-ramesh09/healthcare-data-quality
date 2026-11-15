"""Public workflow schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

Key = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")]
Side = Literal["left", "right"]
Method = Literal["exact", "statistical"]
State = Literal["pending", "running", "interrupted", "failed", "completed"]


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    left_fixture: Key
    right_fixture: Key
    profile: Key


class Input(BaseModel):
    key: str
    source: str
    manifest: str


class Catalog(BaseModel):
    fixtures: list[Input]
    profiles: list[str]


class Failure(BaseModel):
    code: str
    retryable: bool


class ErrorDetail(Failure):
    message: str


class Error(BaseModel):
    error: ErrorDetail
    request_id: str


class Stage(BaseModel):
    name: str
    status: State
    batch_id: int | None
    run_id: int | None
    attempt_count: int
    updated_at: datetime
    counts: dict | None
    failure: Failure | None


class Summary(BaseModel):
    id: int
    status: State
    profile: str
    inputs: list[Input]
    ruleset_sha256: str
    model_sha256: str
    created_at: datetime
    updated_at: datetime
    stages: list[Stage]


class Batch(BaseModel):
    batch_id: int
    source_key: str
    manifest_sha256: str
    counts: dict


class File(BaseModel):
    file_label: str
    bytes: int
    sha256: str


class Finding(BaseModel):
    file_label: str
    entry_index: int
    rule_id: str
    rule_version: int
    category: str
    severity: str
    pointer: str
    diagnostic: str
    related_file_label: str | None
    related_index: int | None
    related_file_sha256: str | None
    file_sha256: str
    fhir_id: str
    resource_type: str
    batch_id: int


class Explanation(BaseModel):
    levels: dict[str, Literal["exact", "close", "conflict", "missing"]]
    contributions: dict[str, float]


class Recommendation(BaseModel):
    left_batch: int
    right_batch: int
    left_id: str
    right_id: str
    block_codes: list[str]
    method: Method
    score: float
    evidence: Explanation
    disposition: Literal["suggested_match", "review", "unlikely_match"]
    reason: str
    left_file_label: str
    left_entry_index: int
    left_file_sha256: str
    right_file_label: str
    right_entry_index: int
    right_file_sha256: str


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
