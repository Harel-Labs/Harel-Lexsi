"""T8 step 0: how fast can Tiny Aya generate inside a GPU Job, and does vLLM serve it?

Runs inside an HF Job on a GPU (scripts/hf_job.py run scripts/t8_probe.py --gpu --yes). No Postgres, no training.

Two measurements on a real builder prompt (system prompt + a task's first turn, about 2.5k tokens):
  1. Hugging Face `generate`, plain weights in eval mode: one request, then a batch of 4.
  2. vLLM's OpenAI-compatible server, if it can load the architecture (`Cohere2ForCausalLM`): one request,
     then 8 at once. vLLM is installed into its own virtualenv so a failed install cannot break the job.

The result decides how the T8 evaluation generates (see docs/t8-plan.md). It is printed and written to
$OUT_DIR/probe.json.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODEL = "CohereLabs/tiny-aya-global"
REVISION = "af89d219b53ed9b13b8a4645f9c8028973510324"
NEW_TOKENS = 384
PORT = 8000


def builder_messages() -> list[dict]:
    from harel_lexsi.adapters.builder_prompt import system_prompt
    from harel_lexsi.adapters.train_rollout import load_tasks, training_prompt

    task = next(t for t in load_tasks() if t["id"] == "implement-apply_coupon")
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": training_prompt(task)}]


def probe_hf(messages: list[dict]) -> dict:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
    model = AutoModelForCausalLM.from_pretrained(MODEL, revision=REVISION, dtype=torch.bfloat16).to("cuda").eval()
    enc = tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True).to("cuda")
    n_prompt = enc["input_ids"].shape[1]
    out = {"prompt_tokens": n_prompt}
    with torch.inference_mode():
        model.generate(**enc, max_new_tokens=8, do_sample=False)  # warm-up
        for label, batch in (("single", 1), ("batch4", 4)):
            ids = enc["input_ids"].repeat(batch, 1)
            mask = enc["attention_mask"].repeat(batch, 1)
            torch.cuda.synchronize()
            t = time.time()
            gen = model.generate(input_ids=ids, attention_mask=mask, max_new_tokens=NEW_TOKENS, min_new_tokens=NEW_TOKENS,
                                 do_sample=True, temperature=0.9)
            torch.cuda.synchronize()
            dt = time.time() - t
            new = (gen.shape[1] - n_prompt) * batch
            out[label] = {"seconds": round(dt, 1), "new_tokens": new, "tokens_per_s": round(new / dt, 1)}
            print(f"[probe] HF generate {label}: {new} tokens in {dt:.1f}s = {new / dt:.1f} tok/s", flush=True)
    del model
    torch.cuda.empty_cache()
    return out


def _chat(messages: list[dict]) -> int:
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": NEW_TOKENS, "temperature": 0.9}).encode()
    req = urllib.request.Request(f"http://localhost:{PORT}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["usage"]["completion_tokens"]


def probe_vllm(messages: list[dict]) -> dict:
    uv = shutil.which("uv")
    venv = Path("/tmp/vllm-env")
    log = Path("/tmp/vllm.log")
    try:
        subprocess.run([uv, "venv", str(venv), "--python", "3.12"], check=True, capture_output=True, text=True)
        t = time.time()
        r = subprocess.run([uv, "pip", "install", "--python", str(venv / "bin" / "python"), "vllm"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return {"ok": False, "stage": "install", "error": r.stderr[-800:]}
        ver = subprocess.run([str(venv / "bin" / "python"), "-c", "import vllm, torch; print(vllm.__version__, torch.__version__)"],
                             capture_output=True, text=True).stdout.strip()
        print(f"[probe] vLLM installed in {time.time() - t:.0f}s: {ver}", flush=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "install", "error": str(e)[:800]}

    cmd = [str(venv / "bin" / "vllm"), "serve", MODEL, "--revision", REVISION, "--port", str(PORT), "--dtype", "bfloat16",
           "--max-model-len", "8192", "--gpu-memory-utilization", "0.85"]
    server = subprocess.Popen(cmd, stdout=log.open("w"), stderr=subprocess.STDOUT, env={**os.environ})
    try:
        t = time.time()
        while time.time() - t < 600:
            if server.poll() is not None:
                return {"ok": False, "stage": "load", "version": ver, "error": log.read_text()[-1500:]}
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=3)
                break
            except Exception:  # noqa: BLE001
                time.sleep(5)
        else:
            return {"ok": False, "stage": "load-timeout", "version": ver, "error": log.read_text()[-1500:]}
        out = {"ok": True, "version": ver, "load_seconds": round(time.time() - t)}
        print(f"[probe] vLLM server up in {out['load_seconds']}s", flush=True)
        _chat(messages[:1] + [{"role": "user", "content": "Say ok."}])  # warm-up
        t = time.time()
        n = _chat(messages)
        dt = time.time() - t
        out["single"] = {"seconds": round(dt, 1), "new_tokens": n, "tokens_per_s": round(n / dt, 1)}
        print(f"[probe] vLLM single: {n} tokens in {dt:.1f}s = {n / dt:.1f} tok/s", flush=True)
        t = time.time()
        with cf.ThreadPoolExecutor(8) as ex:
            total = sum(ex.map(_chat, [messages] * 8))
        dt = time.time() - t
        out["parallel8"] = {"seconds": round(dt, 1), "new_tokens": total, "tokens_per_s": round(total / dt, 1)}
        print(f"[probe] vLLM 8 at once: {total} tokens in {dt:.1f}s = {total / dt:.1f} tok/s", flush=True)
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "request", "version": ver, "error": f"{e} | {log.read_text()[-1000:]}"}
    finally:
        server.terminate()


def main() -> int:
    import torch
    if not torch.cuda.is_available():
        print("[probe] ABORT: no CUDA", flush=True)
        return 3
    result = {"gpu": torch.cuda.get_device_name(0), "model": MODEL, "revision": REVISION, "new_tokens": NEW_TOKENS}
    messages = builder_messages()
    try:
        result["hf_generate"] = probe_hf(messages)
    except Exception as e:  # noqa: BLE001
        result["hf_generate"] = {"error": str(e)[:500]}
        print(f"[probe] HF generate failed: {e}", flush=True)
    result["vllm"] = probe_vllm(messages)
    if not result["vllm"].get("ok"):
        print(f"[probe] vLLM not usable at stage {result['vllm'].get('stage')}: {result['vllm'].get('error', '')[-600:]}", flush=True)
    out = Path(os.environ.get("OUT_DIR", "/tmp")) / "probe.json"
    out.write_text(json.dumps(result, indent=2))
    print("[probe] RESULT " + json.dumps(result)[:1500], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
