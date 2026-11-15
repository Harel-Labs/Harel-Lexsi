"""Publish the submission's Hugging Face artefacts: one model repo, one dataset repo, one collection.

    uv run python scripts/hf_publish.py --owner bhumiheda --dry-run     # stage and show what would be uploaded
    uv run python scripts/hf_publish.py --owner bhumiheda               # create the repos (public) and upload

Tokens, both from .env, never printed:
  HF_SUBMISSION_TOKEN (or HF_PUBLISH_TOKEN)  write access to repos and collections in the --owner namespace
  HF_JOB_TOKEN      read access to the private bucket <job-user>/jobs-artifacts where the jobs left the adapter and the
                    T8 run data (only downloaded from, never written)

What goes where:
  model    <owner>/harel-tiny-aya-dpo-lora        the T8 LoRA adapter, its provenance.json, a model card
  dataset  <owner>/harel-shop-verifier-runs       run outputs: T2 baseline, T8 collection / pairs / evaluation, A1, A2,
                                                 the gate log, the task set; a dataset card
  collection "Harel: verifier-gated fine-tuning"  both repos

Nothing here contains the hidden contracts (spec/spec.md's Holdout section) or any token. The adapter is a derivative of
CohereLabs/tiny-aya-global (CC-BY-NC-4.0), so it is published under the same licence.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "hf://buckets/Dead-Bytes/jobs-artifacts/harel-out/t8/collect-20261114-164908"
MODEL_REPO, DATASET_REPO = "harel-tiny-aya-dpo-lora", "harel-shop-verifier-runs"
COLLECTION = "Harel: verifier-gated fine-tuning"

# (bucket path relative to BUCKET, destination path in the dataset repo)
BUCKET_DATA = [
    ("coverage.json", "t8/collection/coverage.json"),
    ("candidates.jsonl", "t8/collection/candidates.jsonl"),
    ("strict/pairs.jsonl", "t8/pairs/strict/pairs.jsonl"),
    ("strict/pairs_eval.jsonl", "t8/pairs/strict/pairs_eval.jsonl"),
    ("strict/coverage.json", "t8/pairs/strict/coverage.json"),
    ("verifier/pairs.jsonl", "t8/pairs/verifier_only/pairs.jsonl"),
    ("verifier/pairs_eval.jsonl", "t8/pairs/verifier_only/pairs_eval.jsonl"),
    ("pairs_summary.json", "t8/pairs/pairs_summary.json"),
    ("strict/eval-20261114-192806/trajectories_base.jsonl", "t8/evaluation/trajectories_base.jsonl"),
    ("strict/eval-20261114-192806/trajectories_trained.jsonl", "t8/evaluation/trajectories_trained.jsonl"),
]
# (repo path, destination path in the dataset repo)
LOCAL_DATA = [
    ("docs/t2-baseline-run/results.jsonl", "t2_baseline/results.jsonl"),
    ("docs/t2-baseline-run/summary.json", "t2_baseline/summary.json"),
    ("docs/t2-baseline-run/summary.md", "t2_baseline/summary.md"),
    ("runs/hosted-tiny-aya-v3-t09-20261114/trajectories.jsonl", "t2_baseline/trajectories.jsonl"),
    ("docs/t8-run/eval_rows.jsonl", "t8/evaluation/eval_rows.jsonl"),
    ("docs/t8-run/eval_summary.json", "t8/evaluation/eval_summary.json"),
    ("docs/t8-run/eval_summary.md", "t8/evaluation/eval_summary.md"),
    ("docs/t8-run/gate_decision.json", "t8/evaluation/gate_decision.json"),
    ("docs/a1-judge-run/judge_scores.jsonl", "a1_judge/judge_scores.jsonl"),
    ("docs/a1-judge-run/judge_summary.json", "a1_judge/judge_summary.json"),
    ("docs/a2-run/rows.jsonl", "a2_reward_hacking/rows.jsonl"),
    ("docs/a2-run/summary.json", "a2_reward_hacking/summary.json"),
    ("docs/a2-run/summary.md", "a2_reward_hacking/summary.md"),
    ("docs/gate-holdout.md", "v7_gate/gate-holdout.md"),
    ("tasks/tasks.jsonl", "tasks/tasks.jsonl"),
    ("tasks/split.json", "tasks/split.json"),
]

MODEL_CARD = """---
license: cc-by-nc-4.0
base_model: CohereLabs/tiny-aya-global
library_name: peft
tags: [lora, dpo, agents, verifier, harel]
---

