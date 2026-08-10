"""Draait alle migrations/*.sql in volgorde. Idempotent (de SQL is het ook)."""

from pathlib import Path

from db import get_engine, INVENTORY


def main():
    engine = get_engine()
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted((INVENTORY / "migrations").glob("*.sql")):
            print(f"→ {sql_file.name}")
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()
    print("Migraties toegepast.")


if __name__ == "__main__":
    main()
