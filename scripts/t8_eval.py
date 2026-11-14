"""T8 step 5: the base model and the trained one, same server code, same harness, same settings; then the numbers and the
gate decision. Runs in an HF Job on a GPU, with AuditKIT installed (the paired bootstrap).

    hf_job.py run scripts/t8_eval.py --gpu --yes --with-auditkit -- --run collect-20261114-164908 --rule strict

The rules were written first, in docs/t8-plan.md. In short: the held-out tasks decide (n=8, 5 samples each); the mutation
set (the 11 patch tasks) must not fall; the trained model wins only if held-out verified success rises by at least 0.10 and
the mutation set does not fall; otherwise the old model is kept. Both models are served by scripts/serve_hf.py (the trained
one with the adapter merged into the pinned weights), one after the other so they do not share the GPU.

Outputs, in $OUT_DIR/t8/<run>/<rule>/eval-<name>/: eval_rows.jsonl (one row per episode), trajectories_<model>.jsonl,
eval_summary.json and .md, gate_decision.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = ROOT / "spec" / "spec.md"
API_MODEL = "openai/tiny-aya-global"


def load():
    from harel_lexsi.adapters.train_rollout import load_tasks

    split = json.loads((ROOT / "tasks" / "split.json").read_text())
    tasks = {t["id"]: t for t in load_tasks()}
    held = list(split["heldout"])
    mutation = [t for t, rec in tasks.items() if rec["kind"] == "patch"]  # patch tasks = a reference handler with one injected bug
    return split, tasks, held, mutation


# ----- one worker ------------------------------------------------------------------------------------------------

def worker(a: argparse.Namespace) -> int:
    from harel_lexsi.adapters.baseline import score_episode
    from harel_lexsi.adapters.hosted_rollout import EpisodeRunner
    from harel_lexsi.adapters.shop_harness import ShopSandbox, patch_start_for
    from harel_lexsi.verifier.score import score_files

    split, tasks, _, _ = load()
    out = Path(a.out)
    jobs = json.loads((out / f"jobs_{a.label}_{a.worker}.json").read_text())
    sandbox = ShopSandbox()  # SHOP_DSN (this worker's database) is in the environment
    runner = EpisodeRunner(sandbox, base_url=f"http://127.0.0.1:{a.port}/v1", api_key="local", api_model=API_MODEL,
                           max_steps=a.max_steps, max_new_tokens=a.max_new_tokens, temperature=a.temperature)
    score = lambda files, handler: score_files(files, handler, spec=SPEC)  # noqa: E731
    for tid, k in jobs:
        task = tasks[tid]
        t = time.time()
        traj, error = None, ""
        try:
            traj = runner.run(task)
        except Exception as e:  # noqa: BLE001  one bad episode must not end the worker
            error = f"{type(e).__name__}: {e}"[:200]
        start = patch_start_for(task["prompt"])
        er = score_episode(task, traj, scorer=score, split=split, start_text=start[1] if start else None,
                           visible=sandbox.last_tests, error=error)
        row = {**asdict(er), "model": a.label, "sample": k, "writes": len(sandbox.attempts),
               "refused": sum(not x["accepted"] for x in sandbox.attempts), "green": bool(sandbox.green),
               "seconds": round(time.time() - t, 1)}
        with (out / f"rows_{a.label}_{a.worker}.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        if traj is not None:
            with (out / f"trajectories_{a.label}_{a.worker}.jsonl").open("a") as f:
                f.write(json.dumps({"task_id": tid, "sample": k, **traj.to_dict()}, default=str) + "\n")
        print(f"[w{a.worker}:{a.label}] {tid} s{k}: {er.verdict} {'/'.join(er.classes) or '-'} visible "
              f"{er.visible_passed}/{er.visible_passed + er.visible_failed} writes {row['writes']} ({row['refused']} refused) "
              f"{row['seconds']}s {error}", flush=True)
    sandbox.close()
    return 0


# ----- the orchestrator ------------------------------------------------------------------------------------------

def run_model(label: str, adapter: Path | None, jobs: list, workers: int, out: Path, a: argparse.Namespace) -> None:
    from t8_collect import health

    cmd = [sys.executable, str(ROOT / "scripts" / "serve_hf.py"), "--port", str(a.port), "--max-batch", str(a.max_batch),
           "--top-p", str(a.top_p)] + (["--adapter", str(adapter)] if adapter else [])
    t0 = time.time()
    log = (out / f"server_{label}.log").open("w")
    server = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        while health(a.port) is None:
            if server.poll() is not None:
                raise RuntimeError(f"the {label} server exited:\n" + (out / f"server_{label}.log").read_text()[-1500:])
            if time.time() - t0 > 1500:
                raise RuntimeError(f"the {label} server did not come up in 25 minutes")
            time.sleep(5)
        print(f"[t8] {label} server up after {time.time() - t0:.0f}s; {len(jobs)} episodes, {workers} workers", flush=True)
        for i in range(workers):
            (out / f"jobs_{label}_{i}.json").write_text(json.dumps(jobs[i::workers]))
        procs = [subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(i), "--label", label, "--out", str(out),
             "--port", str(a.port), "--max-steps", str(a.max_steps), "--temperature", str(a.temperature),
             "--max-new-tokens", str(a.max_new_tokens)],
            env={**os.environ, "SHOP_DSN": f"postgresql://shop:shop@localhost:5433/shop_{i}"}) for i in range(workers)]
        t1, last = time.time(), 0.0
        while any(p.poll() is None for p in procs):
            time.sleep(5)
            if time.time() - last > 90:
                n = sum(len((out / f"rows_{label}_{i}.jsonl").read_text().splitlines())
                        for i in range(workers) if (out / f"rows_{label}_{i}.jsonl").exists())
                print(f"[t8] {label}: {n}/{len(jobs)} episodes in {time.time() - t1:.0f}s; server {health(a.port)}", flush=True)
                last = time.time()
            if time.time() - t1 > a.timeout_min * 60:
                print(f"[t8] {label}: TIMEOUT, stopping the workers and using what exists", flush=True)
                for p in procs:
                    p.terminate()
                break
        print(f"[t8] {label} finished in {time.time() - t1:.0f}s", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=90)  # free the GPU before the next model loads
        except subprocess.TimeoutExpired:
            server.kill()


def orchestrate(a: argparse.Namespace) -> int:
    import train_t3

    from harel_lexsi.adapters import evaluation as E
    from harel_lexsi.adapters.pairs import read_jsonl
    from harel_lexsi.factory.deploy_gate import decide, load_thresholds

    split, tasks, held, mutation = load()
    eval_ids = sorted(set(held) | set(mutation))  # the 8 held-out tasks and the 7 train patch tasks
    base = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "t8" / a.run / a.rule
    adapter = base / a.adapter
    if not (adapter / "adapter_config.json").exists():
        print(f"[t8] ABORT: no adapter at {adapter}", flush=True)
        return 2
    prov = json.loads((adapter / "provenance.json").read_text()) if (adapter / "provenance.json").exists() else {}
    if prov.get("held_out_in_training") is not False or set(prov.get("train_tasks", [])) & set(held):
        print("[t8] ABORT: the adapter's provenance does not show a train-only run", flush=True)
        return 2
    name = a.name or time.strftime("eval-%Y%m%d-%H%M%S")
    out = base / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"[t8] {name}: base vs trained ({a.adapter}); {len(eval_ids)} tasks ({len(held)} held-out + {len(mutation)} patch, "
          f"{len(set(mutation) - set(held))} of them seen in training) x {a.samples} samples = {len(eval_ids) * a.samples} "
          f"episodes per model; temperature {a.temperature}, top_p {a.top_p}, {a.max_new_tokens} tokens a turn", flush=True)

    train_t3.start_postgres(extra_databases=tuple(f"shop_{i}" for i in range(a.workers)))
    jobs = [(t, k) for k in range(a.samples) for t in eval_ids]  # interleaved: a partial run still covers every task
    for label, ad in (("base", None), ("trained", adapter)):
        run_model(label, ad, jobs, a.workers, out, a)

    rows = []
    for f in sorted(out.glob("rows_*.jsonl")):
        rows += read_jsonl(f)
    (out / "eval_rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for label in ("base", "trained"):
        with (out / f"trajectories_{label}.jsonl").open("w") as w:
            for f in sorted(out.glob(f"trajectories_{label}_*.jsonl")):
                w.write(f.read_text())
    c = E.compare(rows, heldout_ids=held, mutation_ids=mutation)
    thresholds, sha = load_thresholds()
    decision = decide(*E.gate_inputs(c), thresholds, sha)
    (out / "gate_decision.json").write_text(json.dumps(decision.as_dict(), indent=2))
    md = E.render_markdown(c, decision.render())
    (out / "eval_summary.md").write_text(md)
    summary = {k: v for k, v in c.items() if k != "rates"}
    summary.update({"per_task": c["rates"], "decision": decision.as_dict(), "adapter": a.adapter, "adapter_provenance": prov,
                    "settings": {"temperature": a.temperature, "top_p": a.top_p, "max_new_tokens": a.max_new_tokens,
                                 "max_steps": a.max_steps, "samples": a.samples},
                    "rules_sha256": hashlib.sha256((ROOT / "docs" / "t8-plan.md").read_bytes()).hexdigest()})
    (out / "eval_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print("[t8] RESULT\n" + md, flush=True)
    print(f"[t8] DECISION: {'DEPLOY the trained model' if decision.deploy else 'KEEP THE OLD MODEL'} ({'; '.join(decision.reasons)})", flush=True)
    print(f"[t8] outputs in {out}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--worker", type=int, default=None, help="internal")
    p.add_argument("--label", default="base", help="internal")
    p.add_argument("--run", default="", help="the collection folder under $OUT_DIR/t8")
    p.add_argument("--rule", default="strict")
    p.add_argument("--adapter", default="adapter", help="the adapter folder name inside <run>/<rule>")
    p.add_argument("--name", default="")
    p.add_argument("--out", default="", help="internal")
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--max-batch", type=int, default=16)
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--top-p", type=float, default=0.75)
    p.add_argument("--max-new-tokens", type=int, default=768)
    p.add_argument("--timeout-min", type=float, default=45.0, help="per model")
    a = p.parse_args()
    if a.worker is not None:
        return worker(a)
    if not a.run:
        p.error("--run is required")
    return orchestrate(a)


if __name__ == "__main__":
    raise SystemExit(main())
