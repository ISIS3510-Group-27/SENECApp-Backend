"""BQ10: detect groups with declining attendance and compare two re-engagement
features (event reminders vs. group messages) with a randomized assignment.

Weeks are rolling 7-day windows ending at "now": week 0 is the most recent.
"""

import hashlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    Event,
    Notification,
    NotificationType,
    ReengagementArm,
    ReengagementCase,
    ReengagementStatus,
    StudentGroup,
)
from app.services import messages as message_service
from app.services import notifications
from app.services.schedule import campus_tz

WEEK = timedelta(days=7)
DETECTION_WEEKS = 8  # 4 baseline weeks + 4 consecutive declining weeks
DECLINE_THRESHOLD = 0.30
MIN_BASELINE_WEEKLY_ATTENDANCE = 3.0
EVALUATION_PERIOD = timedelta(days=28)
REMINDER_LEAD = timedelta(hours=24)
MESSAGE_INTERVAL = timedelta(days=7)

REENGAGEMENT_MESSAGE = (
    "We miss you! New activities are coming up this week. Check the group's events and come say hi."
)


def weekly_attendance(
    db: Session, group_ids: list[int], now: datetime, weeks: int = DETECTION_WEEKS
) -> dict[int, list[int]]:
    """Attendance count per group per week, oldest -> newest."""
    start = now - weeks * WEEK
    series = {gid: [0] * weeks for gid in group_ids}
    rows = db.execute(
        select(Event.group_id, Event.starts_at, func.count(Attendance.id))
        .join(Attendance, Attendance.event_id == Event.id)
        .where(Event.group_id.in_(group_ids), Event.starts_at > start, Event.starts_at < now)
        .group_by(Event.group_id, Event.id)
    ).all()
    for group_id, starts_at, count in rows:
        weeks_ago = int((now - starts_at) / WEEK)
        series[group_id][weeks - 1 - weeks_ago] += count
    return series


def is_declining(series: list[int]) -> tuple[bool, float, float]:
    """``(declining, baseline_mean, decline_pct)`` for an 8-week series.

    Declining = the 4 most recent weeks are each below the previous 4-week average,
    and their average is more than 30% lower.
    """
    baseline_weeks, recent_weeks = series[:4], series[4:]
    baseline = sum(baseline_weeks) / 4
    recent = sum(recent_weeks) / 4
    if baseline < MIN_BASELINE_WEEKLY_ATTENDANCE:
        return False, baseline, 0.0
    decline = 1 - recent / baseline
    consecutive = all(week < baseline for week in recent_weeks)
    return consecutive and decline > DECLINE_THRESHOLD, baseline, round(decline, 4)


def detect_declines(db: Session, now: datetime | None = None) -> list[ReengagementCase]:
    """Open a case (with a randomly assigned arm) for each newly declining group."""
    now = now or datetime.now(UTC)
    open_cases = set(
        db.scalars(
            select(ReengagementCase.group_id).where(
                ReengagementCase.status == ReengagementStatus.ACTIVE
            )
        )
    )
    group_ids = [
        gid
        for gid in db.scalars(select(StudentGroup.id).where(StudentGroup.is_active))
        if gid not in open_cases
    ]
    series_by_group = weekly_attendance(db, group_ids, now)
    arm_counts = Counter(db.scalars(select(ReengagementCase.arm)))

    created = []
    for group_id, series in series_by_group.items():
        declining, baseline, decline = is_declining(series)
        if not declining:
            continue
        arm = _assign_arm(group_id, now, arm_counts)
        arm_counts[arm] += 1
        case = ReengagementCase(
            group_id=group_id,
            arm=arm,
            detected_at=now,
            evaluation_ends_at=now + EVALUATION_PERIOD,
            weekly_attendance=series,
            baseline_weekly_attendance=round(baseline, 2),
            decline_pct=decline,
            lapsed_user_ids=lapsed_members(db, group_id, now),
        )
        db.add(case)
        created.append(case)
    db.commit()
    return created


def _assign_arm(group_id: int, now: datetime, counts: Counter) -> ReengagementArm:
    """Balanced randomization: the arm with fewer groups, ties broken by a hash coin."""
    reminders, messages = (
        counts[ReengagementArm.EVENT_REMINDERS],
        counts[ReengagementArm.GROUP_MESSAGES],
    )
    if reminders != messages:
        return (
            ReengagementArm.EVENT_REMINDERS
            if reminders < messages
            else ReengagementArm.GROUP_MESSAGES
        )
    coin = hashlib.sha256(f"{group_id}:{now:%Y%m%d}".encode()).digest()[0] % 2
    return ReengagementArm.EVENT_REMINDERS if coin == 0 else ReengagementArm.GROUP_MESSAGES


