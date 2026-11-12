# Hugging Face + OpenRouter integration for the agents

Resolves [Issue #1 (B1): Confirm compute](https://github.com/Harel-Labs/Harel-Lexsi/issues/1). Status: **draft for team review**.

## 1. B1 decision

| Field | Value |
|---|---|
| Local GPU | None. |
| GPU for training | **Hugging Face compute (HF Jobs / HF GPU clusters)**, chosen over other rented GPUs. |
| GPU type / VRAM | _TBD: pick the flavor with `hf jobs hardware`. VRAM caps model size and `num_generations`._ |
| Hours / budget | _TBD_ |
| Where it runs | Training on HF GPUs; serving via HF Inference; baselines and governance agent via OpenRouter |
| Hackathon-provided compute | _Still check first (B1 criterion). Record here._ |

**Training order: keep the board's order, GRPO first (T3), DPO (T8) as fallback.** With HF GPUs, GRPO is possible, and Harel's verdict is the reward. The B5 smoke test (one stock AgentTune GRPO step on the HF GPU) decides it: if it passes and step time is acceptable, run T3; if reward is flat or steps are too slow, switch to T8 on verified (failure, repair) pairs.

**Fine-tuning method (LoRA vs full) is a memory choice, not a requirement.** The board uses LoRA only as the low-VRAM option (B2, T3, risk table). Pick it after the HF flavor is known: small GPU, use LoRA and merge into the base weights before serving; large GPU, full fine-tune the 3B model (Tiny Aya, see B2).

## 2. Roles

| Service | Used for | Not used for |
|---|---|---|
| **OpenRouter** | **Governance agent** (proposes factory changes, e.g. builder prompt, with expected effect); **"standard LLM judge" baseline** that Harel is compared against; optional frontier baseline builder | Verifier verdicts, anything in the holdout section |
| **HF Inference Providers** (router) or our own HF GPU/endpoint | Rollouts and A/B evaluation of the builder (Tiny Aya, base now, retrained later). The router may not serve Tiny Aya or our fine-tune, so plan on our own endpoint | Frontier baselines |
| **HF Hub** | Storing retrained models, datasets (`trajectories.jsonl`, verified failure/repair pairs) | n/a |
| **HF Jobs / GPU clusters** | AgentTune training (GRPO, DPO fallback) of the builder | Serving |
| **AuditKIT** | Before/after pass rate, mutant catch rate vs LLM judge, wasted tool calls | Not an LLM; no provider access |
| **Verifier (Harel)** | Pass/fail/unknown per step | **Stays model-free: no model client import.** |

## 3. Architecture

One small module, `llm_client`, wraps both providers because both expose an OpenAI-compatible `/chat/completions` API:

- HF router base URL: `https://router.huggingface.co/v1`, auth with `HF_TOKEN`. A provider can be pinned by suffixing the model id (e.g. `model:provider`). Docs: [Inference Providers](https://huggingface.co/docs/inference-providers), [function calling](https://huggingface.co/docs/inference-providers/guides/function-calling).
- OpenRouter base URL: `https://openrouter.ai/api/v1`, auth with `OPENROUTER_API_KEY`. Docs: [API reference](https://openrouter.ai/docs/api-reference/overview).
- Interface: `chat(role, messages, tools=None, **params) -> {message, tool_calls, usage, latency, provider, model}`.
- `role` (e.g. `builder`, `baseline`, `judge`) resolves to provider and model id via config, so swapping a model is a config change.
- Cross-cutting: timeout, retry with exponential backoff on 429/5xx, per-call logging of tokens, latency and cost into the episode record so AuditKIT can read it.
- Guard: a test that fails if the verifier package imports `llm_client` or any provider SDK.

Config sketch (`config.yaml`, no secrets):

```yaml
roles:
  builder:  {provider: hf,         model: "CohereLabs/tiny-aya-global"}   # pinned in B2
  baseline: {provider: openrouter, model: "<frontier model id>"}
  judge:    {provider: openrouter, model: "<judge model id>"}
```

Secrets: `HF_TOKEN`, `OPENROUTER_API_KEY` in environment or a gitignored `.env`. Never committed, never logged.

## 4. Data flow

1. Task from the shop environment goes to the **builder** (HF) or a **baseline** (OpenRouter).
2. Agent edits handlers; visible tests run; the episode is recorded.
3. The **verifier** scores each step against the holdout contracts (model-free).
4. Verdicts become rewards or preference pairs (chosen = verifier-pass attempt, rejected = fail).
5. **AgentTune training job** on HF GPUs retrains the builder (GRPO with the verdict as reward, or DPO on verified pairs); the result is pushed to the Hub (merged if LoRA was used).
6. DeploymentGate: serve the retrained model (see risk 1), rerun held-out tasks; deploy only if Harel-verified success rises with no regression.
7. **Governance loop:** the OpenRouter governance agent proposes a factory change with its expected effect; an A/B run scored by Harel decides whether it is kept.
8. **AuditKIT** `compare_models` compares retrained vs base builder with bootstrap significance, and Harel vs the OpenRouter LLM judge on deliberately broken versions.

## 5. Risks and open questions

1. **Serving the retrained model.** Serverless providers generally serve catalog models, not ours. Options: deploy the merged model to a dedicated Inference Endpoint, run a Space, or evaluate inside the HF training job. Needs a spike.
2. **Tool-calling support varies** by provider and model. Cohere models, including Tiny Aya, do not render `tools` in their chat template; AgentTune falls back to putting tool schemas in the system prompt (see B2). Check the `supports_tools` flag on the router's `/v1/models` if the router is used. OpenRouter can filter models by `supported_parameters=tools`.
3. **Credits and rate limits** on both services during a hackathon. Cache baseline runs; keep episode counts small.
4. **Cost reporting differs.** OpenRouter returns token usage; confirm whether a cost field is present before depending on it. HF cost must be estimated from tokens or billing.
5. **Licensing.** AgentTune and AuditKIT are noncommercial; keep the verifier behind an adapter (already in B5).
6. **HF flavor and budget still to pick.** HF Jobs bills per minute; run `hf jobs hardware` for current flavors and prices, and check the [pricing page](https://huggingface.co/docs/hub/jobs-pricing). A 1.5B model should fit on a single small/mid GPU, to be confirmed by the B5 smoke test.
7. **Data leakage.** Nothing from the holdout section may enter any prompt sent to either provider.

## 6. Ticket mapping

- **B1**: this doc, plus filling the table in §1.
- **B2**: pin builder model; confirm it is on the HF router with tool support.
- **B5**: scaffold; add `llm_client` and smoke tests.
- **T3 / T8**: GRPO first on HF GPU, DPO fallback.
- **Model:** the builder is `CohereLabs/tiny-aya-global` (hackathon requires a dense open-weights Cohere Labs model), pinned in B2. Qwen models are used only for local dry runs.
- Suggested new tickets: `llm_client` module; secrets and `.env` setup; LoRA serving spike; cost/latency logging into trajectories.

## 7. Acceptance checks

- [ ] Smoke call to the HF router returns a completion for the builder model, including one tool-call request.
- [ ] Smoke call to OpenRouter returns a completion and token usage.
- [ ] Both calls appear in the episode log with tokens and latency.
- [ ] Import-guard test passes (verifier never imports a model client).
- [ ] §1 table filled in: GPU, VRAM, hours, location.
