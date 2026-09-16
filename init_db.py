"""
init_db.py — Initialise the question_bank.db SQLite database.

Reads the schema from schema.sql and creates all tables and indexes.

Usage::

    python init_db.py
    python init_db.py --path C:/Users/92534/Desktop/考研/src/question_bank.db
"""

import os
import sqlite3
import sys


def init_db(db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db",
            schema_path: str = "C:/Users/92534/Desktop/考研/src/schema.sql") -> None:
    """
    Initialise the database schema and create all tables.

    Parameters
    ----------
    db_path : str
        Path where the SQLite database will be created / updated.
    schema_path : str
        Path to the SQL schema file.
    """
    if not os.path.exists(schema_path):
        print(f"ERROR: Schema file not found: {schema_path}", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()

    # Ensure the parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.close()

    print(f"Database initialised at {db_path}")
    print(f"  Schema loaded from {schema_path}")


if __name__ == "__main__":
    path = "C:/Users/92534/Desktop/考研/src/question_bank.db"
    if len(sys.argv) > 1 and sys.argv[1] == "--path":
        path = sys.argv[2]
    init_db(db_path=path)
