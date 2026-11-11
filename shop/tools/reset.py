"""python -m tools.reset : return the database to the seeded state."""
import time

from tools.db import connect, reset

if __name__ == "__main__":
    with connect() as conn:
        t0 = time.perf_counter()
        reset(conn)
        print(f"reset to seeded state in {time.perf_counter() - t0:.3f}s")
