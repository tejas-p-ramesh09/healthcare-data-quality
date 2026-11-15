"""Resolve fixed, checksummed operator-owned fixtures and profiles."""

import hashlib
import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from healthcare_dq.db import ROOT
from healthcare_dq.inputs import discover, manifest
from healthcare_dq.quality.engine import ruleset

from . import stages
from .service import WorkflowError


@dataclass(frozen=True)
class Assets:
    spec: dict
    files: tuple
    rules: dict
    model: dict


def safe(root, name, directory=False):
    rel = Path(name)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise ValueError("Unsafe catalog entry")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in catalog")
    if not (current.is_dir() if directory else current.is_file()):
        raise ValueError("Wrong catalog entry type")
    return current


def load_catalog(root=ROOT):
    data = json.loads(safe(root, "config/workflow/demo-v1.json").read_bytes())
    return data, partial(resolve, root, data)


def resolve(root, catalog, request):
    keys = [request["left_fixture"], request["right_fixture"]]
    if any(k not in catalog["fixtures"] for k in keys):
        raise WorkflowError("UNKNOWN_FIXTURE")
    profile_key = request["profile"]
    if profile_key not in catalog["profiles"]:
        raise WorkflowError("UNKNOWN_PROFILE")
    entries = [catalog["fixtures"][k] for k in keys]
    if entries[0]["source"] == entries[1]["source"]:
        raise WorkflowError("SAME_SOURCE_NOT_SUPPORTED")
    profile = catalog["profiles"][profile_key]
    try:
        fixture_root = safe(root, "fixtures/demo", directory=True)
        files = tuple(discover(safe(fixture_root, f["directory"], directory=True)) for f in entries)
        hashes = [manifest(f) for f in files]
        if hashes != [f["manifest"] for f in entries]:
            raise ValueError("Fixture manifest changed")
        rules_path = safe(root, profile["ruleset"])
        model_path = safe(root, profile["model"])
        for path, label in ((rules_path, "ruleset"), (model_path, "model")):
            if hashlib.sha256(path.read_bytes()).hexdigest() != profile[label + "_file_sha256"]:
                raise ValueError("Artifact checksum changed")
        config, model = ruleset(rules_path), json.loads(model_path.read_bytes())
        spec = {
            "profile": profile_key,
            "inputs": [{"key": key, "source": row["source"], "manifest": h}
                       for key, row, h in zip(keys, entries, hashes, strict=True)],
            "ruleset_file_sha256": profile["ruleset_file_sha256"],
            "model_file_sha256": profile["model_file_sha256"],
            **stages.revisions(config, model),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkflowError("ARTIFACT_UNAVAILABLE") from error
    return Assets(spec, files, config, model)
