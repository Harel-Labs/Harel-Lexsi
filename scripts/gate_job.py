"""Run the V7 gate inside an HF Job (it needs Postgres) and save its log to $OUT_DIR/gate/.

    hf_job.py run scripts/gate_job.py                      # against the 43 hidden contracts of spec/spec.md
    hf_job.py run scripts/gate_job.py -- --fixtures        # against the test-fixture contracts

The gate itself is `python -m harel_lexsi.verifier.gate`; this only starts and migrates a database first.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    import train_t3

    from harel_lexsi.verifier import gate
    from harel_lexsi.verifier.replay import _shop_db

    train_t3.start_postgres()
    db = _shop_db()
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    fixtures = "--fixtures" in sys.argv[1:]
    out = Path(os.environ.get("OUT_DIR", ROOT / "runs")) / "gate"
    out.mkdir(parents=True, exist_ok=True)
    log = out / ("gate-fixtures.md" if fixtures else "gate-holdout.md")
    args = ["--log", str(log)] + ([] if fixtures else ["--spec", str(ROOT / "spec" / "spec.md")])
    return gate.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
