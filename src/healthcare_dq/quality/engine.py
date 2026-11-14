"""Ruleset identity and deterministic evaluation independent of storage."""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from .rules import RULES, check


@dataclass(frozen=True)
class Record:
    path: str
    index: int
    kind: str
    identifier: str
    payload: dict
    encounter: str | None = None


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def ruleset(path):
    config = json.loads(path.read_text())
    if (set(config) != {"contract", "encounter_grace_hours", "rules"}
            or type(config["contract"]) is not int or config["contract"] != 1
            or config["rules"] != {r.code: r.version for r in RULES}
            or type(config["encounter_grace_hours"]) is not int
            or not 0 <= config["encounter_grace_hours"] <= 168):
        raise ValueError("Unsupported quality ruleset")
    return config


def evaluate(records, config):
    encounters = {r.identifier: r for r in records if r.kind == "Encounter"}
    by_kind = {}
    counts = {}
    for rule in RULES:
        by_kind.setdefault(rule.kind, []).append(rule)
        counts[rule.code] = Counter(pass_=0, finding=0, not_applicable=0, unable_to_assess=0)
    findings = []
    for record in records:
        related = encounters[record.encounter] if record.encounter is not None else None
        for rule in by_kind.get(record.kind, ()):
            outcome, reason = check(rule, record.payload, related.payload if related else None,
                                    config["encounter_grace_hours"])
            counts[rule.code]["pass_" if outcome == "pass" else outcome] += 1
            if outcome in {"unable_to_assess", "not_applicable"}:
                counts[rule.code][reason] += 1
            if outcome == "finding":
                encounter = related if rule.category == "cross_resource" else None
                findings.append({"path": record.path, "entry_index": record.index,
                                     "rule_id": rule.code, "rule_version": rule.version,
                                     "category": rule.category, "severity": rule.severity,
                                     "pointer": rule.pointer, "diagnostic": rule.message,
                                     "related_path": encounter.path if encounter else None,
                                     "related_index": encounter.index if encounter else None})
    return counts, findings
