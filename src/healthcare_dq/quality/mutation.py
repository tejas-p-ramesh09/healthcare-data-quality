"""Freeze perturbations and controls without calling the detector."""

import hashlib
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import simplejson

from healthcare_dq.fhir import prepare
from healthcare_dq.inputs import discover, manifest, parse

from .engine import canonical, digest


def date(value):
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.utcoffset() is not None else None


def candidates(entries, grace):
    """Independent preconditions and expected label keys for experiment v1."""
    encounters = {e.identifier: e.payload for e in entries if e.kind == "Encounter"}
    for e in entries:
        p = e.payload
        if e.kind == "Patient" and p.get("birthDate", "").strip():
            yield e, "PATIENT_BIRTH_DATE_MISSING", "/birthDate", "remove", None
        if e.kind == "Observation":
            if any(c.get("system", "").strip() and c.get("code", "").strip()
                   for c in p.get("code", {}).get("coding", [])):
                yield e, "OBSERVATION_CODE_NO_USABLE_CODING", "/code/coding", "replace", []
            end = date(encounters.get(e.fields["encounter_id"], {}).get("period", {}).get("end"))
            at = date(p.get("effectiveDateTime"))
            if end and at and at - end <= grace:
                yield e, "OBSERVATION_AFTER_ENCOUNTER_END", "/effectiveDateTime", "replace", (
                    end + grace + timedelta(seconds=60)).isoformat()
        if e.kind == "Condition":
            start, end = date(p.get("onsetDateTime")), date(p.get("abatementDateTime"))
            if start and end and start <= end:
                yield e, "CONDITION_ABATEMENT_BEFORE_ONSET", "/abatementDateTime", "replace", (
                    start - timedelta(seconds=60)).isoformat()
        if e.kind == "MedicationRequest" and p.get("intent") == "order" and p.get("authoredOn", "").strip():
            yield e, "MEDICATION_ORDER_AUTHORED_ON_MISSING", "/authoredOn", "remove", None


def parent_and_key(payload, pointer):
    *parents, key = pointer.lstrip("/").split("/")
    for part in parents:
        payload = payload[part]
    return payload, key


def value_digest(value):
    return hashlib.sha256(simplejson.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def freeze(root, destination, settings, config):
    files = discover(root)
    source = manifest(files)
    if source != settings["baseline_manifest"] or settings["version"] != 1:
        raise ValueError("Unexpected experiment version or source manifest")
    if destination.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
            destination.resolve()):
        raise ValueError("Variant and source directories must be separate")
    groups = defaultdict(list)
    for candidate in candidates(prepare(files), timedelta(hours=config["encounter_grace_hours"])):
        groups[candidate[1]].append(candidate)
    quotas = settings["positives_per_rule"], settings["controls_per_rule"]
    eligible = Counter({rule: len(group) for rule, group in groups.items()})
    if set(groups) != set(config["rules"]) or any(n < sum(quotas) for n in eligible.values()):
        raise ValueError(f"Insufficient eligible controls and positives: {dict(eligible)}")

    selected = set()
    rows = []
    rng = random.Random(settings["seed"])
    for rule in sorted(groups):
        options = sorted(groups[rule], key=lambda case: (case[0].path, case[0].index))
        options = [case for case in options if (case[0].path, case[0].index) not in selected]
        if len(options) < sum(quotas):
            raise ValueError(f"Insufficient distinct targets for {rule}: {len(options)}")
        for i, (e, _, pointer, op, value) in enumerate(rng.sample(options, sum(quotas))):
            selected.add((e.path, e.index))
            parent, key = parent_and_key(e.payload, pointer)
            rows.append({
                "rule_id": rule, "rule_version": config["rules"][rule], "path": e.path,
                "entry_index": e.index, "resource_type": e.kind, "fhir_id": e.identifier,
                "pointer": pointer, "positive": i < quotas[0], "precondition": "eligible_v1",
                "operator": op if i < quotas[0] else "control", "operator_version": 1,
                "before_sha256": value_digest(parent[key]),
                "after_sha256": value_digest(value) if i < quotas[0] and op != "remove" else None,
                "replacement": value if i < quotas[0] else None,
            })
    destination.mkdir(parents=True, exist_ok=False)
    variants = destination / "fhir"
    variants.mkdir()
    by_file = defaultdict(list)
    for row in rows:
        if row["positive"]:
            by_file[row["path"]].append(row)
    for f in files:
        data = f.data
        if f.path in by_file:
            bundle = simplejson.loads(f.data, use_decimal=True)
            for row in by_file[f.path]:
                payload = bundle["entry"][row["entry_index"]]["resource"]
                parent, key = parent_and_key(payload, row["pointer"])
                if value_digest(parent[key]) != row["before_sha256"]:
                    raise ValueError("Mutation precondition changed")
                if row["operator"] == "remove":
                    del parent[key]
                else:
                    parent[key] = row["replacement"]
            data = simplejson.dumps(bundle, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        (variants / f.path).write_bytes(data)
    for f in discover(variants):
        original = next(item for item in files if item.path == f.path)
        after = parse(f)
        before = parse(original)
        for row in by_file[f.path]:
            parent, key = parent_and_key(before[row["entry_index"]]["resource"], row["pointer"])
            if row["operator"] == "remove":
                del parent[key]
            else:
                parent[key] = row["replacement"]
        if before != after:
            raise ValueError("Unexpected variant changes")
    if manifest(discover(root)) != source:
        raise ValueError("Original bytes changed")
    result = {"settings": settings, "source_manifest": source, "variant_manifest": manifest(discover(variants)),
              "ruleset_sha256": digest(config), "eligible": dict(eligible), "labels": rows,
              "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination / "manifest.json").write_text(canonical(result) + "\n")
    return result
