"""python -m tools.migrate : rebuild the schema and seed it (destroys all data)."""
from tools.db import connect, migrate

if __name__ == "__main__":
    with connect() as conn:
        migrate(conn)
    print("schema rebuilt and seeded")
