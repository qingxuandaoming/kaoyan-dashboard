"""
FSRS-5 (Free Spaced Repetition Scheduler, version 5) — Complete Python Implementation.

This module provides:
  - Card: dataclass representing a flashcard with FSRS state.
  - FSRScheduler: core scheduling engine implementing the FSRS-5 algorithm.
  - DailyPlanner: generates daily study plans based on FSRS state and topic priorities.
  - Helper functions for knowledge graph loading, notes indexing, and phase calculation.

Exam target date: 2026-12-19
"""

import json
import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DECAY: float = -0.5
FACTOR: float = 19.0 / 81.0  # 0.9^(1/DECAY) - 1 ≈ 0.2345…

# FSRS-5 default parameters (19 weights, v4 optimised)
DEFAULT_PARAMS: List[float] = [
    # w0-w3: initial stability for ratings Again / Hard / Good / Easy
    0.4072, 1.1829, 3.1262, 15.4722,
    # w4-w7: initial difficulty + difficulty update
    7.2102, 0.5316, 1.0651, 0.0234,
    # w8-w11: recall stability growth
    1.616,  0.1544, 1.0824, 1.9813,
    # w12-w15: forget stability decay + Hard penalty + Easy bonus
    0.0953, 0.2975, 2.2042, 0.2407,
    # w16-w18: lapse stability decay + difficulty decay
    0.5014, 0.6539, 2.0613,
]

# ---------------------------------------------------------------------------
# Enums / constants for card states
# ---------------------------------------------------------------------------
class State:
    NEW = 0
    LEARNING = 1
    REVIEW = 2
    RELEARNING = 3


class Queue:
    NEW = 0
    LEARNING = 1
    REVIEW = 2
    SUSPENDED = 3


class Rating:
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


# ---------------------------------------------------------------------------
# Card dataclass
# ---------------------------------------------------------------------------
@dataclass
class Card:
    """Represents a single flashcard with its FSRS scheduling state."""
    id: str                          # Card ID
    question_id: str                 # Reference to question
    state: int = State.NEW           # 0=New, 1=Learning, 2=Review, 3=Relearning
    difficulty: float = 0.0          # D parameter (1-10)
    stability: float = 0.0           # S parameter (days)
    due_date: str = ""               # ISO date string (YYYY-MM-DD)
    last_review: str = ""            # ISO datetime string
    reps: int = 0                    # Total review count
    lapses: int = 0                  # Total lapse (forget) count
    queue: int = Queue.NEW           # Scheduling queue
    interval_days: float = 0.0       # Last computed interval in days


