"""S4: the visible tests are shallow. They pass on correct handlers AND on trap variants that break
a hidden contract, while the effect-level checks catch the traps."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SHOP = Path(__file__).resolve().parent.parent
TRAPS = ["reference.traps.reserve_on_add", "reference.traps.checkout_no_event"]


def run_visible(handlers: str) -> int:
    env = {**os.environ, "SHOP_HANDLERS": handlers}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "app/tests/visible", "-q", "-p", "no:cacheprovider"],
        cwd=SHOP, env=env, capture_output=True,
    ).returncode


def test_visible_passes_on_reference():
    assert run_visible("reference.handlers") == 0


def test_visible_fails_on_unimplemented_stubs():
    assert run_visible("app.handlers") != 0


@pytest.mark.parametrize("trap", TRAPS)
def test_visible_passes_on_trap(trap):
    assert run_visible(trap) == 0


# ---------- the effect checks the visible tests do not make ----------

def test_trap_reserve_on_add_breaks_stock_contract(conn):
    t = importlib.import_module("reference.traps.reserve_on_add")
    t.add_to_cart(conn, 1, 1, 5)
    on_hand = conn.execute("SELECT on_hand FROM inventory WHERE product_id = 1").fetchone()["on_hand"]
    assert on_hand != 50  # a correct add_to_cart leaves stock at 50


def test_trap_checkout_no_event_breaks_outbox_contract(conn):
    t = importlib.import_module("reference.traps.checkout_no_event")
    t.add_to_cart(conn, 1, 1, 1)
    t.checkout(conn, 1)
    events = [r["event"] for r in conn.execute("SELECT event FROM outbox").fetchall()]
    assert "order.placed" not in events  # a correct checkout publishes order.placed
