"""Pure project quality policies for published FHIR projections."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

INSTANT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)")


@dataclass(frozen=True)
class Rule:
    code: str
    kind: str
    category: str
    pointer: str
    message: str
    version: int = 1
    severity: str = "warning"


RULES = (
    Rule("PATIENT_BIRTH_DATE_MISSING", "Patient", "completeness", "/birthDate",
         "Birth date is missing for downstream use."),
    Rule("OBSERVATION_CODE_NO_USABLE_CODING", "Observation", "structure", "/code/coding",
         "No coding has both a system and a code."),
    Rule("OBSERVATION_AFTER_ENCOUNTER_END", "Observation", "cross_resource", "/effectiveDateTime",
         "Effective time exceeds the linked encounter end plus the configured grace period."),
    Rule("CONDITION_ABATEMENT_BEFORE_ONSET", "Condition", "temporal", "/abatementDateTime",
         "Abatement time precedes onset time."),
    Rule("MEDICATION_ORDER_AUTHORED_ON_MISSING", "MedicationRequest", "domain", "/authoredOn",
         "An order has no authored time for the project timeline."),
)


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def instant(value):
    if not isinstance(value, str) or not INSTANT.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def check(rule, payload, encounter, grace_hours):
    match rule.code:
        case "PATIENT_BIRTH_DATE_MISSING":
            violated = not nonblank(payload.get("birthDate"))
        case "OBSERVATION_CODE_NO_USABLE_CODING":
            code = payload.get("code")
            codings = code.get("coding") if isinstance(code, dict) else None
            violated = not isinstance(codings, list) or not any(
                isinstance(c, dict) and nonblank(c.get("system")) and nonblank(c.get("code"))
                for c in codings
            )
        case "OBSERVATION_AFTER_ENCOUNTER_END":
            if encounter is None:
                return "not_applicable", "NO_LINKED_ENCOUNTER"
            when = instant(payload.get("effectiveDateTime"))
            end = instant((encounter.get("period") or {}).get("end"))
            if when is None or end is None:
                return "unable_to_assess", "INCOMPARABLE_TIMES"
            violated = when - end > timedelta(hours=grace_hours)
        case "CONDITION_ABATEMENT_BEFORE_ONSET":
            start = instant(payload.get("onsetDateTime"))
            end = instant(payload.get("abatementDateTime"))
            if start is None or end is None:
                return "unable_to_assess", "INCOMPARABLE_TIMES"
            violated = end < start
        case "MEDICATION_ORDER_AUTHORED_ON_MISSING":
            if payload.get("intent") != "order":
                return "not_applicable", "NOT_ORDER"
            violated = not nonblank(payload.get("authoredOn"))
        case _:
            raise ValueError(f"Unknown rule: {rule.code}")
    return ("finding", rule.code) if violated else ("pass", None)
