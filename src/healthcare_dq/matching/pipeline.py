"""Run the same candidate and score path for offline and persisted evaluation."""

from itertools import product
from time import perf_counter

from .core import VERSION, block, compare, decide, exact, fit, score
from .evaluation import report


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
    policy = {"low": min(values, default=0), "high": max(values, default=0) + 1, "margin": 0}
    for high in high_options:
        for low in [*sorted({x for x in values if x <= high}), high]:
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
