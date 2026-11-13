import ast
from pathlib import Path

import pytest

from harel_lexsi.adapters.module_merge import NotPython, defined_names, merge_module

BASE = '''"""Cart handlers."""
import json

from app.errors import ShopError

LIMIT = 5


def one(conn):
    """stub"""
    raise NotImplementedError


def two(conn):
    # keep this comment
    raise NotImplementedError
'''


def test_replaces_only_the_named_function_in_place():
    out = merge_module(BASE, "def two(conn):\n    return 2\n")
    assert defined_names(out) == ["LIMIT", "one", "two"]
    assert "return 2" in out and "raise NotImplementedError" in out  # `one` is still the stub
    assert out.index("def one") < out.index("def two")
    assert "keep this comment" not in out  # it was inside the replaced function
    assert out.startswith('"""Cart handlers."""')


def test_other_handlers_survive_a_write_that_names_one():
    out = merge_module(BASE, "def one(conn):\n    return 1\n")
    assert "def two" in out and "# keep this comment" in out


def test_new_names_are_appended():
    out = merge_module(BASE, "def helper(x):\n    return x\n")
    assert defined_names(out) == ["LIMIT", "one", "two", "helper"]


def test_missing_imports_are_added_once_after_the_import_block():
    new = "import json\nimport re\nfrom app.errors import ShopError\n\ndef one(conn):\n    return re\n"
    out = merge_module(BASE, new)
    assert out.count("import json") == 1 and out.count("ShopError\n") >= 1
    assert out.index("import re") < out.index("LIMIT = 5")
    assert out.count("import re") == 1
    ast.parse(out)


def test_writing_the_whole_module_back_changes_nothing_important():
    assert defined_names(merge_module(BASE, BASE)) == defined_names(BASE)
    assert merge_module(BASE, BASE).strip() == BASE.strip()


def test_decorated_function_is_replaced_whole():
    base = "import functools\n\n@functools.cache\ndef f():\n    return 1\n\ndef g():\n    return 2\n"
    out = merge_module(base, "def f():\n    return 10\n")
    assert "functools.cache" not in out and "return 10" in out and "def g" in out
    ast.parse(out)


def test_assignment_replaced():
    assert "LIMIT = 9" in merge_module(BASE, "LIMIT = 9\n") and "LIMIT = 5" not in merge_module(BASE, "LIMIT = 9\n")


def test_unparseable_text_is_refused_with_a_line_number():
    with pytest.raises(NotPython, match=r"line 1"):
        merge_module(BASE, "def f(a=1, b):\n    pass\n")


def test_merge_into_the_real_cart_stub_keeps_all_six_handlers():
    stub = (Path(__file__).resolve().parents[1] / "shop" / "app" / "handlers" / "cart.py").read_text()
    before = [n for n in defined_names(stub) if not n.startswith("_")]
    out = merge_module(stub, "def update_cart_item(conn, user_id, product_id, qty):\n    return {'ok': True}\n")
    assert set(before) <= set(defined_names(out))
    assert "{'ok': True}" in out
    ast.parse(out)