def lapsed_members(db: Session, group_id: int, now: datetime) -> list[int]:
    """Active members who attended in the baseline weeks but not in the recent 4 weeks."""
    recent_start = now - 4 * WEEK
    baseline_start = now - DETECTION_WEEKS * WEEK

    def attendees(start: datetime, end: datetime) -> set[int]:
        return set(
            db.scalars(
                select(Attendance.user_id)
                .join(Event, Event.id == Attendance.event_id)
                .where(Event.group_id == group_id, Event.starts_at >= start, Event.starts_at < end)
            )
        )

    members = set(notifications.active_member_ids(db, group_id))
    lapsed = attendees(baseline_start, recent_start) - attendees(recent_start, now)
    return sorted(lapsed & members)


# --- Interventions ---------------------------------------------------------------------


def send_event_reminders(db: Session, now: datetime | None = None) -> int:
    """Reminder arm: remind lapsed members about events starting within 24 hours."""
    now = now or datetime.now(UTC)
    sent = 0
    for case in _active_cases(db, ReengagementArm.EVENT_REMINDERS):
        events = db.scalars(
            select(Event).where(
                Event.group_id == case.group_id,
                ~Event.is_cancelled,
                Event.starts_at > now,
                Event.starts_at <= now + REMINDER_LEAD,
            )
        ).all()
        for event in events:
            already = set(
                db.scalars(
                    select(Notification.user_id).where(
                        Notification.event_id == event.id,
                        Notification.type == NotificationType.EVENT_REMINDER,
                    )
                )
            )
            recipients = [uid for uid in case.lapsed_user_ids if uid not in already]
            created = notifications.notify(
                db,
                recipients,
                NotificationType.EVENT_REMINDER,
                title=f"Coming up: {event.title}",
                body=(
                    f"{event.group.name} meets {event.starts_at.astimezone(campus_tz()):%a %H:%M}."
                    " We'd love to see you!"
                ),
                group_id=event.group_id,
                event_id=event.id,
                data={"group_id": event.group_id, "event_id": event.id, "reengagement": True},
            )
            sent += len(created)
        case.last_intervention_at = now
    db.commit()
    return sent


def send_group_messages(db: Session, now: datetime | None = None) -> int:
    """Message arm: a weekly platform message in the group chat (members are notified)."""
    now = now or datetime.now(UTC)
    posted = 0
    for case in _active_cases(db, ReengagementArm.GROUP_MESSAGES):
        if case.last_intervention_at and now - case.last_intervention_at < MESSAGE_INTERVAL:
            continue
        group = db.get(StudentGroup, case.group_id)
        message_service.post_system_message(db, group, REENGAGEMENT_MESSAGE)
        case.last_intervention_at = now
        posted += 1
    db.commit()
    return posted


def evaluate_cases(db: Session, now: datetime | None = None) -> int:
    """Close cases whose evaluation period ended and record their return rate."""
    now = now or datetime.now(UTC)
    closed = 0
    for case in _active_cases(db):
        if case.evaluation_ends_at > now:
            continue
        returned = returned_members(db, case, until=case.evaluation_ends_at)
        case.returned_count = len(returned)
        case.return_rate = (
            round(len(returned) / len(case.lapsed_user_ids), 4) if case.lapsed_user_ids else None
        )
        case.status = ReengagementStatus.COMPLETED
        closed += 1
    db.commit()
    return closed


def returned_members(db: Session, case: ReengagementCase, until: datetime) -> set[int]:
    """Lapsed members who attended any of the group's events after detection."""
    if not case.lapsed_user_ids:
        return set()
    return set(
        db.scalars(
            select(Attendance.user_id)
            .join(Event, Event.id == Attendance.event_id)
            .where(
                Event.group_id == case.group_id,
                Event.starts_at >= case.detected_at,
                Event.starts_at < until,
                Attendance.user_id.in_(case.lapsed_user_ids),
            )
        )
    )


def case_summary(db: Session, case: ReengagementCase, now: datetime) -> dict[str, Any]:
    if case.status == ReengagementStatus.COMPLETED:
        returned_count, rate = case.returned_count or 0, case.return_rate
    else:
        returned = returned_members(db, case, until=now)
        returned_count = len(returned)
        rate = (
            round(returned_count / len(case.lapsed_user_ids), 4) if case.lapsed_user_ids else None
        )
    return {
        "case_id": case.id,
        "group_id": case.group_id,
        "arm": case.arm.value,
        "status": case.status.value,
        "detected_at": case.detected_at,
        "weekly_attendance": case.weekly_attendance,
        "baseline_weekly_attendance": case.baseline_weekly_attendance,
        "decline_pct": case.decline_pct,
        "lapsed_members": len(case.lapsed_user_ids),
        "returned_members": returned_count,
        "return_rate": rate,
    }


def _active_cases(db: Session, arm: ReengagementArm | None = None) -> list[ReengagementCase]:
    stmt = select(ReengagementCase).where(ReengagementCase.status == ReengagementStatus.ACTIVE)
    if arm is not None:
        stmt = stmt.where(ReengagementCase.arm == arm)
    return list(db.scalars(stmt))
