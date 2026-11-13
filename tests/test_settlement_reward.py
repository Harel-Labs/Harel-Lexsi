"""T1: the settlement reward. The pure parts need no database; the scorer tests need Postgres."""
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from harel_lexsi.adapters.rewards import settlement_reward
from harel_lexsi.adapters.settlement_reward import (
    final_files,
    harel_settlement_reward,
    make_harel_settlement_reward,
)
from harel_lexsi.verifier.score import score_files
from harel_lexsi.verifier.types import Settlement, Verdict, Violation

PASS = Settlement("C-1", Verdict.PASS, "ok")
UNKNOWN = Settlement("C-2", Verdict.UNKNOWN, "no markers")


def fail(*steps):
    return Settlement("C-3", Verdict.FAIL, "x", tuple(Violation("closure_breach", s) for s in steps))


# ---------- the numbers ----------

def test_pass_unknown_fail_are_plus_one_zero_negative():
    assert settlement_reward([PASS]) == 1.0
    assert settlement_reward([UNKNOWN]) == 0.0
    assert settlement_reward([fail(1)]) == -1.0 < 0


def test_without_a_fail_reward_is_the_pass_fraction_and_unknown_adds_nothing():
    assert settlement_reward([PASS, PASS]) == 1.0
    assert settlement_reward([PASS, UNKNOWN]) == 0.5
    assert settlement_reward([PASS, PASS, PASS, UNKNOWN]) == 0.75


def test_any_fail_is_negative_whatever_else_passes():
    for n_pass in range(10):
        assert settlement_reward([PASS] * n_pass + [fail(1)]) < 0
    assert settlement_reward([PASS, fail(1)]) == pytest.approx(-0.75)
    assert settlement_reward([PASS, UNKNOWN, fail(1)], fail_reward=-0.5) == pytest.approx(-0.5 * (1 - 1 / 6))


def test_more_passes_hurt_less_among_failing_attempts():
    assert settlement_reward([fail(1)]) < settlement_reward([PASS, fail(1)]) < settlement_reward([PASS] * 5 + [fail(1)])


def test_unknown_is_never_positive_and_never_beats_a_pass():
    assert settlement_reward([UNKNOWN, UNKNOWN]) == 0.0 < settlement_reward([PASS])


def test_optional_step_penalty_counts_distinct_failing_steps():
    assert settlement_reward([fail(2, 2, 3)], step_penalty=0.1) == pytest.approx(-1.2)
    assert settlement_reward([fail(2, 2, 3)]) == -1.0           # off by default
    assert settlement_reward([PASS], step_penalty=0.1) == 1.0   # passes pay no penalty


def test_empty_means_nothing_proved():
    assert settlement_reward([]) == 0.0


def test_bad_parameters_are_refused():
    with pytest.raises(ValueError):
        settlement_reward([PASS], fail_reward=0.0)
    with pytest.raises(ValueError):
        settlement_reward([PASS], step_penalty=-0.1)


def test_deterministic_for_the_same_settlements():
    s = [PASS, fail(1, 4), UNKNOWN]
    assert len({settlement_reward(s, step_penalty=0.05) for _ in range(20)}) == 1


# ---------- from a trajectory to files ----------

def step(name, **arguments):
    return {"action": {"name": name, "arguments": arguments}}


def test_final_files_replays_write_file_calls_later_wins():
    t = {"steps": [step("read_file", path="app/handlers/cart.py"),
                   step("write_file", path="app/handlers/cart.py", content="MARK = 1\n"),
                   step("write_file", path="app/handlers/cart.py", content="MARK = 2\n"),
                   step("finish", answer="done")]}
    files = final_files(t)
    assert list(files) == ["app/handlers/cart.py"]
    assert "MARK = 2" in files["app/handlers/cart.py"] and "MARK = 1" not in files["app/handlers/cart.py"]


def test_final_files_accepts_objects_and_json_string_arguments():
    obj = SimpleNamespace(steps=[SimpleNamespace(action={"name": "write_file", "arguments":
                                                         '{"path": "app/handlers/cart.py", "content": "X = 1\\n"}'})])
    assert "X = 1" in final_files(obj)["app/handlers/cart.py"]


def test_final_files_skips_refused_and_malformed_writes():
    t = {"steps": [step("write_file", path="app/schema.sql", content="x"),
                   step("write_file", path="../evil.py", content="x"),
                   step("write_file", path="reference/handlers/cart.py", content="x"),
                   step("write_file", path="app/handlers/cart.py"),            # no content
                   step("write_file", args="garbage", kwargs={}),              # a small model's output
                   step("write_file", path="app/handlers/ok.py", content="ok")]}
    assert list(final_files(t)) == ["app/handlers/ok.py"]


# ---------- the AgentTune-facing function (fake scorer) ----------

def test_reward_fn_scores_each_completion_from_its_own_trajectory():
    seen = []

    def scorer(files, handler):
        seen.append((dict(files), handler))
        return [PASS] if "good" in files.get("app/handlers/cart.py", "") else [fail(1)]

    fn = make_harel_settlement_reward(scorer=scorer)
    trajs = [{"steps": [step("write_file", path="app/handlers/cart.py", content="good")]},
             {"steps": [step("write_file", path="app/handlers/cart.py", content="bad")]}]
    assert fn(["a", "b"], handler=["add_to_cart", "checkout"], trajectories=trajs) == [1.0, -1.0]
    assert [h for _, h in seen] == ["add_to_cart", "checkout"]
    assert fn.__name__ == "harel_settlement_reward"


def test_reward_fn_without_trajectory_or_handler_is_zero_not_positive():
    fn = make_harel_settlement_reward(scorer=lambda f, h: pytest.fail("should not score"))
    assert fn(["a"], handler=["add_to_cart"], trajectories=None) == [0.0]
    assert fn(["a"], handler=[None], trajectories=[{"steps": []}]) == [0.0]


