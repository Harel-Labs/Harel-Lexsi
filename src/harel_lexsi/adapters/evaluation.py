"""T8 step 5: from episodes to the numbers the rules in docs/t8-plan.md call for. Pure functions, no model.

Rows are one episode each: {"model": "base" | "trained", "task_id", "split", "kind", "verdict", "visible_passed",
"visible_failed", "wrote_files", "steps", "refused", ...}. A task's score for a model is the share of its samples whose final
files the verifier PASSES. A set's score is the mean over its tasks (every task counts the same, however many samples it has).

    held-out set      the held-out tasks: the decision
    mutation set      the patch tasks (a reference handler with one injected bug), seen in training or not: "no regression"

UNKNOWN and FAIL both count as not passing, as in T2. The significance test is AuditKIT's paired bootstrap over tasks.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def visible_ok(row: Mapping[str, Any]) -> bool:
    return row["visible_failed"] == 0 and row["visible_passed"] > 0


def task_rates(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """model -> task id -> {n, verified, visible, rate, visible_rate}."""
    by: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["model"]][r["task_id"]].append(r)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for model, tasks in by.items():
        out[model] = {}
        for tid, rs in tasks.items():
            n = len(rs)
            v = sum(r["verdict"] == "pass" for r in rs)
            vis = sum(visible_ok(r) for r in rs)
            out[model][tid] = {"n": n, "verified": v, "visible": vis, "rate": v / n, "visible_rate": vis / n}
    return out


def set_rate(rates: Mapping[str, Mapping[str, Any]], task_ids: Iterable[str], key: str = "rate") -> float | None:
    """Mean over the tasks that have at least one episode; None when none do."""
    vals = [rates[t][key] for t in task_ids if t in rates]
    return sum(vals) / len(vals) if vals else None


def paired(rates: Mapping[str, Mapping[str, Mapping[str, Any]]], task_ids: Sequence[str], base: str, new: str,
           key: str = "rate") -> list[tuple[float, float]]:
    """(base, new) per-task pairs for the tasks both models ran."""
    return [(rates[base][t][key], rates[new][t][key]) for t in sorted(task_ids) if t in rates.get(base, {}) and t in rates.get(new, {})]


def compare(rows: Sequence[Mapping[str, Any]], *, heldout_ids: Sequence[str], mutation_ids: Sequence[str],
            base: str = "base", new: str = "trained", n_resamples: int = 5000, seed: int = 42) -> dict[str, Any]:
    """Held-out and mutation-set numbers for `new` against `base`, each with its n, delta and significance."""
    from auditkit._bootstrap import paired_bootstrap

    rates = task_rates(rows)

    def block(ids: Sequence[str], key: str = "rate") -> dict[str, Any]:
        pairs = paired(rates, ids, base, new, key)
        out: dict[str, Any] = {"n": len(pairs), "base": None, "new": None, "delta": None, "p_value": None, "significant": None}
        if pairs:
            out["base"] = sum(b for b, _ in pairs) / len(pairs)
            out["new"] = sum(c for _, c in pairs) / len(pairs)
            out["delta"] = out["new"] - out["base"]
        if len(pairs) >= 2:
            stats = paired_bootstrap(pairs, n_resamples=n_resamples, seed=seed)
            out["p_value"], out["significant"] = stats.get("p_value"), stats.get("significant")
        return out

    def extra(ids: Sequence[str]) -> dict[str, Any]:
        rs = [r for r in rows if r["task_id"] in set(ids)]
        res: dict[str, Any] = {}
        for m in (base, new):
            mine = [r for r in rs if r["model"] == m]
            writes = sum(r.get("writes", 0) for r in mine)
            res[m] = {"episodes": len(mine), "visible_rate": (sum(visible_ok(r) for r in mine) / len(mine)) if mine else None,
                      "refused_share": (sum(r.get("refused", 0) for r in mine) / writes) if writes else None}
        return res

    return {"heldout": block(heldout_ids), "mutation": block(mutation_ids),
            "heldout_visible": block(heldout_ids, "visible_rate"), "mutation_visible": block(mutation_ids, "visible_rate"),
            "episode_stats": {"heldout": extra(heldout_ids), "mutation": extra(mutation_ids)}, "rates": rates}


def gate_inputs(c: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The two dicts `factory.deploy_gate.decide` reads. None when a set has no paired task (no evidence)."""
    def pick(b: Mapping[str, Any], with_sig: bool) -> dict[str, Any] | None:
        if not b["n"]:
            return None
        d: dict[str, Any] = {"n": b["n"], "base": b["base"], "new": b["new"]}
        if with_sig:
            d["significant"] = b["significant"]
        return d
    return pick(c["heldout"], True), pick(c["mutation"], False)


def render_markdown(c: Mapping[str, Any], decision_text: str, *, base: str = "base", new: str = "trained") -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    lines = [f"# T8 evaluation: {new} against {base}", ""]
    lines += ["| set | tasks (n) | " + base + " | " + new + " | change | p (paired bootstrap over tasks) |", "|---|---|---|---|---|---|"]
    for label, key in (("held-out, verified success", "heldout"), ("mutation set (patch tasks), verified success", "mutation"),
                       ("held-out, visible tests pass", "heldout_visible"), ("mutation set, visible tests pass", "mutation_visible")):
        b = c[key]
        d = "n/a" if b["delta"] is None else f"{b['delta']:+.0%}"
        p = "n/a" if b["p_value"] is None else f"{b['p_value']:.3f}"
        lines.append(f"| {label} | {b['n']} | {pct(b['base'])} | {pct(b['new'])} | {d} | {p} |")
    lines += ["", "| episodes | " + base + " | " + new + " |", "|---|---|---|"]
    for set_name in ("heldout", "mutation"):
        s = c["episode_stats"][set_name]
        lines.append(f"| {set_name}: episodes, share of writes refused as not Python | "
                     f"{s[base]['episodes']}, {pct(s[base]['refused_share'])} | {s[new]['episodes']}, {pct(s[new]['refused_share'])} |")
    lines += ["", "| task | n | " + base + " verified | " + new + " verified |", "|---|---|---|---|"]
    r = c["rates"]
    for tid in sorted(set(r.get(base, {})) | set(r.get(new, {}))):
        b, n = r.get(base, {}).get(tid), r.get(new, {}).get(tid)
        lines.append(f"| {tid} | {(b or n)['n']} | {pct(b['rate']) if b else 'n/a'} | {pct(n['rate']) if n else 'n/a'} |")
    return "\n".join(lines) + "\n\n" + decision_text
