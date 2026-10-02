"""Group recommender: feature extraction, scoring and training (pure Python, no DB).

The model is a logistic regression over a handful of interpretable features. It
starts from hand-tuned weights and is periodically re-fitted on its own logs
(recommendation shown -> did the student join within N days?), which is what makes
it a "smart" feature that improves with usage.
"""

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.core.geo import haversine_m

FEATURES = (
    "interest_match",
    "category_affinity",
    "schedule_fit",
    "proximity",
    "popularity",
    "verified",
)

DEFAULT_MODEL_VERSION = "heuristic-v1"
DEFAULT_WEIGHTS: dict[str, float] = {
    "bias": -3.0,
    "interest_match": 3.5,
    "category_affinity": 1.2,
    "schedule_fit": 1.0,
    "proximity": 0.8,
    "popularity": 0.6,
    "verified": 0.3,
}

MAX_TRAINING_ROWS = 8_000

# Distance at which proximity has decayed to ~37% (1/e).
PROXIMITY_SCALE_M = 400
NEUTRAL = 0.5


@dataclass(frozen=True)
class StudentProfile:
    interest_ids: frozenset[int]
    # interest category id -> number of the student's interests in it
    category_counts: dict[int, int]
    # Coordinates of the buildings where the student has classes.
    class_locations: tuple[tuple[float, float], ...]
    # Returns True if the student has no class at that moment.
    is_free_at: Callable[[datetime], bool] | None = None


@dataclass(frozen=True)
class GroupProfile:
    group_id: int
    category_id: int
    tag_ids: frozenset[int]
    meeting_location: tuple[float, float] | None
    event_times: tuple[datetime, ...]
    member_count: int
    verified: bool
    tag_names: dict[int, str] = field(default_factory=dict)


def compute_features(
    student: StudentProfile, group: GroupProfile, max_members: int
) -> dict[str, float]:
    matched = student.interest_ids & group.tag_ids
    interest_match = len(matched) / len(group.tag_ids) if group.tag_ids else 0.0

    total_interests = sum(student.category_counts.values())
    category_affinity = (
        student.category_counts.get(group.category_id, 0) / total_interests
        if total_interests
        else 0.0
    )

    if student.is_free_at is not None and group.event_times:
        free = sum(1 for moment in group.event_times if student.is_free_at(moment))
        schedule_fit = free / len(group.event_times)
    else:
        schedule_fit = NEUTRAL

    if group.meeting_location and student.class_locations:
        distance = min(
            haversine_m(*group.meeting_location, *location) for location in student.class_locations
        )
        proximity = math.exp(-distance / PROXIMITY_SCALE_M)
    else:
        proximity = NEUTRAL

    popularity = math.log1p(group.member_count) / math.log1p(max_members) if max_members else 0.0

    return {
        "interest_match": round(interest_match, 4),
        "category_affinity": round(category_affinity, 4),
        "schedule_fit": round(schedule_fit, 4),
        "proximity": round(proximity, 4),
        "popularity": round(popularity, 4),
        "verified": 1.0 if group.verified else 0.0,
    }


def score(features: dict[str, float], weights: dict[str, float]) -> float:
    z = weights.get("bias", 0.0) + sum(weights.get(name, 0.0) * features[name] for name in FEATURES)
    return 1 / (1 + math.exp(-z))


def explain(features: dict[str, float], group: GroupProfile, student: StudentProfile) -> list[str]:
    """Short human-readable reasons shown next to each recommendation."""
    reasons = []
    matched = sorted(
        group.tag_names[i] for i in student.interest_ids & group.tag_ids if i in group.tag_names
    )
    if matched:
        reasons.append(f"Matches your interests: {', '.join(matched)}")
    if group.event_times and features["schedule_fit"] >= 0.75:
        reasons.append("Its events fit your free time")
    if features["proximity"] >= 0.6 and group.meeting_location:
        reasons.append("Meets close to where you have classes")
    if features["popularity"] >= 0.7:
        reasons.append("Popular on campus")
    if features["verified"]:
        reasons.append("Verified group")
    return reasons


# --- Training ------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingResult:
    weights: dict[str, float]
    auc: float
    rows: int
    positive_rate: float


def train_logistic_regression(
    rows: Sequence[tuple[dict[str, float], int]],
    epochs: int = 300,
    learning_rate: float = 0.5,
    l2: float = 0.001,
    seed: int = 7,
) -> TrainingResult:
    """Batch gradient descent with L2 regularization (bias not regularized).

    Large logs are down-sampled (deterministically) to keep training fast in pure Python.
    """
    if not rows:
        raise ValueError("No training rows")
    if len(rows) > MAX_TRAINING_ROWS:
        rows = random.Random(seed).sample(list(rows), MAX_TRAINING_ROWS)
    weights = {"bias": 0.0, **dict.fromkeys(FEATURES, 0.0)}
    n = len(rows)
    for _ in range(epochs):
        gradient = dict.fromkeys(weights, 0.0)
        for features, label in rows:
            error = score(features, weights) - label
            gradient["bias"] += error
            for name in FEATURES:
                gradient[name] += error * features[name]
        for name in weights:
            penalty = 0.0 if name == "bias" else l2 * weights[name]
            weights[name] -= learning_rate * (gradient[name] / n + penalty)

    # Evaluate on a deterministic sample to keep AUC cheap on large logs.
    sample = list(rows)
    if len(sample) > 20_000:
        sample = random.Random(seed).sample(sample, 20_000)
    auc = roc_auc([score(f, weights) for f, _ in sample], [label for _, label in sample])
    positives = sum(label for _, label in rows)
    return TrainingResult(
        weights={k: round(v, 4) for k, v in weights.items()},
        auc=round(auc, 4),
        rows=n,
        positive_rate=round(positives / n, 4),
    )


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Probability that a random positive is scored above a random negative."""
    ranked = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0])
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        average_rank = (i + j) / 2 + 1  # ties share the average rank
        rank_sum += average_rank * sum(label for _, label in ranked[i : j + 1])
        i = j + 1
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)
