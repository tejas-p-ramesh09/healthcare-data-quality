"""Deterministic demographic features, blocking, scoring and recommendations."""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

VERSION = 1
FIELDS = ("given", "family", "birth", "phone", "street", "zip")
LEVELS = ("exact", "close", "conflict", "missing")


def normalized(value):
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def objects(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def birth(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value):
        return ""
    try:
        date.fromisoformat({"4": value + "-01-01", "7": value + "-01"}.get(str(len(value)), value))
    except ValueError:
        return ""
    return value


def phone(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:\+1[ ().-]*)?[0-9 ().-]+", value.strip()):
        return ""
    value = value.strip()
    digits = re.sub(r"[^0-9]", "", value)
    if value.startswith("+") and len(digits) != 11:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


@dataclass(frozen=True)
class Features:
    names: tuple
    birth: str
    phones: tuple
    streets: tuple
    zips: tuple


def features(p):
    names = set()
    for name in objects(p.get("name")):
        if name.get("use") in ("old", "temp", "anonymous"):
            continue
        given = name.get("given")
        given = normalized(given[0]) if isinstance(given, list) and given else ""
        family = normalized(name.get("family"))
        if given or family:
            names.add((given, family))
    phones = {phone(t.get("value")) for t in objects(p.get("telecom")) if t.get("system") == "phone"}
    streets, zips = set(), set()
    for a in objects(p.get("address")):
        line = a.get("line")
        if isinstance(line, list) and line:
            street = normalized(line[0])
            street = " ".join({"street": "st", "avenue": "ave", "road": "rd"}.get(w, w)
                              for w in street.split())
            streets.add(street)
        z = a.get("postalCode")
        if isinstance(z, str) and re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", z):
            zips.add(z[:5])
    return Features(tuple(sorted(names)), birth(p.get("birthDate")), tuple(sorted(phones - {""})),
                    tuple(sorted(streets - {""})), tuple(sorted(zips)))


def keys(f):
    for given, family in f.names:
        if len(f.birth) == 10:
            if family:
                yield "dob_family", (f.birth, family[:3])
            if given:
                yield "dob_given", (f.birth, given[:3])
        if f.birth and given:
            for z in f.zips:
                yield "year_zip_initial", (f.birth[:4], z, given[0])
    for p in f.phones:
        yield "phone", (p,)


def block(left, right, *, max_block=10000, max_pairs=100000):
    indexes = defaultdict(list)
    for rid, record in sorted(right.items()):
        for k in sorted(set(keys(record))):
            indexes[k].append(rid)
    groups = defaultdict(list)
    for lid, record in sorted(left.items()):
        for k in sorted(set(keys(record))):
            if k in indexes:
                groups[k].append(lid)
    pairs = defaultdict(set)
    emissions = 0
    for k, lids in sorted(groups.items()):
        rids = indexes[k]
        if len(lids) * len(rids) > max_block:
            raise ValueError("OVERSIZED_BLOCK:" + k[0])
        for lid in lids:
            for rid in rids:
                pairs[lid, rid].add(k[0])
                emissions += 1
                if len(pairs) > max_pairs:
                    raise ValueError("CANDIDATE_LIMIT")
    return {p: sorted(v) for p, v in sorted(pairs.items())}, emissions


def level(a, b, fuzzy=False):
    if not a or not b:
        return "missing"
    if set(a) & set(b):
        return "exact"
    if fuzzy and any(SequenceMatcher(None, min(x, y)[:128], max(x, y)[:128], autojunk=False).ratio()
                     >= 0.8 for x in a for y in b):
        return "close"
    return "conflict"


def compare(a, b):
    def names(f, index):
        return tuple(p[index] for p in f.names if p[index])

    dob = "missing"
    if a.birth and b.birth:
        if a.birth == b.birth and len(a.birth) == 10:
            dob = "exact"
        elif a.birth.startswith(b.birth) or b.birth.startswith(a.birth):
            dob = "close"
        else:
            dob = "conflict"
    return dict(zip(FIELDS, (level(names(a, 0), names(b, 0), True),
                             level(names(a, 1), names(b, 1), True), dob,
                             level(a.phones, b.phones), level(a.streets, b.streets),
                             level(a.zips, b.zips)), strict=True))


def exact(a, b):
    return bool(len(a.birth) == 10 and a.birth == b.birth
                and any(g and f and (g, f) in b.names for g, f in a.names))


def fit(comparisons):
    counts = {label: Counter() for label in (False, True)}
    totals = Counter()
    for vector, match in comparisons:
        totals[match] += 1
        counts[match].update(vector.items())
    if not all(totals[k] for k in counts):
        raise ValueError("FIT_REQUIRES_BOTH_CLASSES")
    weights = {
        feature: {state: round(math.log(
            ((counts[True][feature, state] + 1) / (totals[True] + len(LEVELS)))
            / ((counts[False][feature, state] + 1) / (totals[False] + len(LEVELS)))), 8)
                for state in LEVELS}
        for feature in FIELDS
    }
    return {"weights": weights, "support": {str(k): totals[k] for k in counts},
            "levels": {str(k): {f: {s: counts[k][f, s] for s in LEVELS} for f in FIELDS} for k in counts}}


def score(vector, model):
    contributions = {f: model["weights"][f][s] for f, s in vector.items()}
    return round(sum(contributions.values()), 8), contributions


def decide(scores, low, high, margin, review_only=False):
    if not all(math.isfinite(x) for x in (low, high, margin)) or low > high or margin < 0:
        raise ValueError("INVALID_DECISION_POLICY")
    groups = defaultdict(list)
    for (left, right), value in scores.items():
        if not math.isfinite(value):
            raise ValueError("NONFINITE_SCORE")
        for endpoint in ((0, left), (1, right)):
            groups[endpoint].append(value)
    top = {k: sorted(v, reverse=True)[:2] for k, v in groups.items()}
    result = {}
    for pair, value in scores.items():
        competitors = [values[0] if value < values[0] else values[1]
                       for endpoint in enumerate(pair) if len(values := top[endpoint]) > 1]
        if value < low:
            result[pair] = ("unlikely_match", "BELOW_REVIEW_THRESHOLD")
        elif review_only or value < high:
            result[pair] = ("review", "BETWEEN_THRESHOLDS")
        elif any(other >= value - margin for other in competitors):
            result[pair] = ("review", "COMPETING_CANDIDATE")
        else:
            result[pair] = ("suggested_match", "ABOVE_THRESHOLD")
    return result