def test_reward_fn_scores_identical_trajectories_once_and_identically():
    calls = []
    fn = make_harel_settlement_reward(scorer=lambda f, h: calls.append(1) or [fail(1)])
    t = {"steps": [step("write_file", path="app/handlers/cart.py", content="x")]}
    assert fn(["a", "b"], handler="add_to_cart", trajectories=[t, t]) == [-1.0, -1.0]
    assert len(calls) == 1


# ---------- the real scorer (needs Postgres) ----------

@pytest.fixture
def needs_db():
    try:
        psycopg.connect("postgresql://shop:shop@localhost:5433/shop", connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")


CART_OK = (Path(__file__).parent / "fixtures" / "cart_impl.py").read_text()


def reward_for(files, handler="add_to_cart", **kw):
    fn = make_harel_settlement_reward(timeout=kw.pop("timeout", 60), **kw)
    t = {"steps": [step("write_file", path=p, content=c) for p, c in files.items()]}
    return fn(["c"], handler=[handler], trajectories=[t])[0]


def test_a_correct_handler_is_rewarded_and_a_stub_is_punished(needs_db):
    assert reward_for({"app/handlers/cart.py": CART_OK}) == 1.0
    assert reward_for({}) == -1.0  # untouched stub raises NotImplementedError


def test_a_trap_that_passes_visible_tests_is_punished(needs_db):
    trap = CART_OK.replace(
        'return {"cart_id": cart["id"], "product_id": product_id, "qty": total}',
        'conn.execute("UPDATE inventory SET on_hand = on_hand - %s WHERE product_id = %s", (qty, product_id))\n'
        '        return {"cart_id": cart["id"], "product_id": product_id, "qty": total}', 1)
    assert trap != CART_OK and reward_for({"app/handlers/cart.py": trap}) < 0


def test_same_trajectory_gives_the_same_reward_every_time(needs_db):
    assert len({reward_for({"app/handlers/cart.py": CART_OK}) for _ in range(3)}) == 1


def test_code_that_hangs_is_unknown_so_zero_never_positive(needs_db):
    hang = CART_OK.replace("def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:\n",
                           "def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:\n"
                           "    while True:\n        pass\n", 1)
    assert hang != CART_OK
    assert reward_for({"app/handlers/cart.py": hang}, timeout=8) == 0.0


def test_unknown_handler_with_no_contracts_is_zero(needs_db):
    assert reward_for({}, handler="no_such_handler") == 0.0


def test_scorer_never_exposes_reference_code_to_the_agent_files(needs_db):
    src = "import reference\n" + CART_OK  # the scorer's child has only the workspace on its path
    assert reward_for({"app/handlers/cart.py": src}) <= 0.0


def test_default_instance_is_the_named_reward():
    assert harel_settlement_reward.__name__ == "harel_settlement_reward"
    assert callable(score_files)


def test_final_files_reads_agenttune_multi_call_steps():
    """Real Tiny Aya rollouts record `action={"tool_calls": [...]}`, several calls per step."""
    traj = {"steps": [{"action": {"tool_calls": [
        {"id": "a", "name": "read_file", "arguments": {"path": "app/handlers/cart.py"}},
        {"id": "b", "name": "write_file", "arguments": {"path": "app/handlers/cart.py", "content": "X = 1\n"}},
    ]}}, {"action": {}}]}
    assert "X = 1" in final_files(traj)["app/handlers/cart.py"]


def test_final_files_reads_in_memory_openai_style_calls():
    """In memory (training time) calls are `{"function": {"name", "arguments": "<json>"}}`."""
    import json as _json
    args = _json.dumps({"path": "app/handlers/cart.py", "content": "Y = 2\n"})
    traj = {"steps": [{"action": {"tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "write_file", "arguments": args}},
        {"id": "b", "type": "function", "function": {"name": "write_file", "arguments": "{not json"}},
    ]}}]}
    assert "Y = 2" in final_files(traj)["app/handlers/cart.py"]


# ---------- writes are merged into the module ----------

def test_a_write_naming_one_handler_keeps_the_others():
    t = {"steps": [step("write_file", path="app/handlers/cart.py",
                        content="def update_cart_item(conn, user_id, product_id, qty):\n    return {'ok': True}\n")]}
    text = final_files(t)["app/handlers/cart.py"]
    for name in ("add_to_cart", "remove_from_cart", "clear_cart", "update_cart_item"):
        assert f"def {name}" in text
    assert "{'ok': True}" in text


def test_a_write_that_is_not_python_is_skipped_like_the_harness_refuses_it():
    t = {"steps": [step("write_file", path="app/handlers/cart.py", content="def f(a=1, b):\n    pass\n")]}
    assert final_files(t) == {}


def test_patch_task_writes_merge_into_the_start_file_not_the_stub():
    import json
    from pathlib import Path
    tasks = Path(__file__).resolve().parents[1] / "tasks"
    task = next(json.loads(line) for line in (tasks / "tasks.jsonl").read_text().splitlines()
                if json.loads(line)["id"] == "patch-add_to_cart")
    start = (tasks / task["start"]).read_text()
    t = {"task": task["prompt"], "steps": [step("write_file", path=task["target"],
                                                  content="def add_to_cart(conn, user_id, product_id, qty):\n    return {'fixed': 1}\n")]}
    text = final_files(t)[task["target"]]
    assert "{'fixed': 1}" in text
    # the other handlers still have the start file's implementation, not the stub's NotImplementedError
    other = next(line for line in start.splitlines() if line.startswith("def ") and "add_to_cart" not in line)
    assert other in text