# ---------------------------------------------------------------------------
# FSRS-5 Scheduler
# ---------------------------------------------------------------------------
class FSRScheduler:
    """
    Core FSRS-5 scheduling engine.

    ⚠️⚠️ 已废弃 —— 请勿在此新增调度特性 ⚠️⚠️

    生产的唯一真相源是 **src/fsrs_core.js**（由 src/serve.js 调用）。
    本类自 2026-09-13 起降级为「公式参考 + parity 测试基准」：
    learning steps / fuzz / max_interval / leech 等新特性只在 fsrs_core.js
    实现，本文件不再跟进。

    保留原因：作为跨语言一致性对照。两者由
    `python src/tools/test_fsrs_parity.py` 逐位校验（纯公式 2400 个值）。
    若该测试失败，说明两边发生了漂移，应先确认改动是否有意为之。

    已知语义差异（非 bug，勿"修复"）：
      本类的 elapsed 锚定「今日零点」，fsrs_core.js 锚定「当前时刻」。
      因此带 last_review 的卡在两版间 stability 会有微小差异，
      parity 测试对这类情况只比对状态转移，不比对数值。

    Usage::

        fsrs = FSRScheduler()
        card = Card(id="c1", question_id="q1")
        card = fsrs.schedule(card, rating=3)  # Good
    """

    # Class-level constants
    DECAY = DECAY
    FACTOR = FACTOR

    def __init__(self, w: Optional[List[float]] = None):
        """
        Initialise the scheduler.

        Parameters
        ----------
        w : list[float], optional
            19-element parameter vector. Defaults to ``DEFAULT_PARAMS``.
        """
        if w is None:
            self.w = list(DEFAULT_PARAMS)
        else:
            if len(w) != 19:
                raise ValueError(f"Expected 19 parameters, got {len(w)}")
            self.w = list(w)

        self.desired_retention: float = 0.85  # default target retrievability
        self.today: date = date.today()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    @classmethod
    def get_interval(cls, S: float, desired_retention: float) -> float:
        """
        Calculate the review interval (in days) from stability and desired
        retention using the power forgetting curve.

        Formula::

            I = S / FACTOR * (R^(1/DECAY) - 1)

        where FACTOR = 19/81 and DECAY = -0.5.
        """
        if S <= 0:
            return 0.0
        return S / cls.FACTOR * (desired_retention ** (1.0 / cls.DECAY) - 1.0)

    # ------------------------------------------------------------------
    # Core scheduling entry point
    # ------------------------------------------------------------------
    def schedule(self, card: Card, rating: int,
                 today: Optional[date] = None) -> Card:
        """
        Rate a card and update its FSRS state.

        Parameters
        ----------
        card : Card
            The card to review.
        rating : int
            1=Again, 2=Hard, 3=Good, 4=Easy.
        today : date, optional
            Override for "today" (defaults to ``date.today()``).

        Returns
        -------
        Card
            The updated card (mutated in-place *and* returned).
        """
        if today is None:
            today = self.today
        if rating not in (1, 2, 3, 4):
            raise ValueError(f"Rating must be 1-4, got {rating}")

        # Elapsed days since last review
        elapsed = 0.0
        if card.last_review:
            try:
                lr = datetime.fromisoformat(card.last_review)
                elapsed = max(
                    0.0,
                    (datetime.combine(today, datetime.min.time()) - lr).total_seconds()
                    / 86400.0,
                )
            except (ValueError, TypeError):
                elapsed = 0.0

        card.last_review = datetime.now().isoformat(timespec="seconds")

        # ---- NEW card ----
        if card.state == State.NEW:
            self._schedule_new(card, rating, today)

        # ---- LEARNING or RELEARNING ----
        elif card.state in (State.LEARNING, State.RELEARNING):
            if rating == Rating.AGAIN:
                card.lapses += 1
            # Re-compute difficulty & stability
            card.difficulty = self._next_difficulty(card.difficulty, rating)
            card.stability = self._stability_after_recall(
                card.difficulty, card.stability, elapsed, rating
            )
            interval = self.get_interval(card.stability, self.desired_retention)
            card.interval_days = interval
            if rating >= Rating.GOOD:
                card.state = State.REVIEW
                card.queue = Queue.REVIEW
                card.due_date = (today + timedelta(days=max(1, round(interval)))).isoformat()
            else:
                card.due_date = today.isoformat()

        # ---- REVIEW ----
        elif card.state == State.REVIEW:
            if rating == Rating.AGAIN:
                card.lapses += 1
                card.difficulty = self._next_difficulty(card.difficulty, rating)
                card.stability = self._stability_after_forget(
                    card.difficulty, card.stability, elapsed
                )
                card.state = State.RELEARNING
                card.queue = Queue.LEARNING
                card.due_date = today.isoformat()
            else:
                card.difficulty = self._next_difficulty(card.difficulty, rating)
                card.stability = self._stability_after_recall(
                    card.difficulty, card.stability, elapsed, rating
                )
                interval = self.get_interval(card.stability, self.desired_retention)
                card.interval_days = interval
                card.due_date = (today + timedelta(days=max(1, round(interval)))).isoformat()

        card.reps += 1
        return card

    # ------------------------------------------------------------------
    # Internal: NEW card initialisation
    # ------------------------------------------------------------------
    def _schedule_new(self, card: Card, rating: int, today: date) -> None:
        """Handle a brand-new card's first review."""
        card.difficulty = self._init_difficulty(rating)
        card.stability = self._init_stability(rating)

        if rating == Rating.AGAIN:
            card.state = State.LEARNING
            card.queue = Queue.LEARNING
            card.due_date = today.isoformat()
            card.interval_days = 0.0
        else:
            card.state = State.REVIEW
            card.queue = Queue.REVIEW
            interval = self.get_interval(card.stability, self.desired_retention)
            card.interval_days = interval
            card.due_date = (today + timedelta(days=max(1, round(interval)))).isoformat()

    # ------------------------------------------------------------------
    # FSRS-5 formulae (private)
    # ------------------------------------------------------------------
    def _init_difficulty(self, rating: int) -> float:
        """
        Initial difficulty for a new card.

        D0(G) = w4 - (G - 3) * w5
        Clamped to [1, 10].
        """
        D = self.w[4] - (rating - 3) * self.w[5]
        return max(1.0, min(10.0, D))

    def _next_difficulty(self, D: float, rating: int) -> float:
        """
        Update difficulty after a review.

        delta_D = -w6 * (rating - 3)
        D' = w7 * D0(3) + (1 - w7) * (D + delta_D)
        Clamped to [1, 10].
        """
        delta_D = -self.w[6] * (rating - 3)
        D_new = self.w[7] * self._init_difficulty(3) + (1.0 - self.w[7]) * (D + delta_D)
        return max(1.0, min(10.0, D_new))

    def _init_stability(self, rating: int) -> float:
        """
        Initial stability S0 for a new card.
        S0 = w[rating - 1]  (one of w0, w1, w2, w3).
        """
        return self.w[rating - 1]

    def _stability_after_recall(
        self, D: float, S: float, elapsed: float, rating: int
    ) -> float:
        """
        Stability after successful recall (rating >= 2).

        S'_r = S * (
            e^(w8)
            * (11 - D)
            * S^(-w9)
            * (e^(w10 * (1 - R)) - 1)
            * hard_penalty
            * easy_bonus
            + 1
        )

        where R = retrievability at elapsed time.
        """
        if S <= 0:
            S = 0.1  # safety floor

        R = self._retrievability(elapsed, S)

        hard_penalty = self.w[14] if rating == Rating.HARD else 1.0
        easy_bonus = self.w[15] if rating == Rating.EASY else 1.0

        new_S = S * (
            math.exp(self.w[8])
            * (11.0 - D)
            * (S ** (-self.w[9]))
            * (math.exp(self.w[10] * (1.0 - R)) - 1.0)
            * hard_penalty
            * easy_bonus
            + 1.0
        )
        return max(0.1, new_S)

    def _stability_after_forget(
        self, D: float, S: float, elapsed: float
    ) -> float:
        """
        Stability after forgetting (rating = 1 / Again).

        S'_f = w11
             * D^(-w12)
             * ((S + 1)^w13 - 1)
             * e^(w14 * (1 - R))
        """
        if S <= 0:
            S = 0.1

        R = self._retrievability(elapsed, S)

        new_S = (
            self.w[11]
            * (D ** (-self.w[12]))
            * ((S + 1.0) ** self.w[13] - 1.0)
            * math.exp(self.w[14] * (1.0 - R))
        )
        return max(0.1, min(new_S, S))  # never exceed previous stability

    @staticmethod
    def _retrievability(elapsed: float, S: float) -> float:
        """
        Power forgetting curve — current retrievability.

        R(t, S) = (1 + FACTOR * t / S) ^ DECAY
        """
        if S <= 0:
            return 0.0
        return (1.0 + FACTOR * elapsed / S) ** DECAY

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------
    def card_to_dict(self, card: Card) -> dict:
        """Convert a Card to a plain dictionary (for JSON / DB storage)."""
        return {
            "id": card.id,
            "question_id": card.question_id,
            "state": card.state,
            "difficulty": round(card.difficulty, 4),
            "stability": round(card.stability, 4),
            "due_date": card.due_date,
            "last_review": card.last_review,
            "reps": card.reps,
            "lapses": card.lapses,
            "queue": card.queue,
            "interval_days": round(card.interval_days, 2),
        }

    @staticmethod
    def card_from_dict(d: dict) -> Card:
        """Reconstruct a Card from a dictionary."""
        return Card(
            id=d.get("id", ""),
            question_id=d.get("question_id", ""),
            state=int(d.get("state", 0)),
            difficulty=float(d.get("difficulty", 0.0)),
            stability=float(d.get("stability", 0.0)),
            due_date=d.get("due_date", ""),
            last_review=d.get("last_review", ""),
            reps=int(d.get("reps", 0)),
            lapses=int(d.get("lapses", 0)),
            queue=int(d.get("queue", 0)),
            interval_days=float(d.get("interval_days", 0.0)),
        )

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------
    def get_due_cards(self, cards: List[Card],
                      today: Optional[date] = None) -> List[Card]:
        """Return all cards that are due for review on *today*."""
        if today is None:
            today = self.today
        today_str = today.isoformat()
        return [c for c in cards if c.due_date and c.due_date <= today_str]

    def get_new_cards(self, cards: List[Card]) -> List[Card]:
        """Return all cards in the NEW state."""
        return [c for c in cards if c.state == State.NEW]

    def summary(self, cards: List[Card],
                today: Optional[date] = None) -> dict:
        """Return a summary dict: counts by state + due count."""
        if today is None:
            today = self.today
        today_str = today.isoformat()
        counts = {
            "new": 0, "learning": 0, "review": 0, "relearning": 0,
            "due": 0, "total": len(cards),
        }
        for c in cards:
            if c.state == State.NEW:
                counts["new"] += 1
            elif c.state == State.LEARNING:
                counts["learning"] += 1
            elif c.state == State.REVIEW:
                counts["review"] += 1
            elif c.state == State.RELEARNING:
                counts["relearning"] += 1
            if c.due_date and c.due_date <= today_str:
                counts["due"] += 1
        return counts


