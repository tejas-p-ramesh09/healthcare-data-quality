"""Two-pass interpretation of the supported Synthea FHIR collection subset."""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .inputs import MAX_ENTRIES, InputError, parse

TABLES = {
    "Patient": "patient",
    "Encounter": "encounter",
    "Observation": "observation",
    "Condition": "condition",
    "MedicationRequest": "medication_request",
}
ID = r"[A-Za-z0-9.-]{1,64}"
RELATIVE = re.compile(rf"([A-Za-z]+)/({ID})")
URN = re.compile(r"urn:uuid:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


@dataclass
class Entry:
    path: str
    index: int
    alias: str | None
    payload: dict
    fields: dict = field(default_factory=dict)

    @property
    def kind(self):
        return self.payload["resourceType"]

    @property
    def identifier(self):
        return self.payload.get("id")

    def error(self, code, key=""):
        suffix = f"/{key}" if key else ""
        return InputError(code, self.path, f"/entry/{self.index}/resource{suffix}")

    def get(self, key, kind=str, required=False, container=None):
        value = (self.payload if container is None else container).get(key)
        if value is None and not required:
            return None
        if type(value) is not kind or (kind is str and not value.strip()):
            raise self.error("INVALID_FIELD", key)
        return value


def prepare(files):
    entries, aliases, identities = [], {}, {}
    for file in files:
        for index, item in enumerate(parse(file)):
            if len(entries) >= MAX_ENTRIES:
                raise InputError("ENTRY_LIMIT", file.path)
            resource = item.get("resource") if isinstance(item, dict) else None
            if not isinstance(resource, dict) or not isinstance(resource.get("resourceType"), str):
                raise InputError("INVALID_ENTRY", file.path, f"/entry/{index}")
            alias = item.get("fullUrl")
            entry = Entry(file.path, index, alias, resource)
            if alias is not None:
                if not isinstance(alias, str) or not alias:
                    raise InputError("INVALID_FULL_URL", file.path, f"/entry/{index}/fullUrl")
                key = (file.path, alias)
                if key in aliases:
                    raise InputError("AMBIGUOUS_ALIAS", file.path, f"/entry/{index}/fullUrl")
                aliases[key] = entry
            identifier = entry.identifier
            if identifier is not None and not isinstance(identifier, str):
                raise entry.error("INVALID_ID", "id")
            if entry.kind in TABLES:
                if not isinstance(identifier, str) or not re.fullmatch(ID, identifier):
                    raise entry.error("INVALID_ID", "id")
                identity = entry.kind, identifier
                if identity in identities:
                    raise entry.error("DUPLICATE_ID", "id")
                identities[identity] = entry
            entries.append(entry)

    def resolve(entry, key, expected, required=False):
        obj = entry.get(key, dict, required)
        if obj is None:
            return None
        ref = entry.get("reference", str, True, obj)
        match = RELATIVE.fullmatch(ref)
        if URN.fullmatch(ref):
            target = aliases.get((entry.path, ref))
        elif match:
            if match[1] != expected:
                raise entry.error("WRONG_TARGET_TYPE", key)
            target = identities.get(tuple(match.groups()))
        else:
            raise entry.error("UNSUPPORTED_REFERENCE", key)
        if target is None:
            raise entry.error("UNRESOLVED_REFERENCE", key)
        if target.kind != expected:
            raise entry.error("WRONG_TARGET_TYPE", key)
        return target

    for entry in entries:
        if entry.kind not in TABLES:
            continue
        entry.fields = project(entry)
        if entry.kind != "Patient":
            entry.fields["patient_id"] = resolve(entry, "subject", "Patient", True).identifier
        if entry.kind not in ("Patient", "Encounter"):
            encounter = resolve(entry, "encounter", "Encounter")
            if encounter is not None:
                patient = resolve(encounter, "subject", "Patient", True)
                if patient.identifier != entry.fields["patient_id"]:
                    raise entry.error("CROSS_PATIENT_ENCOUNTER", "encounter")
            entry.fields["encounter_id"] = encounter.identifier if encounter else None
    return entries


def project(entry):
    get = entry.get
    match entry.kind:
        case "Patient":
            return {"gender": get("gender"), "birth_date": get("birthDate")}
        case "Encounter":
            period = get("period", dict) or {}
            klass = get("class", dict) or {}
            return {"status": get("status", required=True), "class_code": get("code", container=klass),
                    "period_start": get("start", container=period), "period_end": get("end", container=period)}
        case "Observation":
            quantity = get("valueQuantity", dict) or {}
            value = quantity.get("value")
            if value is not None and type(value) not in (int, Decimal):
                raise entry.error("INVALID_FIELD", "valueQuantity/value")
            return {"status": get("status", required=True), "code": get("code", dict, True),
                    "effective_at": get("effectiveDateTime"), "quantity": value,
                    "quantity_unit": get("unit", container=quantity), "value_string": get("valueString"),
                    "value_code": get("valueCodeableConcept", dict)}
        case "Condition":
            return {"code": get("code", dict), "clinical_status": get("clinicalStatus", dict),
                    "verification_status": get("verificationStatus", dict), "onset_at": get("onsetDateTime"),
                    "recorded_at": get("recordedDate")}
        case "MedicationRequest":
            return {"status": get("status", required=True), "intent": get("intent", required=True),
                    "authored_at": get("authoredOn"), "medication_code": get("medicationCodeableConcept", dict)}
