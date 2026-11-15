"""Run the same candidate and score path for offline and persisted evaluation."""

import math
from collections import Counter
from itertools import product
from time import perf_counter

from .core import FIELDS, LEVELS, VERSION, block, compare, decide, exact, fit, score
from .evaluation import report


def validate_model(model):
    def finite(value):
        return type(value) in (int, float) and -math.inf < value < math.inf

    try:
        weights = model["statistical"]["weights"]
        policies = model["policies"]
        if (type(model["version"]) is not int or model["version"] != VERSION
                or set(weights) != set(FIELDS) or set(policies) != {"exact", "statistical"}):
            raise ValueError("INVALID_MATCHING_MODEL")
        for levels in weights.values():
            if set(levels) != set(LEVELS) or not all(finite(v) for v in levels.values()):
                raise ValueError("INVALID_MATCHING_MODEL")
        if not math.isfinite(sum(max(abs(v) for v in levels.values()) for levels in weights.values())):
            raise ValueError("INVALID_MATCHING_MODEL")
        for policy in policies.values():
            if (set(policy) - {"review_only"} != {"low", "high", "margin"}
                    or type(policy.get("review_only", False)) is not bool
                    or not all(finite(policy[k]) for k in ("low", "high", "margin"))
                    or policy["low"] > policy["high"] or policy["margin"] < 0):
                raise ValueError("INVALID_MATCHING_MODEL")
        if model.get("default", "exact") not in policies:
            raise ValueError("INVALID_MATCHING_MODEL")
    except (AttributeError, KeyError, TypeError, OverflowError):
        raise ValueError("INVALID_MATCHING_MODEL") from None



def rank(left, right, model, *, timings=False):
    start = perf_counter()
    pairs, emissions = block(left, right)
    blocked = perf_counter()
    rows = {}
    for pair, rules in pairs.items():
        a, b = left[pair[0]], right[pair[1]]
        vector = compare(a, b)
        value, weights = score(vector, model["statistical"])
        rows[pair] = {"blocks": rules, "levels": vector, "exact": int(exact(a, b)),
                      "statistical": value, "contributions": weights}
    if timings:
        return rows, emissions, {"block": blocked - start, "score": perf_counter() - blocked}
    return rows, emissions


def recommendations(rows, method, policy):
    return decide({pair: row[method] for pair, row in rows.items()}, **policy)


def train(left, right, truth):
    pairs, _ = block(left, right)
    return fit((compare(left[l], right[r]), truth["left"][l] == truth["right"][r])
               for l, r in pairs)


def choose(rows, truth, method):
    values = sorted({row[method] for row in rows.values()})
    high_options = [1] if method == "exact" else values
    sweep = []
    best = None
    # Put the suggestion threshold above every selection score. Include only
    # whole tied groups in the review band, keeping all other pairs scored.
    high = math.nextafter(max(values, default=0), math.inf)
    frequencies = Counter(row[method] for row in rows.values())
    low, reviews = high, 0
    for value in reversed(values):
        if reviews + frequencies[value] > 20:
            break
        reviews += frequencies[value]
        low = value
    policy = {"low": low, "high": high, "margin": 0, "review_only": True}
    for high in high_options:
        for low in sorted({x for x in values if x <= high} | {high}):
            for margin in ((0,) if method == "exact" else (0, 0.5)):
                candidate = {"low": low, "high": high, "margin": margin}
                result = report(truth, recommendations(rows, method, candidate))
                m = result["full"]
                reviews = result["dispositions"].get("review", 0)
                sweep.append({"policy": candidate, "metrics": m, "reviews": reviews})
                if m["tp"] + m["fp"] < 5 or reviews > 20:
                    continue
                key = m["precision"], m["recall"], -reviews
                if best is None or key > best:
                    best, policy = key, candidate
    result = report(truth, recommendations(rows, method, policy))
    return policy, result, sweep


def select(fit_data, selection_data):
    artifact = {"version": VERSION, "statistical": train(*fit_data)}
    left, right, truth = selection_data
    rows, _ = rank(left, right, artifact)
    artifact["policies"], results, sweeps = {}, {}, {}
    for method in ("exact", "statistical"):
        artifact["policies"][method], results[method], sweeps[method] = choose(rows, truth, method)
    a, b = (results[k]["full"] for k in ("exact", "statistical"))
    artifact["default"] = ("statistical" if (b["precision"] or 0, b["recall"] or 0) >
                           (a["precision"] or 0, a["recall"] or 0) else "exact")
    return artifact, {"selection": results, "sweeps": sweeps}


def assess(left, right, truth, artifact):
    rows, emissions = rank(left, right, artifact)
    result = {m: report(truth, recommendations(rows, m, p)) for m, p in artifact["policies"].items()}
    result["blocking_emissions"] = emissions
    result["comparisons"] = len(rows)
    if len(left) * len(right) <= 10000:
        all_pairs = {(l, r): int(exact(left[l], right[r])) for l, r in product(left, right)}
        result["exhaustive_exact"] = report(truth, decide(all_pairs, **artifact["policies"]["exact"]))
    return result
