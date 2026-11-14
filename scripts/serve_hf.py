"""A small OpenAI-compatible server for Tiny Aya that batches concurrent requests. For HF Jobs; not for a laptop.

Why: one request through Hugging Face `generate` runs at about 30 tokens/s, but a batch of 4 takes about the same
wall time (probe, A100: 115 tokens/s), because small-model decoding is limited by per-token overhead, not by
the GPU. Running many episodes in parallel against one batching server therefore multiplies throughput, and
because the episodes talk to it through the same OpenAI-style API as the hosted endpoint, they run exactly
the T2 baseline's code path (adapters/hosted_engine.py).

    python scripts/serve_hf.py --port 8000 [--adapter DIR] [--max-batch 16] [--revision af89d219...]

POST /v1/chat/completions  {messages, max_tokens, temperature}  ->  OpenAI-style response (no streaming, no tools)
GET  /health               ->  {"ok": true, "served": N, "batches": M, "mean_batch": X}

Only the Python standard library for HTTP; torch/transformers/peft for the model. Requests that arrive within
`--wait-ms` of each other are generated together (left-padded). A batch that runs out of memory is split in half
and retried, so a burst of long prompts slows down instead of failing.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "CohereLabs/tiny-aya-global"
REVISION = "af89d219b53ed9b13b8a4645f9c8028973510324"


def load(model_id: str, revision: str, adapter: str | None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id, revision=revision, padding_side="left")
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_id, revision=revision, dtype=torch.bfloat16).to("cuda").eval()
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter).merge_and_unload().eval()  # merged: no LoRA overhead
        print(f"[serve] merged adapter {adapter}", flush=True)
    return tok, model


class Batcher:
    def __init__(self, tok, model, max_batch: int, wait_s: float):
        self.tok, self.model, self.max_batch, self.wait_s = tok, model, max_batch, wait_s
        self.q: queue.Queue = queue.Queue()
        self.served = self.batches = self.seqs = 0
        threading.Thread(target=self.loop, daemon=True).start()

    def submit(self, messages: list[dict], max_tokens: int, temperature: float) -> dict:
        item = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "done": threading.Event(), "result": None}
        self.q.put(item)
        item["done"].wait()
        return item["result"]

    def loop(self) -> None:
        while True:
            batch = [self.q.get()]
            deadline = time.time() + self.wait_s
            while len(batch) < self.max_batch and (left := deadline - time.time()) > 0:
                try:
                    batch.append(self.q.get(timeout=left))
                except queue.Empty:
                    break
            self.generate(batch)

    def generate(self, batch: list[dict]) -> None:
        import torch

        try:
            results = self._run(batch)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if len(batch) == 1:
                results = [{"error": "out of memory on a single request"}]
            else:  # split and retry: slower, not failed
                half = len(batch) // 2
                self.generate(batch[:half])
                self.generate(batch[half:])
                return
        except Exception as e:  # noqa: BLE001
            results = [{"error": f"{type(e).__name__}: {e}"[:300]}] * len(batch)
        for item, res in zip(batch, results, strict=True):
            item["result"] = res
            item["done"].set()
        self.served += len(batch)
        self.batches += 1
        self.seqs += len(batch)

    def _run(self, batch: list[dict]) -> list[dict]:
        import torch

        tok = self.tok
        texts = [tok.apply_chat_template(b["messages"], add_generation_prompt=True, tokenize=False) for b in batch]
        enc = tok(texts, return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")  # template has the BOS
        n_prompt = enc["input_ids"].shape[1]
        temp = max(b["temperature"] for b in batch)
        sample = temp > 0
        with torch.inference_mode():
            out = self.model.generate(
                **enc, max_new_tokens=max(b["max_tokens"] for b in batch), do_sample=sample,
                temperature=temp if sample else None, top_p=1.0 if sample else None, top_k=0 if sample else None,
                pad_token_id=tok.pad_token_id)
        eos = {tok.eos_token_id, *(self.model.generation_config.eos_token_id if isinstance(
            self.model.generation_config.eos_token_id, list) else [self.model.generation_config.eos_token_id])} - {None}
        results = []
        for i, b in enumerate(batch):
            ids = out[i, n_prompt:].tolist()
            stop = next((j for j, t in enumerate(ids) if t in eos), None)
            finish = "stop" if stop is not None else "length"
            ids = ids[:stop] if stop is not None else ids
            if len(ids) > b["max_tokens"]:
                ids, finish = ids[:b["max_tokens"]], "length"
            prompt_tokens = int((enc["attention_mask"][i]).sum().item())
            results.append({"text": tok.decode(ids, skip_special_tokens=True), "finish": finish,
                            "prompt_tokens": prompt_tokens, "completion_tokens": len(ids)})
        return results


def make_handler(batcher: Batcher, name: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/health"):
                mean = batcher.seqs / batcher.batches if batcher.batches else 0
                self._send(200, {"ok": True, "served": batcher.served, "batches": batcher.batches,
                                 "mean_batch": round(mean, 2)})
            elif self.path.startswith("/v1/models"):
                self._send(200, {"object": "list", "data": [{"id": name, "object": "model"}]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.startswith("/v1/chat/completions"):
                return self._send(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                res = batcher.submit(req["messages"], int(req.get("max_tokens") or 512),
                                     float(req.get("temperature") if req.get("temperature") is not None else 0.7))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": {"message": f"{type(e).__name__}: {e}"[:300]}})
            if "error" in res:
                return self._send(500, {"error": {"message": res["error"]}})
            self._send(200, {
                "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
                "model": name, "choices": [{"index": 0, "finish_reason": res["finish"],
                                            "message": {"role": "assistant", "content": res["text"]}}],
                "usage": {"prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"],
                          "total_tokens": res["prompt_tokens"] + res["completion_tokens"]}})
    return Handler


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=MODEL)
    p.add_argument("--revision", default=REVISION)
    p.add_argument("--adapter", default=None, help="a LoRA adapter folder to merge into the weights")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--max-batch", type=int, default=16)
    p.add_argument("--wait-ms", type=float, default=40.0)
    a = p.parse_args()
    t = time.time()
    tok, model = load(a.model, a.revision, a.adapter)
    print(f"[serve] model ready in {time.time() - t:.0f}s on port {a.port}, max batch {a.max_batch}", flush=True)
    batcher = Batcher(tok, model, a.max_batch, a.wait_ms / 1000)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(batcher, a.model)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