# ---------------------------------------------------------------------------
# Daily Planner
# ---------------------------------------------------------------------------
class DailyPlanner:
    """
    Generate daily study plans based on FSRS state, topic priorities,
    and exam countdown.

    Usage::

        planner = DailyPlanner()
        plan = planner.generate_plan(
            study_date=date.today(),
            fs_hours=6.0,
            cards_due=due_cards,
            new_cards_per_subject={"math": 20, "408": 15, ...},
            exam_weights={"math": 0.3, "408": 0.3, ...},
            topics=topic_list,
            fsrs=fsrs_scheduler,
        )
    """

    EXAM_DATE = date(2026, 12, 19)

    def __init__(self, exam_date: Optional[date] = None):
        self.exam_date = exam_date or self.EXAM_DATE

    # ------------------------------------------------------------------
    # Phase & countdown
    # ------------------------------------------------------------------
    def days_to_exam(self, today: Optional[date] = None) -> int:
        """Return the number of days remaining until the exam."""
        if today is None:
            today = date.today()
        return max(0, (self.exam_date - today).days)

    def get_phase(self, today: Optional[date] = None) -> dict:
        """
        Determine the current study phase based on days remaining.

        Returns a dict with keys: phase (str), days_left (int),
        phase_progress (float 0-1).
        """
        if today is None:
            today = date.today()
        days_left = (self.exam_date - today).days

        if days_left < 0:
            return {"phase": "post_exam", "days_left": 0, "phase_progress": 1.0}
        elif days_left <= 30:
            total = 30
            return {"phase": "sprint", "days_left": days_left,
                    "phase_progress": 1.0 - days_left / total}
        elif days_left <= 90:
            total = 90
            return {"phase": "consolidation", "days_left": days_left,
                    "phase_progress": 1.0 - (days_left - 30) / (total - 30)}
        elif days_left <= 180:
            total = 180
            return {"phase": "deep_study", "days_left": days_left,
                    "phase_progress": 1.0 - (days_left - 90) / (total - 90)}
        else:
            total = 365
            return {"phase": "foundation", "days_left": days_left,
                    "phase_progress": 1.0 - (days_left - 180) / (total - 180)}

    # ------------------------------------------------------------------
    # Topic priority
    # ------------------------------------------------------------------
    @staticmethod
    def compute_priority(
        topic: dict,
        days_to_exam: int,
        mastery: float,
        srs_due_count: int,
        difficulty_bonus: float = 0.0,
    ) -> float:
        """
        Compute a priority score for a topic on a given day.

        Parameters
        ----------
        topic : dict
            Topic metadata (must include ``exam_weight``).
        days_to_exam : int
            Days remaining until the exam.
        mastery : float
            Current mastery level 0-1.
        srs_due_count : int
            Number of cards due for this topic.
        difficulty_bonus : float
            Extra weight for difficult topics.

        Returns
        -------
        float
            Priority score (higher = more important to study today).
        """
        # Urgency: grows as exam approaches
        if days_to_exam <= 0:
            urgency = 1.0
        else:
            urgency = 1.0 / (1.0 + days_to_exam * 0.01)

        importance = topic.get("exam_weight", 1.0)
        gap = max(0.0, 1.0 - mastery)

        # SRS boost: more due cards => higher priority
        srs_boost = min(srs_due_count / 10.0, 1.0)

        priority = (
            urgency * 0.25
            + importance * 0.35
            + gap * 0.20
            + srs_boost * 0.10
            + difficulty_bonus * 0.10
        )
        return round(priority, 4)

    # ------------------------------------------------------------------
    # Hour allocation
    # ------------------------------------------------------------------
    @staticmethod
    def allocate_hours(
        subjects: List[dict],
        exam_weights: dict,
        total_hours: float = 10.0,
    ) -> Dict[str, float]:
        """
        Allocate study hours to each subject based on exam weights and
        mastery gaps.

        Parameters
        ----------
        subjects : list[dict]
            Each dict must have ``name``, ``current_mastery``, ``target_mastery``.
        exam_weights : dict
            Mapping subject_name -> weight (0-1).
        total_hours : float
            Total available study hours for the day.

        Returns
        -------
        dict[str, float]
            subject_name -> allocated hours (clamped to [1, total*0.4]).
        """
        raw: Dict[str, float] = {}
        for subj in subjects:
            name = subj["name"]
            gap = max(0.1, subj.get("target_mastery", 0.8) - subj.get("current_mastery", 0.0))
            weight = exam_weights.get(name, 1.0)
            raw[name] = weight * gap

        total_raw = sum(raw.values()) or 1.0
        hours: Dict[str, float] = {}
        for name, r in raw.items():
            hours[name] = total_hours * r / total_raw

        # Clamp each subject to [1.0, 40% of total]
        cap = total_hours * 0.4
        return {k: round(max(1.0, min(v, cap)), 2) for k, v in hours.items()}

    # ------------------------------------------------------------------
    # Generate daily plan
    # ------------------------------------------------------------------
    def generate_plan(
        self,
        study_date: date,
        fs_hours: float,
        cards_due: List[Card],
        new_cards_per_subject: Dict[str, int],
        exam_weights: Dict[str, float],
        topics: List[dict],
        fsrs: FSRScheduler,
    ) -> dict:
        """
        Generate a daily study plan for a specific date.

        Returns a dict with keys:
          - date: ISO date string
          - phase: current study phase info
          - hours: dict of subject -> allocated hours
          - review_cards: list of card IDs due for review
          - new_cards: dict of subject -> count of new cards to study
          - priorities: list of (topic_id, priority_score) sorted descending
          - summary: FSRS card state summary
        """
        phase = self.get_phase(study_date)
        days_left = self.days_to_exam(study_date)

        # Build per-topic priorities
        priorities: List[Tuple[str, float]] = []
        for topic in topics:
            topic_id = topic.get("id", "")
            mastery = topic.get("mastery", 0.0)
            due_count = sum(
                1 for c in cards_due
                if c.question_id and c.question_id.startswith(topic_id)
            )
            diff_bonus = topic.get("difficulty", 0.5)
            score = self.compute_priority(topic, days_left, mastery, due_count, diff_bonus)
            priorities.append((topic_id, score))

        priorities.sort(key=lambda x: x[1], reverse=True)

        # Build subjects list for hour allocation
        subjects_map: Dict[str, dict] = {}
        for topic in topics:
            subj = topic.get("subject", topic.get("_subject", "unknown"))
            if subj not in subjects_map:
                subjects_map[subj] = {
                    "name": subj,
                    "current_mastery": 0.0,
                    "target_mastery": 0.85,
                    "_count": 0,
                    "_mastery_sum": 0.0,
                }
            subjects_map[subj]["_count"] += 1
            subjects_map[subj]["_mastery_sum"] += topic.get("mastery", 0.0)

        subjects: List[dict] = []
        for s in subjects_map.values():
            if s["_count"] > 0:
                s["current_mastery"] = round(s["_mastery_sum"] / s["_count"], 3)
            subjects.append(s)

        hours = self.allocate_hours(subjects, exam_weights, total_hours=fs_hours)

        # Summary
        all_card_ids = [c.id for c in cards_due]

        return {
            "date": study_date.isoformat(),
            "phase": phase,
            "hours": hours,
            "review_cards": all_card_ids,
            "new_cards": new_cards_per_subject,
            "priorities": priorities,
            "summary": fsrs.summary(cards_due, study_date),
        }


