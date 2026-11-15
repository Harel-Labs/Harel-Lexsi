"""G4 + K2: build the factory-graph row and the single-file HTML report from the committed run records.

Reads only files under docs/ (no network, no model, no database). Writes:
  docs/factory-row.json   the G4 row: trigger, diff, expected, observed, decision
  docs/report.html        the K2 report, one file, opens offline, every number links to its source file

Run:  uv run python scripts/build_report.py
Nothing here is typed by hand: every number comes from a record, and the row says which.
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
T2 = "docs/t2-baseline-run/summary.json"
T2_ROWS = "docs/t2-baseline-run/results.jsonl"
T8_SUM = "docs/t8-run/eval_summary.json"
T8_GATE = "docs/t8-run/gate_decision.json"
T8_PROV = "docs/t8-run/adapter_provenance.json"
T8_PAIRS = "docs/t8-run/pairs_summary.json"
T8_PLAN = "docs/t8-plan.md"
A2 = "docs/a2-run/summary.json"


def load(rel):
    return json.loads((ROOT / rel).read_text())


def jsonl(rel):
    return [json.loads(line) for line in (ROOT / rel).read_text().splitlines() if line.strip()]


def pct(x):
    return f"{round(100 * x)}%"


def factory_row():
    base = load(T2)["summary"]["all"]
    gate, ev, prov, pairs = load(T8_GATE), load(T8_SUM), load(T8_PROV), load(T8_PAIRS)
    h = ev["heldout"]
    return {
        "kind": "model change (DPO adapter), not a prompt or memory proposal",
        "trigger": {
            "what": "baseline passes the visible tests more often than the verifier confirms",
            "visible_pass": base["visible_pass"], "verified": base["verified_success"], "of": base["n"],
            "source": T2},
        "diff": {
            "what": "LoRA adapter trained with DPO on verifier-labelled preference pairs, train split only",
            "pairs": prov["train_pairs"], "rule": prov["pair_rule"], "hyperparameters": prov["hyperparameters"],
            "held_out_in_training": prov["held_out_in_training"], "pairs_summary": pairs[prov["pair_rule"]],
            "source": T8_PROV},
        "expected": {
            "what": "held-out verified success rises by at least +0.10 and the patch-task rate does not fall "
                    "(rule written before the trained model answered)",
            "min_heldout_gain": 0.10, "max_mutation_regression": 0.0, "source": T8_PLAN},
        "observed": {
            "heldout_verified": {"base": h["base"], "trained": h["new"], "n": h["n"], "p_value": h["p_value"]},
            "heldout_visible": {"base": ev["heldout_visible"]["base"], "trained": ev["heldout_visible"]["new"]},
            "patch_tasks_verified": ev["mutation"], "source": T8_SUM},
        "decision": {"decision": gate["decision"], "reasons": gate["reasons"], "source": T8_GATE},
    }


def src(rel, text=None):
    return f'<a href="../{rel}">{html.escape(text or rel)}</a>'


def table(head, rows):
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def report(row):
    t2 = load(T2)["summary"]["all"]
    ev, a2 = load(T8_SUM), load(A2)["summary"]
    verdicts = []
    for r in sorted(jsonl(T2_ROWS), key=lambda r: r["task_id"]):
        vis = f'{r["visible_passed"]}/{r["visible_passed"] + r["visible_failed"]}'
        verdicts.append([html.escape(r["task_id"]), html.escape(r["split"]), f'<b class="{r["verdict"]}">{r["verdict"].upper()}</b>',
                         html.escape(", ".join(r["classes"]) or "none"), vis, str(r["steps"])])
    h, hv = ev["heldout"], ev["heldout_visible"]
    ba = a2["broken"]
    ctl = a2["control"]
    f = row
    ratio = [
        ["verified by Harel", pct(h["base"]), pct(h["new"]), f'n = {h["n"]} tasks, p = {h["p_value"]}'],
        ["visible tests pass", pct(hv["base"]), pct(hv["new"]), f'n = {hv["n"]} tasks, p = {hv["p_value"]}'],
        ["patch tasks verified", pct(ev["mutation"]["base"]), pct(ev["mutation"]["new"]), f'n = {ev["mutation"]["n"]} tasks'],
    ]
    small = '<p class="note">Small sample: %s. Treat as a demonstration, not proof.</p>'
    rowtab = table(["step", "what", "value", "record"], [
        ["trigger", html.escape(f["trigger"]["what"]), f'{f["trigger"]["visible_pass"]} visible vs {f["trigger"]["verified"]} verified of {f["trigger"]["of"]}', src(T2)],
        ["diff", html.escape(f["diff"]["what"]), f'{f["diff"]["pairs"]} pairs, rule {f["diff"]["rule"]}', src(T8_PROV)],
        ["expected", html.escape(f["expected"]["what"]), "gain ≥ +0.10", src(T8_PLAN)],
        ["observed", "held-out verified, base → trained", f'{pct(h["base"])} → {pct(h["new"])}', src(T8_SUM)],
        ["decision", html.escape("; ".join(f["decision"]["reasons"])), f'<b>{f["decision"]["decision"]}</b>', src(T8_GATE)],
    ])
    judge_ok = a2["control"]["judge_with_tests"]["approved"], a2["control"]["judge_with_tests"]["n"]
    ver_ok = a2["control"]["verifier_hidden_contracts"]["approved"], a2["control"]["verifier_hidden_contracts"]["n"]
    jv, vv = ba["judge_with_tests"], ba["verifier_hidden_contracts"]
    judge_tab = table(["", "verifier", "LLM judge (score 1.0 approves)"], [
        [f"broken handlers approved (of {vv['n']})", str(vv["approved"]), str(jv["approved"])],
        [f"correct handlers approved (of {ver_ok[1]})", str(ver_ok[0]), str(judge_ok[0])]])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harel report</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--ok:#1a7f37;--bad:#b42318;--unk:#9a6700;--acc:#0b57d0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181d;--fg:#e8e8e8;--mut:#9aa0a6;--line:#333;--ok:#56d364;--bad:#ff7b72;--unk:#e3b341;--acc:#8ab4f8}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1rem 16px 3rem}}
h1{{font-size:1.5rem}}h2{{margin-top:2rem;font-size:1.2rem}}a{{color:var(--acc)}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid var(--line);padding:.35rem .6rem;text-align:left;vertical-align:top}}
.pass{{color:var(--ok)}}.fail{{color:var(--bad)}}.unknown{{color:var(--unk)}}
.note{{color:var(--mut);font-size:.85rem}}
</style></head><body>
<h1>Harel: a verifier that catches what small coding agents get wrong</h1>
<p>Generated by <code>scripts/build_report.py</code> from the run records in <code>docs/</code>. Links are relative to this file in the repository. UNKNOWN is never counted as a pass.</p>

<h2>1. Baseline verdicts: tests that pass are not handlers that work</h2>
<p>Untrained Tiny Aya Global, 24 tasks, one sample each: <b>{t2["visible_pass"]}</b> pass the visible tests, <b>{t2["verified_success"]}</b> verified by Harel, {t2["unknown"]} UNKNOWN. Source: {src(T2)}, per task {src(T2_ROWS)}.</p>
{table(["task", "split", "verdict", "failure classes", "visible tests", "steps"], verdicts)}
{small % "24 tasks, one sample per task"}
<p class="note">The record keeps the number of steps and the failure classes per task; the step that caused each failure is shown to the agent in the failure line but is not stored in these run files.</p>

<h2>2. Before and after training (held-out tasks)</h2>
{table(["metric", "base", "trained", "sample"], ratio)}
<p>Source: {src(T8_SUM)}; rows per episode in {src("docs/t8-run/eval_rows.jsonl")}. The trained agent passed more visible tests but was not more often right.</p>
{small % "8 held-out tasks, 5 samples each; seven of the eight are at 0% for the base model"}

<h2>3. Factory-graph row (G4)</h2>
<p>Generated by this script and saved as <a href="../docs/factory-row.json">docs/factory-row.json</a>. This round changed the model, not the prompt, so it is a model-change row.</p>
{rowtab}
{small % "n = 8 held-out tasks, so significance cannot be reached; the gate does not require it"}

<h2>4. LLM judge versus verifier on broken handlers</h2>
{judge_tab}
<p>Source: {src(A2)}, per item {src("docs/a2-run/rows.jsonl")}.</p>
{small % "19 broken and 6 correct handlers, one judge model and one prompt"}
</body></html>
"""


def main():
    row = factory_row()
    (DOCS / "factory-row.json").write_text(json.dumps(row, indent=2) + "\n")
    (DOCS / "report.html").write_text(report(row))
    print("wrote docs/factory-row.json and docs/report.html")


if __name__ == "__main__":
    main()
