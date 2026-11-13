"""S6: the shop harness. Needs Postgres (cd shop && docker compose up -d)."""
import os
from pathlib import Path

import psycopg
import pytest

from harel_lexsi.adapters.shop_harness import ShopHarness, make_tools

CART_IMPL = (Path(__file__).parent / "fixtures" / "cart_impl.py").read_text()
DSN = os.environ.get("SHOP_DSN", "postgresql://shop:shop@localhost:5433/shop")


def call(h, name, **args):
    return h.step({"name": name, "arguments": args})


@pytest.fixture
def harness():
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")
    h = ShopHarness(max_steps=20)
    h.reset("Implement the cart handlers.")
    yield h
    h.close()


def test_manual_episode_end_to_end(harness):
    obs, _, done, _ = call(harness, "read_file", path="app/handlers/cart.py")
    assert "NotImplementedError" in obs.text and not done

    obs, reward, _, _ = call(harness, "write_file", path="app/handlers/cart.py", content=CART_IMPL)
    assert reward == 0.0 and "error" not in obs.text

    obs, _, _, _ = call(harness, "run_visible_tests")
    result = eval(obs.text) if obs.text.startswith("{") else {}
    assert result["passed"] == 8  # the 8 cart tests; the other handlers are still stubs

    obs, _, done, info = call(harness, "finish", answer="cart handlers done")
    assert done and obs.text == "cart handlers done" and info["steps"] == 4
    names = [e.payload["action"]["name"] for e in harness.to_eventlog().events
             if "action" in e.payload]
    assert names == ["read_file", "write_file", "run_visible_tests", "finish"]


@pytest.mark.parametrize("path", [
    "../reference/handlers/cart.py", "reference/handlers/cart.py", "holdout.yaml",
    "spec/spec.md", "/etc/passwd", "app/../../shop/reference/handlers/cart.py", "tools/db.py",
])
def test_reads_outside_app_or_to_hidden_things_are_refused(harness, path):
    obs, reward, _, _ = call(harness, "read_file", path=path)
    assert obs.text.startswith("error:") and reward == -1.0


@pytest.mark.parametrize("path", [
    "app/schema.sql", "app/errors.py", "app/tests/visible/test_visible.py",
    "reference/handlers/cart.py", "../x.py", "app/handlers/sub/x.py", "app/handlers/x.txt",
])
def test_writes_outside_handlers_are_refused(harness, path):
    obs, reward, _, _ = call(harness, "write_file", path=path, content="x")
    assert obs.text.startswith("error:") and reward == -1.0


def test_symlinks_are_refused(harness):
    ws = harness.sandbox.workspace
    (ws / "app" / "handlers" / "link.py").symlink_to(Path("/etc/passwd"))
    obs, _, _, _ = call(harness, "read_file", path="app/handlers/link.py")
    assert obs.text.startswith("error:")


def test_workspace_has_no_reference_or_spec(harness):
    ws = harness.sandbox.workspace
    assert not (ws / "reference").exists() and not (ws / "spec").exists()
    assert not list(ws.rglob("holdout*"))


def test_reset_gives_fresh_stubs_and_database(harness):
    call(harness, "write_file", path="app/handlers/cart.py", content=CART_IMPL)
    harness.reset("second episode")
    obs, _, _, _ = call(harness, "read_file", path="app/handlers/cart.py")
    assert "NotImplementedError" in obs.text
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT count(*) FROM carts").fetchone()[0] == 0
        assert c.execute("SELECT on_hand FROM inventory WHERE product_id = 1").fetchone()[0] == 50


def test_every_tool_call_emits_start_and_end_markers(harness):
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute("SELECT pg_create_logical_replication_slot('s6_test', 'test_decoding')")
    try:
        harness.reset("marker episode")  # reset wipes data, so create the slot first, then reset again
        call(harness, "read_file", path="app/handlers/cart.py")
        call(harness, "run_visible_tests")
        call(harness, "finish", answer="x")
        rows = admin.execute(
            "SELECT data FROM pg_logical_slot_get_changes('s6_test', NULL, NULL, "
            "'include-xids', '0', 'skip-empty-xacts', '1')").fetchall()
    finally:
        admin.execute("SELECT pg_drop_replication_slot('s6_test')")
        admin.close()
    marks = [r[0] for r in rows if "harel.step" in r[0]]
    seen = [(m.split('"tool": "')[1].split('"')[0], m.split('"phase": "')[1].split('"')[0]) for m in marks]
    for tool in ("read_file", "run_visible_tests", "finish"):
        assert (tool, "start") in seen and (tool, "end") in seen


def test_tools_are_plain_documented_functions(harness):
    tools = make_tools(harness.sandbox)
    assert set(tools) == {"read_file", "write_file", "run_visible_tests", "finish"}
    assert all(t.__doc__ for t in tools.values())


def _task(task_id):
    import json

    root = Path(__file__).parents[1] / "tasks"
    return next(json.loads(l) for l in (root / "tasks.jsonl").read_text().splitlines()
                if json.loads(l)["id"] == task_id), root


def test_patch_task_starts_from_its_start_file(harness):
    task, root = _task("patch-remove_coupon")
    harness.reset(task["prompt"])
    obs, _, _, _ = call(harness, "read_file", path=task["target"])
    assert obs.text == (root / task["start"]).read_text()
    assert "checked_out" in obs.text  # the planted bug is what the agent starts from


def test_implement_task_starts_from_the_stub(harness):
    task, _ = _task("implement-remove_coupon")
    harness.reset(task["prompt"])
    obs, _, _, _ = call(harness, "read_file", path=task["target"])
    assert "NotImplementedError" in obs.text


def test_edited_start_file_is_refused(tmp_path):
    import json

    from harel_lexsi.adapters.shop_harness import SandboxError, patch_start_for

    task, _ = _task("patch-remove_coupon")
    (tmp_path / "starts").mkdir()
    (tmp_path / task["start"]).write_text("tampered")
    (tmp_path / "tasks.jsonl").write_text(json.dumps(task) + "\n")
    with pytest.raises(SandboxError):
        patch_start_for(task["prompt"], tmp_path)


def test_tool_schemas_expose_real_parameters(tmp_path):
    """The model's tool schema comes from the signature; a *args/**kwargs wrapper hides path/content."""
    import inspect

    harness = ShopHarness()
    tools = make_tools(harness.sandbox)
    assert list(inspect.signature(tools["write_file"]).parameters) == ["path", "content"]
    assert list(inspect.signature(tools["read_file"]).parameters) == ["path"]
