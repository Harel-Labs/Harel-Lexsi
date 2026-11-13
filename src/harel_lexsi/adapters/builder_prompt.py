"""The builder's system prompt: what a small model must know before it writes a handler.

A 3B builder plans its whole episode in the first turn (read, write, test, finish in one batch), so it
writes before it has read anything and invents a web framework. The prompt therefore carries, up
front, everything it would otherwise have to read: the shared rules, the house style, the schema.

Only builder-visible material is used: the `## Build` section of the spec (never `## Holdout`), the
conventions from the handler modules' own docstring, and app/schema.sql. The example handler is not
one of the tasks. The same prompt must be used for the baseline and for training.
"""
from __future__ import annotations

import re
from pathlib import Path

from harel_lexsi.spec.split import split_spec
from harel_lexsi.verifier.replay import ROOT, SHOP

SPEC = ROOT / "spec" / "spec.md"
SCHEMA = SHOP / "app" / "schema.sql"
SHARED = ("What you are building", "Events")  # Build subsections every task needs
DEFAULT_TEMPERATURE = 0.9  # rollouts sample; at 0 a small model resubmits the same failing code forever

EXAMPLE = '''def set_stock(conn, product_id: int, on_hand: int) -> dict:
    if not isinstance(on_hand, int) or on_hand < 0:
        raise ShopError("invalid_qty")
    with conn.transaction():
        row = conn.execute(
            "SELECT on_hand FROM inventory WHERE product_id = %s FOR UPDATE", (product_id,)
        ).fetchone()
        if row is None:
            raise ShopError("not_found")
        conn.execute("UPDATE inventory SET on_hand = %s WHERE product_id = %s", (on_hand, product_id))
    return {"product_id": product_id, "on_hand": on_hand}


def add_coupon(conn, code: str, percent_off: int) -> dict:
    if not isinstance(percent_off, int) or not 1 <= percent_off <= 100:
        raise ShopError("invalid_percent")
    with conn.transaction():
        existing = conn.execute("SELECT code FROM coupons WHERE code = %s", (code,)).fetchone()
        if existing is not None:
            raise ShopError("already_exists")
        row = conn.execute(
            "INSERT INTO coupons (code, percent_off) VALUES (%s, %s) RETURNING code, percent_off",
            (code, percent_off),
        ).fetchone()
    return {"code": row["code"], "percent_off": row["percent_off"]}'''

TEMPLATE = """You write one Python function (a "handler") for a small online shop backend. The task names it.

{rules}

How handlers are written here:
- A handler takes an open psycopg connection `conn` (autocommit, rows are dicts) and plain inputs.
- Use plain SQL: `row = conn.execute("... %s ...", (value,)).fetchone()` (or `.fetchall()`). Call `.fetchone()` on what `conn.execute` returns, never on `conn`. This also works for `INSERT ... RETURNING id`.
- Rows are dicts: read a column as `row["qty"]`, never `row[0]`.
- There is no `lastrowid`. To get the id of a row you insert, add `RETURNING id` to the INSERT and read `.fetchone()["id"]`.
- No ORM and no web framework. Do not import fastapi, sqlalchemy or models. The module already imports `ShopError`.
- Do all the changes of one call inside a single `with conn.transaction():` block.
- To fail, `raise ShopError("code")`. Return a dict.
- Publish an event only if the task says the handler publishes one. Most handlers publish none: then do not touch `outbox`. To publish: `conn.execute("INSERT INTO outbox (event, payload) VALUES (%s, %s)", ("order.placed", Jsonb({{"order_id": order_id}})))`, with `from psycopg.types.json import Jsonb` above your function.
- Change only the tables the task describes.

Two examples in this style (they are not tasks):
```python
{example}
```

How to work:
1. Reply with your function in one ```python block and nothing else. That saves it (write_file) into the module; every other function there is kept.
2. The result reports the visible tests for your function: how many pass, and why the others fail, with the line of your code that failed.
3. If any fail, reply with the corrected function in a ```python block. Change the line that failed; do not send the same code again.
4. When they all pass, call finish.
The schema is below, so you do not need to read it. The visible tests are in app/tests/visible/test_visible.py.

Database schema (app/schema.sql):
```sql
{schema}
```"""


# AgentTune's defaults tell the model to "answer the original question as plain text" after a tool
# result, which makes a small model explain a failing test instead of fixing its code. These replace them.
TOOLS_FALLBACK = (
    "You can call these tools:\n{tools}\n\n"
    "To write your function, reply with it in one ```python block: that is a write_file call. "
    "To call any other tool, reply with only a JSON list and no other text: "
    '[{{"tool_name": "<name>", "parameters": {{"<argument>": <value>}}}}]. '
    'A user turn that starts with "Tool result" is the output of your call, not a new message from the '
    "user. If it reports failing tests, the tests are right and your function is wrong: reply with the "
    "corrected function. Do not explain. Reply in plain text only after finish answers Done."
)
TOOL_RESULT = ("Tool result for {name}: {content}\n"
               "If tests fail, reply with the corrected function in a ```python block. If they pass, call finish.")


def shared_rules(spec_text: str) -> str:
    """The Build subsections every task needs, without headings. Never reads past `## Build`."""
    build = split_spec(spec_text).build
    chunks: dict[str, list[str]] = {}
    current = None
    for line in build.splitlines():
        m = re.match(r"^### (.+?)\s*$", line)
        if m:
            current = m.group(1)
            continue
        if current in SHARED:
            chunks.setdefault(current, []).append(line)
    return "\n\n".join("\n".join(chunks[name]).strip() for name in SHARED if name in chunks)


def _sql_without_comments(sql: str) -> str:
    lines = [re.sub(r"\s*--.*$", "", line) for line in sql.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line.strip() or line == "")).strip()


def schema_digest(sql: str) -> str:
    """One line per table: `carts(id, user_id, status, coupon_code)`. Short enough to repeat next to
    every request, where a small model actually uses it (it gets column names wrong otherwise)."""
    out = []
    for name, body in re.findall(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", sql, flags=re.DOTALL):
        cols = []
        for line in body.splitlines():
            word = re.sub(r"\s*--.*$", "", line).strip().split(" ")[0].strip(",")
            if word and word.upper() not in {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"} \
                    and re.fullmatch(r"\w+", word):
                cols.append(word)
        out.append(f"{name}({', '.join(cols)})")
    return "\n".join(out)


def system_prompt(spec: Path = SPEC, schema: Path = SCHEMA) -> str:
    return TEMPLATE.format(rules=shared_rules(spec.read_text()), example=EXAMPLE,
                           schema=_sql_without_comments(schema.read_text()))
