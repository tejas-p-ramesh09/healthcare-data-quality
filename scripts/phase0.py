"""Reproduce the Phase 0 experiment with the pinned Synthea release."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/synthea"
RULE = "PATIENT_BIRTH_DATE_MISSING"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_bundle(path):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle"
            or not isinstance(bundle.get("entry"), list)):
        raise ValueError(f"{path}: expected a Bundle with a list of entries")
    for entry in bundle["entry"]:
        if (not isinstance(entry, dict)
                or not isinstance(entry.get("resource"), dict)
                or not isinstance(entry["resource"].get("resourceType"), str)):
            raise ValueError(f"{path}: invalid Bundle entry resource")
    return bundle


def patients(bundle):
    for index, entry in enumerate(bundle["entry"]):
        patient = entry["resource"]
        if patient["resourceType"] == "Patient":
            if not isinstance(patient.get("id"), str) or not patient["id"].strip():
                raise ValueError("Expected a nonempty Patient.id")
            yield index, patient


def findings(bundle):
    result = []
    for index, patient in patients(bundle):
        value = patient.get("birthDate")
        if value is not None and not isinstance(value, str):
            raise ValueError("Expected Patient.birthDate to be a string or null")
        if value is None or not value.strip():
            result.append({
                "code": RULE,
                "patient_id": patient["id"],
                "path": f"/entry/{index}/resource/birthDate",
            })
    return result


def inject(source, destination, patient_id):
    bundle = read_bundle(source)
    matches = [(i, p) for i, p in patients(bundle) if p["id"] == patient_id]
    if len(matches) != 1:
        raise ValueError("Expected exactly one mutation target")
    index, patient = matches[0]
    value = patient.get("birthDate")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Mutation target must have a baseline birthDate")
    mutated = copy.deepcopy(bundle)
    del mutated["entry"][index]["resource"]["birthDate"]
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(mutated, stream, indent=2)
        stream.write("\n")
    return f"/entry/{index}/resource/birthDate"


def inventory(directory):
    records = {}
    counts = Counter()
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"No FHIR JSON found under {directory}")
    for path in paths:
        bundle = read_bundle(path)
        counts.update(entry["resource"]["resourceType"] for entry in bundle["entry"])
        for index, patient in patients(bundle):
            identifier = patient["id"]
            if identifier in records:
                raise ValueError(f"Duplicate Patient.id: {identifier}")
            records[identifier] = (patient.get("birthDate"), path, index)
    return records, counts, len(paths)


def generate(java, jar, directory, settings):
    directory.mkdir(parents=True, exist_ok=False)
    command = [
        str(java), f'-Duser.timezone={settings["timezone"]}', "-jar", str(jar),
        "-c", str(CONFIG / "phase0.properties"),
        "-p", str(settings["population"]),
        "-s", str(settings["population_seed"]),
        "-cs", str(settings["clinician_seed"]),
        "-r", settings["reference_date"], "-e", settings["end_date"],
        f"--exporter.baseDirectory={directory}", settings["location"],
    ]
    print(shlex.join(command), flush=True)
    with (directory / "synthea.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    return inventory(directory / "fhir")


def load_settings(path):
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: could not read configuration: {error}") from error
    if not isinstance(settings, dict):
        raise ValueError(f"{path}: expected a JSON object")

    strings = (
        "synthea_version", "jar_url", "jar_sha256", "location",
        "reference_date", "end_date", "timezone",
    )
    integers = ("population", "population_seed", "clinician_seed")
    for key in (*strings, *integers):
        if key not in settings:
            raise ValueError(f"{path}: missing required key '{key}'")
        value = settings[key]
        if key in strings and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{path}: '{key}' must be a nonempty string")
        if key in integers and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{path}: '{key}' must be an integer")
    if settings["population"] < 1:
        raise ValueError(f"{path}: 'population' must be positive")
    return settings


def run(java, jar, output):
    settings = load_settings(CONFIG / "run.json")
    jar_digest = sha256(jar)
    if jar_digest != settings["jar_sha256"]:
        raise ValueError("Synthea JAR checksum mismatch")
    print(f'Synthea {settings["synthea_version"]} SHA-256 {jar_digest}', flush=True)
    output.mkdir(parents=True, exist_ok=False)
    projections = []
    summaries = []
    for name in ("baseline", "repeat"):
        records, counts, bundles = generate(java, jar, output / name, settings)
        summary = {"run": name, "patients": len(records), "resources": counts, "bundles": bundles}
        print(json.dumps(summary, sort_keys=True), flush=True)
        if len(records) != settings["population"]:
            raise ValueError(f"{name}: expected {settings['population']} distinct patients")
        summaries.append(summary)
        projections.append({key: value[0] for key, value in records.items()})
        if name == "baseline":
            baseline = records
    if projections[0] != projections[1]:
        raise ValueError("Patient ID and birthDate projections differ")

    patient_id = min(baseline)
    _, source, index = baseline[patient_id]
    original = read_bundle(source)
    original_digest = sha256(source)
    if findings(original):
        raise ValueError("Expected no completeness findings in baseline Bundle")

    destination = output / "mutated.json"
    path = inject(source, destination, patient_id)
    mutated = read_bundle(destination)
    expected = copy.deepcopy(original)
    del expected["entry"][index]["resource"]["birthDate"]
    if mutated != expected or sha256(source) != original_digest:
        raise ValueError("Mutation changed more than the target field or altered the source")
    actual = findings(mutated)
    if actual != [{"code": RULE, "patient_id": patient_id, "path": path}]:
        raise ValueError(f"Unexpected findings: {actual}")
    report = {
        "runs": summaries,
        "patient_projection_matches": True,
        "source": str(source.relative_to(output)),
        "source_sha256_before": original_digest,
        "source_sha256_after": sha256(source),
        "expected_rule": RULE,
        "mutation_path": path,
        "baseline_findings": [],
        "mutated_findings": actual,
    }
    formatted = json.dumps(report, indent=2, sort_keys=True)
    (output / "report.json").write_text(formatted + "\n", encoding="utf-8")
    print(formatted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "data/generated/phase0"):
        parser.error("--output must be inside data/generated/phase0")
    try:
        run(args.java.resolve(), args.jar.resolve(), output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Phase 0 failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
