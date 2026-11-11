# B2: L1 model choice

Resolves [Issue #2 (B2)](https://github.com/Harel-Labs/Harel-Lexsi/issues/2). Depends on B1 ([docs/hf-openrouter-integration.md](hf-openrouter-integration.md)).

## Decision

**Pin `Qwen/Qwen3.5-4B` as the L1 builder model.** Config:

```yaml
roles:
  builder:
    provider: hf
    model: "Qwen/Qwen3.5-4B"
    enable_thinking: false      # see "Thinking mode" below
```

Training uses **LoRA** (language model layers only). GRPO runs on Hugging Face GPUs (B1); local machines are for dry runs only.

## Reason

- **Fits local dev.** A 4B model trains with LoRA on a 24 GB Apple M4 (estimated ~10-12 GB bf16, less with 4-bit), so the pipeline, data format and verifier hooks can be tested without spending HF GPU hours.
- **Cheaper GRPO on HF.** Smaller model means more `num_generations` and longer episodes per GPU hour. The HF flavor can be smaller than for 9B.
- **Same family can scale up.** Qwen3.5 also ships a 9B. Same tokenizer and tool-call format, so moving up is a config change if the 4B baseline is too weak (see B2 trigger below).
- **Apache-2.0**, native tool calling, 262K context.
- **Alternatives considered:** Qwen3.5-9B (heavier, bf16 LoRA ~22 GB, no local dev); Gemma 4B (second tool-call format and license, unverified in AgentTune); Qwen2.5-1.5B-Instruct (named in the B2 ticket, kept as last resort).

## Deviations from the B2 ticket

- Criterion 1 says to start from a model AgentTune's examples already run (Qwen3-1.7B or Qwen2.5-1.5B-Instruct). Qwen3.5-4B is **not confirmed** to be one of them. The B5 smoke test (one stock GRPO step on the HF GPU) is the check.
- Criterion 2 (small coder model only if B1 allows): not used. A general model with tool calling is the simpler choice.

## Things to handle

1. **Thinking mode.** The model thinks by default; non-thinking needs `enable_thinking: false` in the request. Long thinking traces inflate rollout cost and reward noise. Start non-thinking; revisit only if baseline verified success (T2) is near zero.
2. **Multimodal architecture.** The model card lists it as image-text-to-text. Apply LoRA to the language model only and verify the trainer loads it as a text model.
3. **vLLM.** Needs a recent (main/nightly) vLLM, with `--enable-auto-tool-choice --tool-call-parser qwen3_coder`. Reports say GRPO via vLLM fast-inference may not work for Qwen3.5 yet in some trainers; if so, rollouts are slower and the T8 (DPO) fallback matters more.
4. **HF router availability.** Only Featherless AI is listed as a provider on the model page. Confirm `supports_tools` on the router's `/v1/models`; if absent, serve from our own endpoint or fall back to OpenRouter for baseline rollouts.

## LoRA config (starting point, tune after B5)

| Setting | Start at |
|---|---|
| Rank / alpha | 16 / 32 |
| Target modules | attention and MLP projections of the language model |
| Dropout | 0.05 |
| Precision | bf16 on HF GPU; 4-bit QLoRA for local dry runs |
| Merge | merge into base weights before serving or DeploymentGate |

## Escalation

Move to Qwen3.5-9B only if T2 baseline verified success is near zero and narrowing tasks does not help. Needs a 40-80 GB HF flavor.

## Done when

- [ ] Model id pinned in config (above)
- [ ] B5 smoke test: Qwen3.5-4B completes one GRPO step on the HF GPU; if not, fall back to Qwen2.5-1.5B-Instruct and record why here
