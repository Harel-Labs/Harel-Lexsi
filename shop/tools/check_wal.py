"""python -m tools.check_wal : exit non-zero unless logical decoding is possible."""
import sys

from tools.db import assert_wal_logical, connect

if __name__ == "__main__":
    try:
        with connect() as conn:
            assert_wal_logical(conn)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {e}")
        sys.exit(1)
    print("OK: wal_level=logical, REPLICA IDENTITY FULL on all tables")
