# B2: L1 model choice

Resolves [Issue #2 (B2)](https://github.com/Harel-Labs/Harel-Lexsi/issues/2). Depends on B1 ([docs/hf-openrouter-integration.md](hf-openrouter-integration.md)).

**Revision:** the first pin was `Qwen/Qwen3.5-4B`. The hackathon requires the fine-tuned builder to be a dense, open-weights model from Cohere Labs ("e.g., Tiny Aya 3B or Aya Expanse 8B", stated in the problem description, the FAQ and the requirements), so the pin is now Tiny Aya. Qwen models stay in use only as local dry-run models (see `scripts/grpo_smoke.py`) and, if wanted, as OpenRouter baselines. They are never the submitted model.

## Decision

**Pin `CohereLabs/tiny-aya-global` as the L1 builder model** (checked 2026-11-13, revision `af89d219b53ed9b13b8a4645f9c8028973510324`; gating is automatic once the licence is accepted). Config:

```yaml
roles:
  builder:
    provider: hf
    model: "CohereLabs/tiny-aya-global"
```

Training uses **LoRA** (AgentTune's own Tiny Aya example uses `r=16, lora_alpha=32, target_modules="all-linear"`). GRPO runs on Hugging Face GPUs (B1); local machines are for dry runs only.

## Reason

- **Meets the requirement:** dense, open-weights, from Cohere Labs.
- **AgentTune already runs it.** Its README has a Tiny Aya tool-calling example (`create_agentic_trainer(model="CohereLabs/tiny-aya-global", tools=[...], peft_config=...)`) and says it runs on CPU or a Mac without vLLM. That satisfies B2's first criterion (a model AgentTune's examples already run), which Qwen3.5-4B did not.
- **Small (3.35B).** LoRA fits a small HF GPU, and a local dry run on the M4 is plausible.
- **Alternatives considered:** Aya Expanse 8B (same fallback tool handling, bigger GPU; the escalation path); Tiny Aya Fire/Earth/Water (regional variants, no reason to prefer them for English code tasks); Qwen models (not allowed as the submitted model).

## Deviations from the B2 ticket

- Criterion 1 named Qwen3-1.7B or Qwen2.5-1.5B-Instruct. Those are replaced by a Cohere model, per the hackathon requirement. Tiny Aya is in AgentTune's examples, so the intent of the criterion holds.
- Criterion 2 (small coder model only if B1 allows): not used. No Cohere coder model is in scope.

## Things to handle

1. **No native tool-call rendering.** Tiny Aya's chat template has only a `default` template and renders a `tool` turn as empty. AgentTune detects this and falls back (with a warning): it puts the tool schemas in the system prompt, asks for a `[{"tool_name": ..., "parameters": {...}}]` list, and returns tool results as user turns. Consequences: (a) check on a real rollout that `write_file` calls reach `trajectories.jsonl` normalized as `{name, arguments}`; the T1 reward (`final_files`) relies on that; (b) a 3B model may emit malformed tool calls, so the baseline (T2) may be low and task narrowing may be needed.
2. **Gated model.** Accept the licence on the model page and run `hf auth login` (or set `HF_TOKEN`) wherever it is downloaded, including HF Jobs.
3. **8K context** (8K in, 8K out). Episodes that read and rewrite handler files must stay short. Keep tool outputs trimmed and tasks to one handler.
4. **License: CC-BY-NC-4.0** plus Cohere's Acceptable Use Policy, so non-commercial. This matches AgentTune and AuditKIT being non-commercial.
5. **Serving.** The card lists Cohere as the inference provider, and we cannot assume the HF serverless router serves it or serves a fine-tune. Plan to serve the base and fine-tuned model from our own HF GPU or endpoint, and keep OpenRouter for baselines and the governance agent only. Check `/v1/models` for tool support if the router is used.
6. **vLLM.** Not needed for AgentTune's Tiny Aya path (it runs without vLLM). GRPO rollouts will be slower than with vLLM; the T8 (DPO) fallback matters more.

## LoRA config (starting point, tune after B5)

| Setting | Start at |
|---|---|
| Rank / alpha | 16 / 32 (AgentTune's example) |
| Target modules | `all-linear` (AgentTune's example) |
| Precision | bf16 on HF GPU; 4-bit or bf16 for local dry runs |
| Merge | merge into base weights before serving or DeploymentGate |

## Escalation

Move to Aya Expanse 8B only if the T2 baseline verified success is near zero and narrowing tasks does not help. Needs a larger HF flavor; confirm size before booking.

## Done when

- [ ] Model id pinned in config (above)
- [ ] B5 smoke test: Tiny Aya completes one GRPO step with the shop tools on the HF GPU, and AuditKIT reads that run's `trajectories.jsonl`; if not, record why here and fall back to Aya Expanse 8B
