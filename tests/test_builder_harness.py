"""The pieces that shape what a small builder sees: system prompt, code-block writes, repair requests.

Pure functions only: no model, no database.
"""
import json
from pathlib import Path

from harel_lexsi.adapters.builder_prompt import (
    TOOLS_FALLBACK,
    schema_digest,
    shared_rules,
    system_prompt,
)
from harel_lexsi.adapters.episode_coach import function_source
from harel_lexsi.adapters.episode_coach import tests_text as coach_tests_text
from harel_lexsi.adapters.hosted_engine import code_block_as_write, flatten_turn
from harel_lexsi.adapters.shop_harness import visible_tests_for

ROOT = Path(__file__).resolve().parents[1]
PATH = "app/handlers/cart.py"
TESTS = (ROOT / "shop" / "app" / "tests" / "visible" / "test_visible.py").read_text()


# ---------- system prompt ----------

def test_system_prompt_uses_only_builder_visible_text():
    spec = (ROOT / "spec" / "spec.md").read_text()
    prompt = system_prompt()
    holdout = spec[spec.index("## Holdout"):]
    assert "All handlers share these rules" in prompt and "order.placed" in prompt
    assert "## Holdout" not in prompt and "contracts:" not in prompt
    for line in holdout.splitlines():  # no contract line leaks in
        if line.strip().startswith("- { id:"):
            assert line.strip() not in prompt


def test_shared_rules_stop_at_the_build_section():
    text = "## Build\n### What you are building\nrule A\n### Cart\ntable\n### Events\nevent B\n## Holdout\nsecret\n"
    assert shared_rules(text) == "rule A\n\nevent B"


def test_schema_digest_lists_every_table_with_its_columns():
    digest = schema_digest((ROOT / "shop" / "app" / "schema.sql").read_text())
    assert "carts(id, user_id, status, coupon_code)" in digest
    assert "cart_items(cart_id, product_id, qty)" in digest
    assert len(digest.splitlines()) == 10


def test_fallback_prompt_formats_with_tool_schemas():
    assert "write_file" in TOOLS_FALLBACK.format(tools='[{"name": "write_file"}]')


# ---------- a python block is a write ----------

def test_python_block_becomes_a_write_call():
    call = json.loads(code_block_as_write("Here:\n```python\ndef f(conn):\n    return {}\n```\nthen junk", PATH))
    assert call == [{"tool_name": "write_file", "parameters": {"path": PATH, "content": "def f(conn):\n    return {}\n"}}]


def test_broken_or_cut_off_block_is_still_a_write_so_the_harness_can_refuse_it():
    assert code_block_as_write('```python\ndef f(conn):\n    return "{"a": %s}"\n```', PATH)
    assert code_block_as_write("```python\ndef f(conn):\n    with conn.transaction():\n        x = (", PATH)


def test_replies_without_a_function_are_left_alone():
    assert code_block_as_write("done", PATH) is None
    assert code_block_as_write("```python\nx = 1\n```", PATH) is None
    assert code_block_as_write('[{"tool_name": "finish", "parameters": {}}]', PATH) is None
    assert code_block_as_write("```python\ndef f():\n    pass\n```", None) is None


def test_history_shows_a_lone_write_as_the_block_the_model_wrote():
    turn = {"role": "assistant", "content": "ignored", "tool_calls": [
        {"function": {"name": "write_file", "arguments": json.dumps({"path": PATH, "content": "def f():\n    pass\n"})}}]}
    assert flatten_turn(turn) == {"role": "assistant", "content": "```python\ndef f():\n    pass\n```"}
    other = {"role": "assistant", "tool_calls": [{"name": "read_file", "arguments": {"path": PATH}}]}
    assert json.loads(flatten_turn(other)["content"]) == [{"tool_name": "read_file", "parameters": {"path": PATH}}]
    assert flatten_turn({"role": "user", "content": "hi"}) == {"role": "user", "content": "hi"}


# ---------- what the coach shows ----------

def test_visible_tests_for_picks_the_tests_that_call_the_handler():
    assert visible_tests_for("update_cart_item", TESTS) == ["test_update_cart_item", "test_update_cart_item_not_in_cart"]
    assert "test_checkout_totals" in visible_tests_for("checkout", TESTS)
    assert visible_tests_for("no_such_handler", TESTS) == []


def test_tests_text_puts_the_handlers_own_tests_first_and_respects_the_limit():
    text = coach_tests_text("add_to_cart", TESTS, limit=900)
    assert text.index("def test_add_to_cart_returns_quantity") < len(text)
    assert "Seed data" in text and "def test_checkout_totals" not in text
    assert "def test_remove_coupon" in coach_tests_text("remove_coupon", TESTS)


def test_function_source_returns_one_function_with_its_decorators():
    module = "import x\n\n@dec\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
    assert function_source(module, "a") == "@dec\ndef a():\n    return 1"
    assert function_source(module, "missing") is None
    assert function_source("def (", "a") is None
