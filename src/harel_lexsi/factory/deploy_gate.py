"""T6: DeploymentGate. Deploy the retrained builder only if held-out verified success rises and the
mutation set does not regress; otherwise keep the old model. Model-free: it only reads numbers.

Thresholds live in `deploy_thresholds.json`, committed BEFORE any retrained model is judged, and the
decision records their SHA-256 so a later edit shows up. Missing evidence is never a deploy: a measure
that was not run keeps the old model.

Inputs are plain dicts so the gate does not depend on where the numbers came from:
    heldout:  {"n": int, "base": rate, "new": rate, "significant": bool | None}
    mutation: {"n": int, "base": rate, "new": rate} or None   (the same measure before and after)
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

THRESHOLDS = Path(__file__).with_name("deploy_thresholds.json")


@dataclass(frozen=True)
class Thresholds:
    min_heldout_gain: float          # new - base on held-out verified success, absolute
    min_heldout_n: int               # fewest held-out tasks the decision may rest on
    max_mutation_regression: float   # how far the mutation-set rate may fall (0.0 = not at all)
    min_mutation_n: int
    require_significance: bool       # also require the paired bootstrap to call the rise significant


def load_thresholds(path: Path = THRESHOLDS) -> tuple[Thresholds, str]:
    text = path.read_text()
    d = json.loads(text)
    return Thresholds(**{k: d[k] for k in Thresholds.__dataclass_fields__}), hashlib.sha256(
        text.encode()).hexdigest()


@dataclass
class Decision:
    deploy: bool
    reasons: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    thresholds_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"decision": "deploy" if self.deploy else "keep_old", "reasons": self.reasons,
                "numbers": self.numbers, "thresholds_sha256": self.thresholds_sha256}

    def render(self) -> str:
        h, m = self.numbers.get("heldout"), self.numbers.get("mutation")
        lines = [f"# DeploymentGate: {'DEPLOY' if self.deploy else 'KEEP THE OLD MODEL'}", ""]
        if h:
            lines.append(f"- held-out verified success: {h['base']:.0%} -> {h['new']:.0%} (n={h['n']})")
        if m:
            lines.append(f"- mutation set: {m['base']:.0%} -> {m['new']:.0%} (n={m['n']})")
        lines += [f"- {r}" for r in self.reasons]
        lines.append(f"- thresholds sha256: {self.thresholds_sha256}")
        return "\n".join(lines) + "\n"


def decide(heldout: Mapping[str, Any] | None, mutation: Mapping[str, Any] | None,
           thresholds: Thresholds, thresholds_sha256: str = "") -> Decision:
    reasons: list[str] = []
    ok = True
    numbers = {"heldout": dict(heldout) if heldout else None, "mutation": dict(mutation) if mutation else None}

    if not heldout:
        ok = False
        reasons.append("no held-out numbers: keep the old model")
    else:
        gain = round(heldout["new"] - heldout["base"], 6)
        if heldout["n"] < thresholds.min_heldout_n:
            ok = False
            reasons.append(f"held-out n={heldout['n']} is below the minimum {thresholds.min_heldout_n}")
        if gain < thresholds.min_heldout_gain or gain <= 0:
            ok = False
            reasons.append(f"held-out gain {gain:+.0%} is below the required {thresholds.min_heldout_gain:+.0%}")
        if thresholds.require_significance and heldout.get("significant") is not True:
            ok = False
            reasons.append("held-out rise is not significant")

    if not mutation:
        ok = False
        reasons.append("no mutation-set numbers: keep the old model")
    else:
        drop = round(mutation["base"] - mutation["new"], 6)
        if mutation["n"] < thresholds.min_mutation_n:
            ok = False
            reasons.append(f"mutation-set n={mutation['n']} is below the minimum {thresholds.min_mutation_n}")
        if drop > thresholds.max_mutation_regression:
            ok = False
            reasons.append(f"mutation set regressed by {drop:.0%} (allowed {thresholds.max_mutation_regression:.0%})")

    if ok:
        reasons.append("held-out success rose and the mutation set did not regress")
    return Decision(ok, reasons, numbers, thresholds_sha256)


def write_decision(out: Path, decision: Decision) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_decision.json").write_text(json.dumps(decision.as_dict(), indent=2))
    (out / "gate_decision.md").write_text(decision.render())