# ---------------------------------------------------------------------------
# Knowledge graph & notes helpers
# ---------------------------------------------------------------------------
def get_days_to_exam(
    exam_date: date = date(2026, 12, 19),
    today: Optional[date] = None,
) -> int:
    """Return days remaining until exam."""
    if today is None:
        today = date.today()
    return max(0, (exam_date - today).days)


def load_all_knowledge_graphs(
    base_path: str = "C:/Users/92534/Desktop/考研/src/knowledge_graph/",
) -> Dict[str, dict]:
    """
    Load all 4 knowledge graphs (408, math, politics, english) from JSON files.

    Returns
    -------
    dict[str, dict]
        Mapping of subject key -> graph data.
    """
    graphs: Dict[str, dict] = {}
    subjects = ["408", "math", "politics", "english"]
    for subj in subjects:
        path = os.path.join(base_path, f"{subj}_graph.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                graphs[subj] = json.load(f)
    return graphs


def load_notes_index(
    path: str = "C:/Users/92534/Desktop/考研/src/笔记索引.yaml",
) -> List[dict]:
    """
    Load the notes index from a YAML file.

    Returns a list of entry dicts.  If the file does not exist, returns [].
    """
    try:
        import yaml
    except ImportError:
        # Fallback: try to parse as JSON
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else data.get("entries", [])
        return []

    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f)

    if isinstance(index, list):
        return index
    return index.get("entries", []) if isinstance(index, dict) else []