# Harel: Tiny Aya Global + DPO LoRA (verifier-labelled pairs)

A LoRA adapter (r=16, alpha=32, all linear layers) for [CohereLabs/tiny-aya-global](https://huggingface.co/CohereLabs/tiny-aya-global)
(revision `af89d219b53ed9b13b8a4645f9c8028973510324`), trained with DPO on preference pairs whose labels come from **Harel**, a
model-free verifier that checks what a tool call changed in a Postgres database against hidden contracts.

**Read this first: this adapter did not improve the measured outcome, and it was not deployed.** Under rules written before
the evaluation, the held-out verified success was 12% for the base model and 12% with this adapter (8 tasks, 5 samples each).
The adapter raised the visible-test pass rate (45% to 55%) and cut invalid Python (14% to 5%), but answers that pass the
visible tests and fail the verifier rose from 23 to 28 of 75. The deployment gate kept the base model. It is published so the
result can be checked and reproduced.

- Training data: 303 train and 30 eval pairs from 10 of the 16 training tasks; chosen = an answer the verifier passes and that
  passes its visible tests, rejected = an answer the verifier fails. No held-out task is in the data.
- Training: 2 epochs, lr 2e-5, beta 0.1, effective batch 8, bf16, one A100. `provenance.json` records the base revision, the code
  commit, the hashes of the task split, spec, tasks and pair files, and the hyperparameters.
- Code, rules and full write-up: the Harel-Lexsi GitHub repository (`docs/project-report.md`, `docs/t8-result.md`).
- Run data: the dataset `{owner}/harel-shop-verifier-runs`.

Licence: CC-BY-NC-4.0, as the base model. Using it needs access to the gated base model.
"""

DATASET_CARD = """---
license: cc-by-nc-4.0
tags: [agents, verifier, trajectories, dpo, reward-hacking, harel]
---

# Harel: run data for verifier-gated fine-tuning of Tiny Aya

Outputs of the Harel project (Lexsi hackathon, Track 3). A small builder agent (`CohereLabs/tiny-aya-global`) writes backend
handlers for a Postgres shop; Harel, a model-free verifier, settles hidden contracts against the database changes each tool call
made and answers PASS, FAIL (with a failure class) or UNKNOWN.

| folder | what | n |
|---|---|---|
| `t2_baseline/` | the untrained agent on 24 tasks: per-task verdicts and visible-test results, AgentTune trajectories | 24 episodes |
| `t8/collection/` | every distinct answer sampled from the pinned weights, with the verifier's verdict | 842 answers from 270 episodes |
| `t8/pairs/` | DPO preference pairs (chosen = verifier PASS, rejected = FAIL, same prompt), two rules for "chosen" | 333 (strict) / 373 |
| `t8/evaluation/` | base vs trained on the held-out and patch tasks: per-episode rows, trajectories, summary, gate decision | 75 + 75 episodes |
| `a1_judge/` | an LLM judge (Command A) scoring the T2 trajectories | 24 |
| `a2_reward_hacking/` | verifier and LLM judge on 19 deliberately broken handlers and 6 correct ones | 25 |
| `v7_gate/` | the verifier's own gate on the 43 hidden contracts | 62 cases |
| `tasks/` | the 24-task set and its frozen train / held-out split | 24 |

Headline results, with their limits stated in the repository's `docs/`: the untrained agent passes its visible tests on 11 of 24
tasks and is verified on 4; DPO training raised visible-test passes but not verified success (12% to 12% held-out, gate: keep the
base model); on the broken handlers both the verifier and the judge approved 0 of 19 at the agreed line, but the judge approved only
2 of 6 correct handlers while the verifier passed 6 of 6. Small samples throughout (24 tasks, 8 held out).

The hidden contracts themselves are not included. Licence CC-BY-NC-4.0 (the trajectories contain the base model's outputs).
"""


def stage(tmp: Path, job_token: str) -> tuple[Path, Path, list[str]]:
    model, data, missing = tmp / "model", tmp / "dataset", []
    model.mkdir()
    data.mkdir()
    env = {**os.environ, "HF_TOKEN": job_token}
    hf = shutil.which("hf") or "hf"
    for name in ("adapter_config.json", "adapter_model.safetensors", "provenance.json"):
        r = subprocess.run([hf, "buckets", "cp", f"{BUCKET}/strict/adapter/{name}", str(model / name)], env=env,
                           capture_output=True, text=True)
        if r.returncode != 0 or not (model / name).exists():
            missing.append(f"bucket: strict/adapter/{name}")
    for src, dst in BUCKET_DATA:
        (data / dst).parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([hf, "buckets", "cp", f"{BUCKET}/{src}", str(data / dst)], env=env, capture_output=True, text=True)
        if r.returncode != 0 or not (data / dst).exists():
            missing.append(f"bucket: {src}")
    for src, dst in LOCAL_DATA:
        if (ROOT / src).is_file():
            (data / dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / src, data / dst)
        else:
            missing.append(f"repo: {src}")
    return model, data, missing


def leak_check(folder: Path) -> list[str]:
    """Nothing published may carry the hidden contracts or a token."""
    bad = []
    holdout = (ROOT / "spec" / "spec.md").read_text().split("## Holdout", 1)[1]
    contract_lines = [line.strip() for line in holdout.splitlines() if line.strip().startswith("- { id:")]
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".jsonl", ".md"):
            text = f.read_text(errors="ignore")
            if "## Holdout" in text or any(c in text for c in contract_lines[:60]):
                bad.append(f"{f.relative_to(folder)}: holdout text")
            for name in ("HF_TOKEN", "HF_JOB_TOKEN", "HF_INF_TOKEN", "HF_PUBLISH_TOKEN", "HF_SUBMISSION_TOKEN"):
                v = os.environ.get(name)
                if v and v in text:
                    bad.append(f"{f.relative_to(folder)}: contains {name}")
    return bad


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--owner", required=True, help="the Hugging Face user or org that will own the repos")
    p.add_argument("--private", action="store_true", help="create the repos private (the hackathon needs them public)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    load_dotenv()
    job_token = os.environ.get("HF_JOB_TOKEN")
    if not job_token:
        print("HF_JOB_TOKEN is not set (needed to read the adapter from the jobs bucket).")
        return 1
    with tempfile.TemporaryDirectory(prefix="hf-publish-") as t:
        model, data, missing = stage(Path(t), job_token)
        (model / "README.md").write_text(MODEL_CARD.replace("{owner}", a.owner))
        (data / "README.md").write_text(DATASET_CARD)
        bad = leak_check(model) + leak_check(data)
        size = lambda d: sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6  # noqa: E731
        print(f"model   {a.owner}/{MODEL_REPO}: {sorted(f.name for f in model.iterdir())} ({size(model):.1f} MB)")
        print(f"dataset {a.owner}/{DATASET_REPO}: {sum(1 for f in data.rglob('*') if f.is_file())} files ({size(data):.1f} MB)")
        for f in sorted(x.relative_to(data) for x in data.rglob("*") if x.is_file()):
            print(f"    {f}")
        if missing:
            print("MISSING (not uploaded): " + "; ".join(missing))
        if bad:
            print("REFUSING: " + "; ".join(bad))
            return 2
        if a.dry_run:
            print("dry run: nothing created or uploaded")
            return 0

        from huggingface_hub import HfApi

        token = os.environ.get("HF_SUBMISSION_TOKEN") or os.environ.get("HF_PUBLISH_TOKEN")
        if not token:
            print("HF_SUBMISSION_TOKEN is not set. Create a token on the owner account (see this file's docstring).")
            return 1
        api = HfApi(token=token)
        who = api.whoami()["name"]
        if who != a.owner and a.owner not in [o["name"] for o in api.whoami().get("orgs", [])]:
            print(f"the publishing token belongs to {who}, which cannot publish under {a.owner}.")
            return 1
        mid, did = f"{a.owner}/{MODEL_REPO}", f"{a.owner}/{DATASET_REPO}"
        api.create_repo(mid, repo_type="model", private=a.private, exist_ok=True)
        api.upload_folder(repo_id=mid, repo_type="model", folder_path=str(model), commit_message="Harel T8 DPO LoRA adapter")
        api.create_repo(did, repo_type="dataset", private=a.private, exist_ok=True)
        api.upload_folder(repo_id=did, repo_type="dataset", folder_path=str(data), commit_message="Harel run data")
        col = api.create_collection(COLLECTION, namespace=a.owner, private=a.private, exists_ok=True,
                                    description="A model-free verifier as the reward and the gate for fine-tuning a small coding agent.")
        for item, kind in ((mid, "model"), (did, "dataset")):
            api.add_collection_item(col.slug, item_id=item, item_type=kind, exists_ok=True)
        print(f"published:\n  https://huggingface.co/{mid}\n  https://huggingface.co/datasets/{did}\n  https://huggingface.co/collections/{col.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
