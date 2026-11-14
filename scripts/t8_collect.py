"""T8 steps 1 to 3 in one GPU job: sample the model, score every answer with the verifier, build the preference pairs.

    hf_job.py run scripts/t8_collect.py --gpu --yes -- --pilot                      # 2 tasks x 4 episodes, tests the pipeline
    hf_job.py run scripts/t8_collect.py --gpu --yes -- --episodes 24 --workers 10   # the real collection

What runs inside the job:
  * Postgres, with one database per worker (episodes reset their database and the verifier replays in it);
  * scripts/serve_hf.py: the pinned Tiny Aya weights behind a batching, OpenAI-compatible endpoint, so many
    episodes generate at once (a batch of 4 costs about the wall time of 1);
  * N worker processes. Each plays its share of (task, episode) pairs with the T2 baseline's harness
    (adapters/hosted_rollout.py), records every write the model made, and scores each distinct function with the
    verifier (adapters/pairs.py);
  * at the end: merge, build the pairs, print the coverage and the go/no-go.

Only the 16 TRAIN tasks are ever sampled. The held-out split is refused here and again in the pair builder.
Outputs go to $OUT_DIR/t8/<name>/: attempts, candidates, pairs, pairs_eval, coverage.json, and the logs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = ROOT / "spec" / "spec.md"
PILOT_TASKS = ["implement-get_order", "patch-checkout"]  # both train tasks, and both solved at least once in T2
API_MODEL = "openai/tiny-aya-global"


def tasks_and_split() -> tuple[dict[str, dict], set[str]]:
    from harel_lexsi.adapters.train_rollout import load_tasks

    held = set(json.loads((ROOT / "tasks" / "split.json").read_text())["heldout"])
    return {t["id"]: t for t in load_tasks()}, held


# ----- one worker ------------------------------------------------------------------------------------------------

def worker(a: argparse.Namespace) -> int:
    from harel_lexsi.adapters.hosted_rollout import EpisodeRunner
    from harel_lexsi.adapters.pairs import collect_candidates, verifier_scorer, write_jsonl
    from harel_lexsi.adapters.shop_harness import ShopSandbox

    tasks, held = tasks_and_split()
    out = Path(a.out) / f"worker_{a.worker}"
    out.mkdir(parents=True, exist_ok=True)
    jobs = json.loads((Path(a.out) / f"jobs_{a.worker}.json").read_text())
    sandbox = ShopSandbox()  # SHOP_DSN (this worker's database) is in the environment
    runner = EpisodeRunner(sandbox, base_url=f"http://127.0.0.1:{a.port}/v1", api_key="local", api_model=API_MODEL,
                           max_steps=a.max_steps, max_new_tokens=a.max_new_tokens, temperature=a.temperature)
    score = verifier_scorer(SPEC)
    for tid, ep in jobs:
        task = tasks[tid]
        assert tid not in held, f"held-out task {tid} in the job list"
        t = time.time()
        error = ""
        try:
            runner.run(task)
        except Exception as e:  # noqa: BLE001  one bad episode must not end the worker
            error = f"{type(e).__name__}: {e}"[:200]
        attempts = list(sandbox.attempts)
        cands = collect_candidates(task, attempts, score)
        with (out / "attempts.jsonl").open("a") as f:
            f.write(json.dumps({"task_id": tid, "episode": ep, "attempts": attempts}) + "\n")
        with (out / "candidates.jsonl").open("a") as f:
            f.write("".join(json.dumps(c.as_dict()) + "\n" for c in cands))
        row = {"task_id": tid, "episode": ep, "attempts": len(attempts), "refused": sum(not x["accepted"] for x in attempts),
               "green": bool(sandbox.green), "verdicts": dict(Counter(c.verdict for c in cands)), "error": error,
               "seconds": round(time.time() - t, 1), **runner.engine.usage}
        with (out / "episodes.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[w{a.worker}] {tid} ep{ep}: {row['attempts']} writes ({row['refused']} refused), "
              f"verdicts {row['verdicts']}, {row['seconds']}s {error}", flush=True)
    sandbox.close()
    return 0


# ----- the orchestrator ------------------------------------------------------------------------------------------

def health(port: int) -> dict | None:
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3).read())
    except Exception:  # noqa: BLE001
        return None


def orchestrate(a: argparse.Namespace) -> int:
    import train_t3

    from harel_lexsi.adapters import pairs as P

    tasks, held = tasks_and_split()
    train_ids = [t for t in tasks if t not in held]
    chosen = a.tasks.split(",") if a.tasks else (PILOT_TASKS if a.pilot else train_ids)
    if any(t in held or t not in tasks for t in chosen):
        print(f"[t8] ABORT: held-out or unknown task in {chosen}", flush=True)
        return 2
    episodes = a.episodes or (4 if a.pilot else 24)
    workers = a.workers or (4 if a.pilot else 10)
    name = a.name or ("pilot" if a.pilot else time.strftime("collect-%Y%m%d-%H%M%S"))
    out = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "t8" / name
    out.mkdir(parents=True, exist_ok=True)
    print(f"[t8] {name}: {len(chosen)} train tasks x {episodes} episodes = {len(chosen) * episodes} episodes, "
          f"{workers} workers, temperature {a.temperature}, top_p {a.top_p}, {a.max_new_tokens} new tokens a turn; held-out tasks excluded: {sorted(held)}", flush=True)

    t0 = time.time()
    train_t3.start_postgres(extra_databases=tuple(f"shop_{i}" for i in range(workers)))
    server_log = (out / "server.log").open("w")
    server = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "serve_hf.py"), "--port", str(a.port),
                               "--max-batch", str(a.max_batch), "--top-p", str(a.top_p)],
                              stdout=server_log, stderr=subprocess.STDOUT)
    try:
        while health(a.port) is None:
            if server.poll() is not None:
                print("[t8] ABORT: the server exited:\n" + (out / "server.log").read_text()[-1500:], flush=True)
                return 3
            if time.time() - t0 > 1500:
                print("[t8] ABORT: the server did not come up in 25 minutes", flush=True)
                return 3
            time.sleep(5)
        print(f"[t8] server up after {time.time() - t0:.0f}s (Postgres and model)", flush=True)

        jobs = [(t, ep) for ep in range(episodes) for t in chosen]  # interleaved: a partial run still covers every task
        for i in range(workers):
            (out / f"jobs_{i}.json").write_text(json.dumps(jobs[i::workers]))
        procs = []
        for i in range(workers):
            env = {**os.environ, "SHOP_DSN": f"postgresql://shop:shop@localhost:5433/shop_{i}"}
            procs.append(subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(i), "--out", str(out), "--port", str(a.port),
                 "--max-steps", str(a.max_steps), "--temperature", str(a.temperature),
                 "--max-new-tokens", str(a.max_new_tokens)], env=env))
        t1, last = time.time(), 0.0
        while any(p.poll() is None for p in procs):
            time.sleep(5)
            if time.time() - last > 60:
                done = sum(len((out / f"worker_{i}" / "episodes.jsonl").read_text().splitlines())
                           for i in range(workers) if (out / f"worker_{i}" / "episodes.jsonl").exists())
                h = health(a.port) or {}
                print(f"[t8] progress {done}/{len(jobs)} episodes in {time.time() - t1:.0f}s; server: {h}", flush=True)
                last = time.time()
            if time.time() - t1 > a.timeout_min * 60:
                print("[t8] TIMEOUT: stopping the workers and using what exists", flush=True)
                for p in procs:
                    p.terminate()
                break
        print(f"[t8] workers finished in {time.time() - t1:.0f}s; server: {health(a.port)}", flush=True)
    finally:
        server.terminate()

    # merge, pair, report
    cands: dict[str, P.Candidate] = {}
    episodes_rows = []
    for i in range(workers):
        w = out / f"worker_{i}"
        if (w / "candidates.jsonl").exists():
            for d in P.read_jsonl(w / "candidates.jsonl"):
                c = P.Candidate(**d)
                if c.fp in cands:
                    cands[c.fp].seen += c.seen
                    cands[c.fp].visible_ok = bool(cands[c.fp].visible_ok or c.visible_ok)
                else:
                    cands[c.fp] = c
        if (w / "episodes.jsonl").exists():
            episodes_rows += P.read_jsonl(w / "episodes.jsonl")
    candidates = list(cands.values())
    pairs = P.build_pairs(candidates, tasks, messages_for=P.inference_messages, heldout=held)
    P.assert_train_only(pairs, held)
    train_pairs, eval_pairs = P.split_pairs(pairs)
    report = P.coverage(candidates, pairs, chosen)
    ok, reasons = P.go_no_go(report)
    P.write_jsonl(out / "candidates.jsonl", (c.as_dict() for c in candidates))
    P.write_jsonl(out / "pairs.jsonl", train_pairs)
    P.write_jsonl(out / "pairs_eval.jsonl", eval_pairs)
    usage = {k: sum(r.get(k, 0) for r in episodes_rows) for k in ("attempts", "refused")}
    report.update({"name": name, "episodes_run": len(episodes_rows), "episode_errors": sum(bool(r["error"]) for r in episodes_rows),
                   "episodes_ending_green": sum(r["green"] for r in episodes_rows), "writes": usage["attempts"],
                   "refused_writes": usage["refused"], "train_pairs": len(train_pairs), "eval_pairs": len(eval_pairs),
                   "wall_seconds": round(time.time() - t0), "go": ok, "no_go_reasons": reasons})
    (out / "coverage.json").write_text(json.dumps(report, indent=2))

    print(f"[t8] RESULT {len(episodes_rows)} episodes ({report['episodes_ending_green']} ended green, "
          f"{report['episode_errors']} errors); {usage['attempts']} writes ({usage['refused']} refused as not Python); "
          f"{len(candidates)} distinct answers: {dict(Counter(c.verdict for c in candidates))}", flush=True)
    print(f"[t8] PAIRS {len(pairs)} ({len(train_pairs)} train + {len(eval_pairs)} eval) from {report['tasks_with_pairs']}/"
          f"{report['tasks']} tasks; hard negatives {report['hard_negative_share']:.0%}, not-Python {report['invalid_share']:.0%}; "
          f"rejected classes {report['rejected_classes']}", flush=True)
    for tid, r in sorted(report["per_task"].items()):
        print(f"[t8]   {tid:30} candidates {r['candidates']:3} pass {r['pass']:3} fail {r['fail']:3} unknown {r['unknown']:2} pairs {r['pairs']:3}", flush=True)
    print(f"[t8] {'GO' if ok else 'NO-GO: ' + '; '.join(reasons)}   (outputs in {out})", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--worker", type=int, default=None, help="internal: run as worker N")
    p.add_argument("--pilot", action="store_true", help="2 train tasks x 4 episodes: tests the whole pipeline")
    p.add_argument("--episodes", type=int, default=0, help="episodes per task (default 24; pilot 4)")
    p.add_argument("--workers", type=int, default=0, help="parallel workers (default 10; pilot 4)")
    p.add_argument("--tasks", default="", help="comma-separated train task ids (default: all 16)")
    p.add_argument("--name", default="")
    p.add_argument("--out", default="")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--max-batch", type=int, default=16)
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--top-p", type=float, default=0.75, help="nucleus sampling, as the hosted provider (unrestricted drifts)")
    p.add_argument("--max-new-tokens", type=int, default=768, help="per turn; a function is 300 to 600 tokens")
    p.add_argument("--timeout-min", type=float, default=150.0, help="stop sampling after this long and use what exists")
    a = p.parse_args()
    return worker(a) if a.worker is not None else orchestrate(a)


if __name__ == "__main__":
    raise SystemExit(main())
