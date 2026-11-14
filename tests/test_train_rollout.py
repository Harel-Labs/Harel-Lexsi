"""Training-side wiring: dataset prompts, per-episode reset, block writes. No model, no GPU.

The reset and extractor tests need Postgres (the sandbox migrates it), like the other harness tests.
"""
import pytest

from harel_lexsi.adapters import train_rollout as T
from harel_lexsi.adapters.builder_prompt import system_prompt
from harel_lexsi.adapters.shop_harness import ShopSandbox, task_for

TASKS = T.load_tasks()
BY_ID = {t["id"]: t for t in TASKS}


def test_task_for_accepts_the_task_prompt_or_a_longer_one():
    t = BY_ID["patch-add_to_cart"]
    assert task_for(t["prompt"])["id"] == "patch-add_to_cart"
    assert task_for(t["prompt"] + "\n\nextra text")["id"] == "patch-add_to_cart"
    assert task_for("something else entirely") is None


def test_every_training_prompt_maps_back_to_its_own_task():
    for t in TASKS:
        assert task_for(T.training_prompt(t))["id"] == t["id"]


def test_prompt_has_the_task_the_tests_the_tables_and_the_current_code():
    p = T.training_prompt(BY_ID["implement-update_cart_item"])
    assert p.startswith(BY_ID["implement-update_cart_item"]["prompt"])
    assert "def test_update_cart_item" in p and "carts(id, user_id, status, coupon_code)" in p
    assert "raise NotImplementedError" in p  # the stub


def test_patch_prompt_shows_the_start_file_code_not_the_stub():
    p = T.training_prompt(BY_ID["patch-add_to_cart"])
    assert "The function as it is now" in p and "raise NotImplementedError" not in p.split("The function as it is now")[1]


def test_prompts_use_only_builder_visible_text():
    holdout_ids = ("C-01", "closure:", "contracts:")
    for t in TASKS:
        p = T.training_prompt(t) + system_prompt()
        assert not [h for h in holdout_ids if h in p]


def test_dataset_rows_carry_the_handler_for_the_reward():
    rows = T.training_rows(TASKS[:3], repeat=2)
    assert len(rows["prompt"]) == len(rows["handler"]) == 6
    assert rows["handler"][0] == rows["handler"][1] == TASKS[0]["handler"]


# ---------- with Postgres ----------

@pytest.fixture
def sandbox():
    sb = ShopSandbox()
    yield sb
    sb.close()


def test_reset_hook_prepares_the_episode_for_the_prompts_task(sandbox):
    hook = T.EpisodeReset(sandbox)
    t = BY_ID["patch-add_to_cart"]
    hook(0, [{"role": "system", "content": "s"}, {"role": "user", "content": T.training_prompt(t)}])
    assert sandbox.handler == "add_to_cart" and hook.episodes == 1
    assert "NotImplementedError" not in (sandbox.workspace / "app/handlers/cart.py").read_text().split("def add_to_cart")[1][:200]
    hook(1, [])  # later turns do nothing
    assert hook.episodes == 1


def test_block_writes_turn_a_python_block_into_a_write_and_stop_on_green(sandbox):
    from agenttune.agentic.rollout_engines import rollout_factory as rf
    original = rf._extract_tool_calls
    undo = T.install_block_writes(sandbox)
    try:
        sandbox.reset(handler="clear_cart")
        calls = rf._extract_tool_calls({"role": "assistant", "content": ""}, "```python\ndef clear_cart(conn, user_id):\n    return {}\n```")
        assert calls and calls[0]["function"]["name"] == "write_file" or calls[0].get("name") == "write_file"
        assert rf._extract_tool_calls({"role": "assistant", "content": "ok"}, "ok") in (None, [])
        sandbox.last_tests, sandbox.writes = {"passed": 1, "failed": 0, "failures": []}, 1
        assert sandbox.green
        assert rf._extract_tool_calls({"role": "assistant", "content": ""}, "```python\ndef clear_cart(conn, user_id):\n    return 1\n```") == []
    finally:
        undo()
    assert rf._extract_tool_calls is original
