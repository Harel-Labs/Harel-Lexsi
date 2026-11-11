"""B5 smoke test: one tool-using GRPO step, tiny model, then read trajectories.jsonl with AuditKIT.

Local dry run on Mac (MPS) or CPU: no vLLM. Not a quality run; it only proves the pipeline works.
    .venv/bin/python scripts/grpo_smoke.py [--model Qwen/Qwen2.5-0.5B-Instruct] [--out runs/smoke]
"""
import argparse
import pathlib

from datasets import Dataset

from agenttune.core.backend_factory import create_agentic_trainer

STOCK = {"apple": 3, "pear": 0, "plum": 7}


def check_stock(item: str) -> dict:
    """Look up how many units of an item are in stock.

    Args:
        item: The item name, e.g. apple.

    Returns:
        A dict with the item and its stock count.
    """
    return {"item": item, "stock": STOCK.get(item, 0)}


def stock_reward(completions, answer=None, **kwargs):
    """1.0 if the final answer contains the right number, else 0.0."""
    out = []
    for comp, gold in zip(completions, answer, strict=False):
        text = comp[-1]["content"] if isinstance(comp, list) and comp else str(comp)
        out.append(1.0 if str(gold) in str(text) else 0.0)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--out", default="runs/smoke")
    a = p.parse_args()

    items = list(STOCK)
    ds = Dataset.from_dict(
        {
            "prompt": [
                [{"role": "user", "content": f"Use the tool to check how many {i} are in stock, then reply with the number."}]
                for i in items * 2
            ],
            "answer": [STOCK[i] for i in items * 2],
        }
    )
    trainer = create_agentic_trainer(
        algorithm="grpo",
        model=a.model,
        train_dataset=ds,
        tools=[check_stock],
        reward_funcs=[stock_reward],
        output_dir=a.out,
        max_steps=1,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=1,
        num_generations=2,
        max_completion_length=128,
        learning_rate=1e-6,
        use_vllm=False,
        report_to="none",
        log_completions=True,
    )
    results = trainer.train()
    print("TRAIN RESULT:", results)

    from auditkit.agent_eval import episodes_from_agenttune

    traj = pathlib.Path(a.out) / "trajectories.jsonl"
    eps = episodes_from_agenttune(str(traj))
    print(f"AUDITKIT: read {len(eps)} episodes from {traj}")


if __name__ == "__main__":
    main()
