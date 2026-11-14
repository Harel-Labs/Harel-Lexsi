"""T8 step 3, rerunnable: rebuild the preference pairs from a collection's saved candidates.

    hf_job.py run scripts/t8_pairs.py -- --run collect-20261114-164908

No model and no GPU: it reads $OUT_DIR/t8/<run>/candidates.jsonl (every distinct answer with the verifier's verdict) and
writes pairs under two rules for what counts as a winning ("chosen") answer, so the difference is visible before any
training:

  verifier   chosen = the verifier PASSES it (the hidden contracts hold)
  strict     chosen = the verifier PASSES it AND it passed every visible test (it also returns what the task describes)

The verifier judges what a handler does to the database; the visible tests also check return values. A verifier PASS that fails
its visible tests can return the wrong shape, and training toward it would teach that. `strict` avoids it, at the price of
fewer chosen answers. Both write pairs.jsonl / pairs_eval.jsonl / coverage.json into $OUT_DIR/t8/<run>/<rule>/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="the collection folder name under $OUT_DIR/t8")
    p.add_argument("--per-task-cap", type=int, default=40)
    a = p.parse_args()

    from harel_lexsi.adapters import pairs as P
    from harel_lexsi.adapters.train_rollout import load_tasks

    base = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "t8" / a.run
    held = set(json.loads((ROOT / "tasks" / "split.json").read_text())["heldout"])
    tasks = {t["id"]: t for t in load_tasks()}
    train_ids = [t for t in tasks if t not in held]
    cands = [P.Candidate(**d) for d in P.read_jsonl(base / "candidates.jsonl")]
    print(f"[pairs] {len(cands)} distinct answers from {base}", flush=True)

    summary = {}
    for rule in ("verifier", "strict"):
        use = cands
        if rule == "strict":  # a winning answer must also pass its visible tests; losers are unchanged
            use = [c for c in cands if c.verdict != "pass" or c.visible_ok]
        pairs = P.build_pairs(use, tasks, messages_for=P.inference_messages, heldout=held, per_task_cap=a.per_task_cap)
        P.assert_train_only(pairs, held)
        train, ev = P.split_pairs(pairs)
        report = P.coverage(use, pairs, train_ids)
        ok, reasons = P.go_no_go(report)
        out = base / rule
        out.mkdir(parents=True, exist_ok=True)
        P.write_jsonl(out / "pairs.jsonl", train)
        P.write_jsonl(out / "pairs_eval.jsonl", ev)
        report.update({"rule": rule, "train_pairs": len(train), "eval_pairs": len(ev), "go": ok, "no_go_reasons": reasons})
        (out / "coverage.json").write_text(json.dumps(report, indent=2))
        summary[rule] = report
        print(f"[pairs] {rule:8} {len(pairs):4} pairs ({len(train)} train + {len(ev)} eval) from {report['tasks_with_pairs']}/"
              f"{report['tasks']} tasks; hard negatives {report['hard_negative_share']:.0%}, not-Python {report['invalid_share']:.0%}; "
              f"{'GO' if ok else 'NO-GO: ' + '; '.join(reasons)}", flush=True)
        for tid, r in sorted(report["per_task"].items()):
            print(f"[pairs]   {rule:8} {tid:30} pass {r['pass']:3} fail {r['fail']:3} pairs {r['pairs']:3}", flush=True)
    (base / "pairs_summary.json").write_text(json.dumps({k: {x: v[x] for x in ("pairs", "train_pairs", "eval_pairs", "tasks_with_pairs",
                                                                                "hard_negative_share", "invalid_share", "go")}
                                                         for k, v in summary.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
