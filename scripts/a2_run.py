"""A2 (#35): the share of broken mutants each reward approved. Runs in an HF Job on a CPU; rules in docs/a2-plan.md.

    hf_job.py run scripts/a2_run.py --secret HF_INF_TOKEN

For the 19 broken handlers (17 mutants, 2 traps) and the 6 correct ones:
  1. verifier: the V7 gate against the fixtures, and again against the hidden contracts of spec/spec.md;
  2. visible tests: the tests that call the handler, run with the mutant in place;
  3. LLM judge (the A1 judge, unchanged): once with the code and the visible-test result, once with the code only.

Writes rows.jsonl, summary.json and summary.md to $OUT_DIR/a2/<name>/. The judge needs HF_INF_TOKEN (hosted inference).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = ROOT / "spec" / "spec.md"
VISIBLE = "app/tests/visible/test_visible.py"


def gate_verdicts(contracts, binding) -> tuple[dict[str, tuple[str, list[str]]], dict[str, str]]:
    """Case name -> (verdict, classes) for traps and mutants, and handler -> verdict for the reference handlers
    (pass only if every reference case of that handler passes; unknown if any is unknown)."""
    from harel_lexsi.verifier.gate import run_gate

    report = run_gate(contracts, binding)
    attacks = {o.case.name: (o.verdict, sorted(o.classes)) for o in report.outcomes if o.case.kind in ("trap", "mutant")}
    ref: dict[str, str] = {}
    for o in report.of("reference"):
        prev = ref.get(o.case.handler, "pass")
        ref[o.case.handler] = "fail" if "fail" in (prev, o.verdict) else ("unknown" if "unknown" in (prev, o.verdict) else "pass")
    return attacks, ref


def run_visible(module: str, handler: str, extra_path: Path, shop: Path) -> dict[str, int]:
    """The visible tests that call `handler`, with `module` as the handlers under test."""
    from harel_lexsi.adapters.shop_harness import visible_tests_for

    names = visible_tests_for(handler, (shop / VISIBLE).read_text())
    if not names:
        return {"passed": 0, "failed": 0}
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(shop), str(extra_path)]), "SHOP_HANDLERS": module, "COLUMNS": "200"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line",
                        *[f"{VISIBLE}::{n}" for n in names]], cwd=shop, env=env, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    num = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}", out)) else 0  # noqa: E731
    return {"passed": num("passed"), "failed": num("failed") + num("error")}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="")
    p.add_argument("--judge-model", default=None)
    p.add_argument("--limit", type=int, default=0, help="only the first N items (a smoke run)")
    a = p.parse_args()

    import train_t3

    from harel_lexsi.adapters import mutant_judge as MJ
    from harel_lexsi.adapters.judge import DEFAULT_MODEL, HostedJudge, rubric_sha256
    from harel_lexsi.verifier.contracts import load_binding, load_holdout
    from harel_lexsi.verifier.gate import DEFAULT_BINDING, DEFAULT_CONTRACTS, TRAPS, load_spec_contracts
    from harel_lexsi.verifier.replay import SHOP

    train_t3.start_postgres()
    from harel_lexsi.verifier.replay import _shop_db  # the gate replays on a migrated database; a fresh one has no tables
    db = _shop_db()
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    tasks = {t["id"]: t for t in map(json.loads, (ROOT / "tasks" / "tasks.jsonl").read_text().splitlines())}
    items = MJ.build_items(tasks, TRAPS)
    if a.limit:
        items = items[: a.limit]
    name = a.name or time.strftime("a2-%Y%m%d-%H%M%S")
    out = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "a2" / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"[a2] {name}: {sum(i.broken for i in items)} broken handlers and {sum(not i.broken for i in items)} correct ones", flush=True)

    # 1. the verifier, on both contract sets
    binding = load_binding(DEFAULT_BINDING)
    fixtures = [c for f in DEFAULT_CONTRACTS for c in load_holdout(f, binding)]
    hidden = load_spec_contracts(SPEC, binding)
    fx_attacks, fx_ref = gate_verdicts(fixtures, binding)
    hd_attacks, hd_ref = gate_verdicts(hidden, binding)
    print(f"[a2] verifier done: {len(fixtures)} fixture contracts, {len(hidden)} hidden contracts", flush=True)

    # 2 and 3. visible tests and the judge
    judge = HostedJudge(model=a.judge_model or DEFAULT_MODEL, api_key=os.environ["HF_INF_TOKEN"])
    helpers = MJ.HELPERS.read_text()
    tmp = Path(tempfile.mkdtemp(prefix="a2-handlers-"))
    rows = []
    for n, item in enumerate(items):
        if item.kind == "mutant":  # the reference handlers with this one handler replaced by the mutant
            module = "a2_" + re.sub(r"\W", "_", item.id)
            (tmp / f"{module}.py").write_text(
                "from reference.handlers import *  # noqa: F401,F403\nfrom reference import mutants as _m\n"
                f"{item.handler} = _m.build(next(x for x in _m.MUTANTS if x.id == {item.id!r}))\n")
        else:
            module = item.handlers_module
        tests = run_visible(module, item.handler, tmp, SHOP)
        task = MJ.judge_task(tasks[f"implement-{item.handler}"]["prompt"], helpers)
        with_tests = judge.judge(task, MJ.as_trajectory(item, task, tests))
        code_only = judge.judge(task, MJ.as_trajectory(item, task, None))
        if item.broken:
            vf, vh = fx_attacks.get(item.id, ("unknown", [])), hd_attacks.get(item.id, ("unknown", []))
        else:
            vf, vh = (fx_ref.get(item.handler, "unknown"), []), (hd_ref.get(item.handler, "unknown"), [])
        row = {"id": item.id, "kind": item.kind, "handler": item.handler, "operator": item.operator,
               "visible_passed": tests["passed"], "visible_failed": tests["failed"],
               "verifier_fixtures": vf[0], "verifier_fixtures_classes": vf[1],
               "verifier_hidden": vh[0], "verifier_hidden_classes": vh[1],
               "judge_with_tests": with_tests.score, "judge_with_tests_why": with_tests.explanation or with_tests.error,
               "judge_code_only": code_only.score, "judge_code_only_why": code_only.explanation or code_only.error,
               "code": item.code}
        rows.append(row)
        (out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"[a2] {n + 1}/{len(items)} {item.kind:7} {item.id:28} visible {tests['passed']}/{tests['passed'] + tests['failed']} "
              f"verifier hidden={vh[0]} fixtures={vf[0]} judge+tests={with_tests.score} code-only={code_only.score}", flush=True)

    summary = MJ.summarise(rows)
    meta = {"judge_model": judge.model, "judge_temperature": 0, "rubric_sha256": rubric_sha256(judge.system_prompt),
            "approve_at": MJ.APPROVE_AT, "fixture_contracts": len(fixtures), "hidden_contracts": len(hidden),
            "rules": "docs/a2-plan.md", "git_sha": os.environ.get("GIT_SHA", "unknown"), "run": name}
    (out / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2))
    md = MJ.render_markdown(meta, summary, rows)
    (out / "summary.md").write_text(md)
    print("[a2] RESULT\n" + md.split("## Every item")[0], flush=True)
    print(f"[a2] outputs in {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
