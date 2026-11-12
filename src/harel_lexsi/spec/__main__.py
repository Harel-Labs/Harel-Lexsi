"""python -m harel_lexsi.spec spec/spec.md --out build/

Lints the spec; only if it is clean, writes build.md (what L1 sees) and holdout.yaml.
"""
import argparse
import sys
from pathlib import Path

from .lint import lint_spec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.spec")
    p.add_argument("spec")
    p.add_argument("--out", default="build")
    a = p.parse_args(argv)
    result = lint_spec(Path(a.spec).read_text())
    if not result.ok:
        for e in result.errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "build.md").write_text(result.parts.build)
    (out / "holdout.yaml").write_text(result.holdout_text)
    print(f"OK: wrote {out}/build.md and {out}/holdout.yaml ({len(result.contracts)} contracts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