def extract_topics_from_graphs(
    graphs: Dict[str, dict],
) -> Tuple[List[dict], set]:
    """
    Extract all topics from knowledge graphs and build a coverage set.

    Returns
    -------
    all_topics : list[dict]
        Flat list of topic dicts (each annotated with ``_subject``).
    covered_topic_ids : set[str]
        Set of all topic/subtopic IDs found.
    """
    all_topics: List[dict] = []
    covered_topic_ids: set = set()

    for subject_name, graph_data in graphs.items():
        topics = graph_data.get("topics", [])
        if isinstance(graph_data, dict):
            # Handle nested subject structure
            for sub_key, sub_data in graph_data.items():
                if isinstance(sub_data, dict) and "topics" in sub_data:
                    topics.extend(sub_data["topics"])

        for topic in topics:
            topic["_subject"] = subject_name
            all_topics.append(topic)
            topic_id = topic.get("id", "")
            if topic_id:
                covered_topic_ids.add(topic_id)
            for sub in topic.get("subtopics", []):
                sub_id = sub.get("id", "")
                if sub_id:
                    covered_topic_ids.add(sub_id)

    return all_topics, covered_topic_ids


def match_notes_to_topics(
    entries: List[dict],
    graphs: Dict[str, dict],
) -> set:
    """
    Match notes index entries to knowledge-graph topic IDs.

    Returns a set of covered topic IDs that have corresponding notes.
    """
    subject_map = {
        "408": {"DS": "DS", "CO": "CO", "OS": "OS", "CN": "CN"},
        "math": {"高数": "GS", "线代": "LA", "概率": "PR"},
        "politics": {"马原": "MY", "史纲": "SG", "毛中特": "MZT", "思修": "SX"},
        "english": {},
    }

    covered: set = set()
    for entry in entries:
        entry_id = entry.get("id", "")
        if entry_id:
            covered.add(entry_id)

    return covered


