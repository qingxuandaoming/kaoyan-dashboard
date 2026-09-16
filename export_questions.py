"""
export_questions.py — Export questions, cards, and SRS state from the
SQLite question_bank.db for use by the flashcard HTML app and other
downstream consumers.

Functions
---------
- load_all_questions()      → list[dict]
- load_questions_by_ids()  → list[dict]
- load_all_cards()          → list[dict]
- export_questions_by_topic() → dict[str, list[dict]]
- export_cards_by_topic()   → dict[str, list[dict]]
- export_srs_state()        → dict[str, dict]
- get_all()                 → tuple (questions, cards, by_topic)

Usage::

    from export_questions import get_all, export_srs_state
    questions, cards, by_topic = get_all()
    state = export_srs_state()
"""

import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple


DB_PATH = "C:/Users/92534/Desktop/考研/src/question_bank.db"


# ---------------------------------------------------------------------------
# Question loaders
# ---------------------------------------------------------------------------
def load_all_questions(db_path: str = DB_PATH) -> List[dict]:
    """Load every row from the ``questions`` table."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM questions")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def load_questions_by_ids(
    question_ids: List[str],
    db_path: str = DB_PATH,
) -> List[dict]:
    """Load questions matching the given IDs."""
    if not question_ids or not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join(["?" for _ in question_ids])
    cursor = conn.execute(
        f"""
        SELECT q.id, q.topic_id, q.type, q.difficulty, q.source, q.content,
               q.times_asked, q.times_correct, q.avg_time_sec, q.last_asked
        FROM questions q
        WHERE q.id IN ({placeholders})
        """,
        question_ids,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Card / topic loaders
# ---------------------------------------------------------------------------
def load_all_cards(db_path: str = DB_PATH) -> List[dict]:
    """Load every row from the ``cards`` table."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM cards")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def export_questions_by_topic(db_path: str = DB_PATH) -> Dict[str, List[dict]]:
    """
    Load all questions and group them by ``topic_id``.

    Returns
    -------
    dict[str, list[dict]]
        Mapping topic_id → list of question dicts.
    """
    questions = load_all_questions(db_path)
    by_topic: Dict[str, List[dict]] = {}
    for q in questions:
        tid = q.get("topic_id", "unknown")
        if tid not in by_topic:
            by_topic[tid] = []
        by_topic[tid].append(q)
    return by_topic


def export_cards_by_topic(db_path: str = DB_PATH) -> Dict[str, List[dict]]:
    """
    Load all cards joined with questions, grouped by ``topic_id``.

    Returns
    -------
    dict[str, list[dict]]
        Mapping topic_id → list of card dicts (with topic_id included).
    """
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT c.*, q.topic_id
        FROM cards c
        LEFT JOIN questions q ON c.question_id = q.id
    """)
    by_topic: Dict[str, List[dict]] = {}
    for row in cursor.fetchall():
        d = dict(row)
        tid = d.get("topic_id") or "unknown"
        if tid not in by_topic:
            by_topic[tid] = []
        by_topic[tid].append(d)
    conn.close()
    return by_topic


# ---------------------------------------------------------------------------
# SRS state export
# ---------------------------------------------------------------------------
def export_srs_state(db_path: str = DB_PATH) -> Dict[str, dict]:
    """
    Export the full FSRS SRS state as a dict keyed by card ID.

    Suitable for JSON serialisation and feeding into the HTML flashcard app.

    Returns
    -------
    dict[str, dict]
        card_id → SRS state fields.
    """
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cards = conn.execute("SELECT * FROM cards").fetchall()

    state: Dict[str, dict] = {}
    for card in cards:
        d = dict(card)
        c_id = d["id"]
        state[c_id] = {
            "question_id": d.get("question_id", ""),
            "state": d.get("state", 0),
            "difficulty": d.get("difficulty", 0.0),
            "stability": d.get("stability", 0.0),
            "due_date": d.get("due_date", ""),
            "last_review": d.get("last_review", ""),
            "reps": d.get("reps", 0),
            "lapses": d.get("lapses", 0),
            "queue": d.get("queue", 0),
            "interval_days": d.get("interval_days", 0.0),
        }
    conn.close()
    return state


# ---------------------------------------------------------------------------
# Combined loader
# ---------------------------------------------------------------------------
def get_all(
    db_path: str = DB_PATH,
) -> Tuple[List[dict], List[dict], Dict[str, List[dict]]]:
    """
    Load everything in one call.

    Returns
    -------
    tuple
        (all_questions, all_cards, questions_by_topic)
    """
    all_questions = load_all_questions(db_path)
    all_cards = load_all_cards(db_path)

    by_topic: Dict[str, List[dict]] = {}
    for q in all_questions:
        tid = q.get("topic_id", "unknown")
        if tid not in by_topic:
            by_topic[tid] = []
        by_topic[tid].append(q)

    return all_questions, all_cards, by_topic


# ---------------------------------------------------------------------------
# JSON export convenience
# ---------------------------------------------------------------------------
def export_to_json(
    output_path: str = "C:/Users/92534/Desktop/考研/src/question_data.json",
    db_path: str = DB_PATH,
) -> str:
    """
    Export all questions, cards, and SRS state to a single JSON file.

    Returns the output path.
    """
    questions, cards, by_topic = get_all(db_path)
    srs_state = export_srs_state(db_path)

    payload = {
        "questions": questions,
        "cards": cards,
        "questions_by_topic": by_topic,
        "srs_state": srs_state,
        "meta": {
            "question_count": len(questions),
            "card_count": len(cards),
            "topic_count": len(by_topic),
        },
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Exported {payload['meta']} to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    db = DB_PATH
    out = "C:/Users/92534/Desktop/考研/src/question_data.json"

    if len(sys.argv) > 1:
        db = sys.argv[1]
    if len(sys.argv) > 2:
        out = sys.argv[2]

    if not os.path.exists(db):
        print(f"Database not found: {db}")
        print("Run init_db.py first to create the schema.")
        sys.exit(1)

    export_to_json(output_path=out, db_path=db)
