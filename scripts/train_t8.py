"""T8 step 4: train a LoRA adapter with DPO on the verifier-labelled preference pairs. Runs in an HF Job on a GPU.

    hf_job.py run scripts/train_t8.py --gpu --yes -- --run collect-20261114-164908 --rule strict

Reads  $OUT_DIR/t8/<run>/<rule>/pairs.jsonl and pairs_eval.jsonl   (scripts/t8_pairs.py wrote them)
Writes $OUT_DIR/t8/<run>/<rule>/adapter/   the LoRA adapter, and provenance.json beside it

The data: chosen = an answer the verifier PASSES, rejected = one it FAILS, for the same prompt. The prompt is the exact pair
of messages the model sees at inference (system message with the tool text, then the first user turn), so DPO trains on
what it is measured on. Only TRAIN-task pairs exist in the files (checked again here against the frozen split).

LoRA r=16, alpha=32, all-linear; the reference model is the same weights with the adapter off (no second copy in memory).
A pair that would not fit the context window is dropped and counted, never truncated: cutting the end of an answer would
train on a different answer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODEL = "CohereLabs/tiny-aya-global"
REVISION = "af89d219b53ed9b13b8a4645f9c8028973510324"
SPEC = ROOT / "spec" / "spec.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="the collection folder under $OUT_DIR/t8")
    p.add_argument("--rule", choices=["verifier", "strict"], default="strict")
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--beta", type=float, default=0.1)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--max-length", type=int, default=6144, help="tokens, prompt plus answer; longer pairs are dropped")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--name", default="adapter")
    a = p.parse_args()

    import torch
    if os.environ.get("ACCELERATOR") and not torch.cuda.is_available():
        print(f"[t8] ABORT: asked for {os.environ['ACCELERATOR']} but PyTorch sees no CUDA", flush=True)
        return 3
    from agenttune.core.backend_factory import create_agentic_trainer
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from harel_lexsi.adapters import pairs as P

    base = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "t8" / a.run / a.rule
    held = set(json.loads((ROOT / "tasks" / "split.json").read_text())["heldout"])
    train_rows = P.read_jsonl(base / "pairs.jsonl")
    eval_rows = P.read_jsonl(base / "pairs_eval.jsonl")
    P.assert_train_only(train_rows + eval_rows, held)  # the held-out tasks must not be in the training data
    print(f"[t8] {a.run}/{a.rule}: {len(train_rows)} train pairs, {len(eval_rows)} eval pairs; tasks "
          f"{len({r['task_id'] for r in train_rows})}; model {MODEL}@{REVISION[:8]} on "
          f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no GPU'}", flush=True)

    tok = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)

    def tokens(messages: list[dict], generation_prompt: bool = False) -> int:
        return len(tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=generation_prompt,
                                           return_dict=True)["input_ids"])

    def fits(r: dict) -> bool:
        return max(tokens(r["prompt"] + r["chosen"]), tokens(r["prompt"] + r["rejected"])) <= a.max_length

    kept_train = [r for r in train_rows if fits(r)]
    kept_eval = [r for r in eval_rows if fits(r)]
    longest = max((max(tokens(r["prompt"] + r["chosen"]), tokens(r["prompt"] + r["rejected"])) for r in kept_train), default=0)
    print(f"[t8] dropped {len(train_rows) - len(kept_train)} train and {len(eval_rows) - len(kept_eval)} eval pairs over "
          f"{a.max_length} tokens; longest kept {longest}", flush=True)

    # The check that matters most: the chat template must split a conversation at the same point the server does, so the
    # tokens DPO scores as "the answer" are the tokens the model produces after the generation prompt at inference.
    r0 = kept_train[0]
    prompt_text = tok.apply_chat_template(r0["prompt"], tokenize=False, add_generation_prompt=True)
    full_text = tok.apply_chat_template(r0["prompt"] + r0["chosen"], tokenize=False)
    assert full_text.startswith(prompt_text), "the chat template changes the prompt when an answer follows it"
    print(f"[t8] template check ok. prompt ends: {prompt_text[-90:]!r}", flush=True)
    print(f"[t8] answer renders as:  {full_text[len(prompt_text):][:90]!r} ... {full_text[-40:]!r}", flush=True)

    cols = ("prompt", "chosen", "rejected")  # only these: extra columns would reach the collator
    train_ds = Dataset.from_list([{k: r[k] for k in cols} for r in kept_train]).shuffle(seed=a.seed)
    eval_ds = Dataset.from_list([{k: r[k] for k in cols} for r in kept_eval]) if kept_eval else None

    model = AutoModelForCausalLM.from_pretrained(MODEL, revision=REVISION, dtype=torch.bfloat16)
    out = base / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    trainer = create_agentic_trainer(
        algorithm="dpo", model=model, processing_class=tok, train_dataset=train_ds, eval_dataset=eval_ds,
        use_rollouts=False, output_dir=str(out / "checkpoints"), num_train_epochs=a.epochs, learning_rate=a.lr,
        beta=a.beta, per_device_train_batch_size=a.batch_size, per_device_eval_batch_size=1,
        gradient_accumulation_steps=a.grad_accum, max_length=a.max_length, seed=a.seed, logging_steps=1,
        save_strategy="no", eval_strategy="epoch" if eval_ds is not None else "no", bf16=True, gradient_checkpointing=True,
        report_to="none", warmup_steps=5,
        peft_config={"r": 16, "lora_alpha": 32, "target_modules": "all-linear", "task_type": "CAUSAL_LM"})
    print(f"[t8] trainer built in {time.time() - t0:.0f}s: epochs {a.epochs}, lr {a.lr}, beta {a.beta}, micro-batch "
          f"{a.batch_size} x accumulation {a.grad_accum}", flush=True)
    t1 = time.time()
    result = trainer.train()
    wall = time.time() - t1
    inner = getattr(trainer, "trainer", trainer)
    history = list(getattr(getattr(inner, "state", None), "log_history", []))
    for h in history:
        if "loss" in h or "eval_loss" in h:
            keep = {k: round(v, 4) if isinstance(v, float) else v for k, v in h.items()
                    if k in ("epoch", "loss", "eval_loss", "rewards/accuracies", "rewards/margins", "eval_rewards/accuracies",
                             "eval_rewards/margins", "grad_norm", "learning_rate")}
            print(f"[t8] log {keep}", flush=True)
    inner.save_model(str(out))
    print(f"[t8] DONE in {wall:.0f}s; adapter saved to {out}", flush=True)

    prov = {"purpose": "T8 DPO adapter", "base_model": MODEL, "base_revision": REVISION, "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "split_sha256": sha256(ROOT / "tasks" / "split.json"), "spec_sha256": sha256(SPEC),
            "tasks_sha256": sha256(ROOT / "tasks" / "tasks.jsonl"), "collection_run": a.run, "pair_rule": a.rule,
            "pairs_train_sha256": sha256(base / "pairs.jsonl"), "pairs_eval_sha256": sha256(base / "pairs_eval.jsonl"),
            "train_pairs": len(kept_train), "eval_pairs": len(kept_eval), "train_tasks": sorted({r["task_id"] for r in kept_train}),
            "held_out_in_training": False, "hyperparameters": {"epochs": a.epochs, "lr": a.lr, "beta": a.beta,
                                                               "micro_batch": a.batch_size, "grad_accum": a.grad_accum, "max_length": a.max_length,
                                                               "seed": a.seed, "lora": {"r": 16, "alpha": 32, "targets": "all-linear"}},
            "train_seconds": round(wall), "final_log": history[-1] if history else None, "result": str(result)[:300]}
    (out / "provenance.json").write_text(json.dumps(prov, indent=2, default=str))
    print("[t8] provenance written", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