# ---------------------------------------------------------------------------
# SQLite convenience helpers
# ---------------------------------------------------------------------------
def load_all_questions(db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db") -> List[dict]:
    """Load all questions from the SQLite database."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM questions")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def load_all_cards(db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db") -> List[Card]:
    """Load all cards from the SQLite database as Card objects."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM cards")
    cards = [FSRScheduler.card_from_dict(dict(row)) for row in cursor.fetchall()]
    conn.close()
    return cards


def load_cards_by_topic(
    db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db",
) -> Dict[str, List[Card]]:
    """Load all cards grouped by their question's topic_id."""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("""
        SELECT c.*, q.topic_id
        FROM cards c
        LEFT JOIN questions q ON c.question_id = q.id
    """)
    by_topic: Dict[str, List[Card]] = {}
    for row in cursor.fetchall():
        d = dict(row)
        tid = d.get("topic_id", "unknown")
        card = FSRScheduler.card_from_dict(d)
        if tid not in by_topic:
            by_topic[tid] = []
        by_topic[tid].append(card)

    conn.close()
    return by_topic


def export_srs_state(db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db") -> dict:
    """
    Export the full SRS state from the database as a dict keyed by card ID.
    Suitable for JSON serialisation.
    """
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cards = conn.execute("SELECT * FROM cards").fetchall()

    state: dict = {}
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


def get_all(
    db_path: str = "C:/Users/92534/Desktop/考研/src/question_bank.db",
) -> Tuple[List[dict], List[Card], Dict[str, List[dict]]]:
    """
    Load everything: all questions, all cards, and questions grouped by topic.

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
# Main (smoke test)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    fsrs = FSRScheduler()

    # Simulate a new card going through a few reviews
    card = Card(id="test-001", question_id="q-001")
    print("=== FSRS-5 Smoke Test ===\n")

    ratings = [3, 3, 2, 3, 4, 1, 3]
    labels = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}

    for i, r in enumerate(ratings):
        card = fsrs.schedule(card, r, today=date(2026, 7, 16) + timedelta(days=i * 3))
        print(
            f"Review {i+1} [{labels[r]:>4s}]  "
            f"S={card.stability:7.2f}  D={card.difficulty:5.2f}  "
            f"state={card.state}  due={card.due_date}  "
            f"interval={card.interval_days:.1f}d"
        )

    print("\nCard summary:", fsrs.card_to_dict(card))

    # Phase check
    planner = DailyPlanner()
    phase = planner.get_phase(date(2026, 7, 16))
    print(f"\nPhase on 2026-07-16: {phase['phase']} "
          f"({phase['days_left']} days left)")
