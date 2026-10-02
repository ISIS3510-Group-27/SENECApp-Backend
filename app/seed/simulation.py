"""Synthetic usage history (SEED_MODE=full).

Generates ~12 weeks of students, schedules, memberships, events, attendance,
notifications, chat, recommendation logs and analytics events, all relative to
"now" and fully deterministic (fixed random seed).

Patterns are planted on purpose so each business question has a clear answer,
while the data still flows through the same tables, recommender features and
re-engagement logic the real API uses.
"""

import logging
import math
import random
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AnalyticsEvent,
    Attendance,
    CampusBuilding,
    CheckInMethod,
    EntryPoint,
    Event,
    FeatureArea,
    GroupMessage,
    GroupSave,
    Interest,
    Membership,
    MembershipRole,
    MessageKind,
    Notification,
    NotificationType,
    PushStatus,
    RecommendationKind,
    RecommendationLog,
    ReengagementArm,
    Release,
    ScheduleBlock,
    SeedRun,
    StudentGroup,
    User,
    UserInterest,
)
from app.recommendations.model import (
    DEFAULT_MODEL_VERSION,
    DEFAULT_WEIGHTS,
    GroupProfile,
    StudentProfile,
    compute_features,
    score,
)
from app.services import reengagement
from app.services.recommendations import train_group_model
from app.services.schedule import campus_tz, free_blocks_for_day, is_free_at

logger = logging.getLogger(__name__)

SEED_RUN_NAME = "simulation"
RANDOM_SEED = 2026
STUDENTS = 300
WEEKS = 12
WEEK = timedelta(days=7)

DEMO_STUDENT = (
    "s.arango@uniandes.edu.co",
    "Sofía Arango",
    ["Tennis", "AI/ML", "Startups", "Travel", "Cars"],
)
DEMO_GROUPS = ["Tennis Uniandes", "Emprendedores Uniandes", "AI & Machine Learning"]
ADMIN_EMAIL = "admin@uniandes.edu.co"

# BQ10: groups whose attendance is made to decline, then respond to re-engagement.
DECLINING_GROUPS = {
    "Fotografía Uniandes",
    "Basket Andes",
    "Sociedad de Debate",
    "Cineclub Uniandes",
}
DECLINE_FACTOR = 0.4
# Per-event probability that a lapsed member comes back, by assigned arm.
RETURN_PROBABILITY = {ReengagementArm.EVENT_REMINDERS: 0.2, ReengagementArm.GROUP_MESSAGES: 0.07}

# Popularity of interests among students (unlisted interests weigh 1.0).
INTEREST_WEIGHTS = {
    "AI/ML": 3.2,
    "Startups": 2.6,
    "Football": 2.4,
    "Travel": 2.3,
    "Music": 2.2,
    "Software Development": 2.2,
    "Finance": 1.8,
    "Running": 1.7,
    "Photography": 1.6,
    "Cybersecurity": 1.6,
    "Climbing": 1.4,
    "Hiking": 1.4,
    "Data Science": 1.5,
    "Video Games": 1.5,
    "Dance": 1.3,
    "Model UN": 1.0,
    "Swimming": 0.9,
}
# Explore browsing preference by category (BQ13: tech & sports get the most views).
CATEGORY_VIEW_WEIGHTS = {
    "technology": 3.0,
    "sports": 2.6,
    "business": 2.0,
    "arts": 1.8,
    "social": 1.2,
    "travel": 1.2,
    "international": 0.9,
    "cars": 0.7,
}
# Arts groups get saved for later more often than they get joined (BQ13).
CATEGORY_SAVE_BOOST = {"arts": 1.8, "travel": 1.4}

# BQ12: how often each filter is used, and how often searches with it lead to an open.
FILTER_USAGE = {
    "categories": 0.55,
    "q": 0.45,
    "interest_ids": 0.35,
    "has_upcoming_events": 0.18,
    "verified": 0.1,
    "buildings": 0.06,
}
FILTER_OPEN_BOOST = {"categories": 1.35, "interest_ids": 1.3, "q": 1.0}

# BQ7: probability of continuing each funnel step (form -> submit is the leakiest).
SUBMIT_PROBABILITY = 0.22
FORM_BASE_BY_ENTRY = {
    EntryPoint.RECOMMENDATION: 0.55,
    EntryPoint.SEARCH: 0.42,
    EntryPoint.EXPLORE: 0.3,
    EntryPoint.NOTIFICATION: 0.38,
}

# BQ8: open / dismiss probabilities by notification type.
NOTIFICATION_BEHAVIOUR = {
    NotificationType.GROUP_MESSAGE: (0.48, 0.25),
    NotificationType.NEW_EVENT: (0.24, 0.35),
    NotificationType.GROUP_RECOMMENDATION: (0.09, 0.55),
    NotificationType.EVENT_REMINDER: (0.4, 0.3),
}

CLASS_SLOTS = [
    time(7),
    time(8, 30),
    time(10),
    time(11, 30),
    time(13),
    time(14, 30),
    time(16),
    time(17, 30),
]
CLASS_BUILDINGS = {"ML": 3, "SD": 2.5, "W": 2, "AU": 2, "LL": 1.5, "B": 1, "G": 1, "C": 1}
MEETING_TIMES = [time(12, 30), time(16), time(17, 30), time(18, 30), time(11, 30)]

# Telemetry --------------------------------------------------------------------------------
DEVICES = [
    # model, OS version, platform, load-time factor, weight
    ("Samsung Galaxy A10", "10", "android", 1.9, 1.2),
    ("Xiaomi Redmi 9A", "10", "android", 2.1, 1.0),
    ("Xiaomi Redmi Note 8", "11", "android", 1.5, 1.4),
    ("Motorola Moto G32", "12", "android", 1.3, 1.6),
    ("Samsung Galaxy A14", "13", "android", 1.2, 2.0),
    ("Google Pixel 7", "14", "android", 0.85, 1.2),
    ("Samsung Galaxy S23", "14", "android", 0.75, 1.0),
    ("iPhone 12", "17", "ios", 0.9, 0.8),
    ("iPhone 14", "18", "ios", 0.8, 0.6),
]
SCREEN_LOAD_MS = {
    "discover": 650,
    "explore": 800,
    "group_detail": 900,
    "join_form": 500,
    "events": 700,
    "event_detail": 600,
    "notifications": 450,
    "profile": 400,
    "recommendations": 1100,
    "free_now": 1300,
    "check_in_scanner": 1500,
    "chat": 750,
    "create_group": 550,
}
# Screens that are notably slower on old Android versions (BQ11).
OLD_OS_SLOW_SCREENS = {"free_now", "check_in_scanner", "group_detail"}
SCREEN_FEATURE = {
    "discover": "home",
    "explore": "explore",
    "group_detail": "group_profile",
    "join_form": "join",
    "events": "events",
    "event_detail": "events",
    "notifications": "notifications",
    "profile": "profile",
    "recommendations": "recommendations",
    "free_now": "free_now",
    "check_in_scanner": "check_in",
    "chat": "chat",
    "create_group": "create_group",
}
# Errors per screen view (BQ1: the camera-based check-in scanner is the most error-prone).
SCREEN_ERROR_RATE = {"check_in_scanner": 0.03, "free_now": 0.012, "chat": 0.008}
BASE_ERROR_RATE = 0.004
ERROR_TYPES = {
    "check_in_scanner": ["CameraPermissionDenied", "QrDecodeError", "CameraUnavailable"],
    "free_now": ["LocationTimeout", "NetworkError"],
    "chat": ["SocketClosed", "NetworkError"],
}

# BQ14: releases (weeks before now) and their effect on error rates.
RELEASES = [
    # app, version, weeks ago (None = before the window), feature area, notes
    ("flutter", "1.2.0", None, FeatureArea.OTHER, "Baseline release"),
    ("kotlin", "1.0.0", None, FeatureArea.OTHER, "Baseline release"),
    ("kotlin", "1.1.0", 10, FeatureArea.RECOMMENDATIONS, "New recommendation cards"),
    ("flutter", "1.3.0", 8, FeatureArea.RECOMMENDATIONS, "Recommender v2 + free-now suggestions"),
    ("flutter", "1.3.1", 7, FeatureArea.OTHER, "Hotfix for recommendations crash"),
    ("kotlin", "1.2.0", 5, FeatureArea.NOTIFICATIONS, "Notification inbox redesign"),
    ("flutter", "1.4.0", 3, FeatureArea.NOTIFICATIONS, "Push notification grouping"),
]
# (app, version) -> {screen: error multiplier}
RELEASE_ERROR_EFFECTS = {
    ("flutter", "1.3.0"): {"recommendations": 4.0, "discover": 2.5, "free_now": 3.0},
    ("kotlin", "1.1.0"): {"recommendations": 0.6},
    ("kotlin", "1.2.0"): {"notifications": 3.0},
    ("flutter", "1.4.0"): {"notifications": 0.8},
}

FIRST_NAMES = [
    "Valentina",
    "Santiago",
    "Mariana",
    "Sebastián",
    "Isabella",
    "Nicolás",
    "Sara",
    "Samuel",
    "Daniela",
    "Juan Pablo",
    "Gabriela",
    "Alejandro",
    "Laura",
    "Mateo",
    "Camila",
    "Andrés",
    "Paula",
    "Felipe",
    "Natalia",
    "David",
    "Juliana",
    "Tomás",
    "Manuela",
    "Esteban",
    "Luisa",
    "Diego",
    "Catalina",
    "Martín",
    "Ana María",
    "Simón",
    "Carolina",
    "Julián",
    "Antonia",
    "Emilio",
]
LAST_NAMES = [
    "Gómez",
    "Rodríguez",
    "Martínez",
    "López",
    "García",
    "Hernández",
    "Pérez",
    "Sánchez",
    "Ramírez",
    "Torres",
    "Díaz",
    "Vargas",
    "Rojas",
    "Moreno",
    "Castro",
    "Ortiz",
    "Jiménez",
    "Suárez",
    "Restrepo",
    "Ospina",
    "Mejía",
    "Cárdenas",
    "Salazar",
    "Arango",
    "Londoño",
    "Rincón",
]
PROGRAMS = [
    "Ingeniería de Sistemas",
    "Ingeniería Industrial",
    "Administración",
    "Economía",
    "Derecho",
    "Medicina",
    "Diseño",
    "Arquitectura",
    "Psicología",
    "Ingeniería Civil",
    "Ciencia Política",
    "Música",
    "Biología",
    "Matemáticas",
]
EVENT_TITLES = {
    "sports": ["Training session", "Friendly match", "Skills clinic", "Tournament day"],
    "business": ["Pitch night", "Case workshop", "Alumni panel", "Networking mixer"],
    "technology": ["Build night", "Workshop", "Paper reading", "Hackathon prep"],
    "arts": ["Rehearsal", "Open studio", "Screening", "Showcase"],
    "travel": ["Trip planning", "Photo night", "Day hike", "Culture talk"],
    "cars": ["Car meet", "Garage session", "Track-day briefing"],
    "social": ["Game night", "Volunteer day", "Debate practice", "Coffee meetup"],
    "international": ["Language tandem", "Exchange Q&A", "Cultural night"],
}
CHAT_LINES = [
    "See you all on Thursday!",
    "Does anyone have notes from last session?",
    "Photos from the event are up 📸",
    "Who's coming this week?",
    "Thanks for organizing!",
    "Reminder: bring your student ID",
    "Great session today 🙌",
    "Can someone share the link?",
]


@dataclass
class Student:
    user: User
    interest_ids: set[int]
    blocks: list[ScheduleBlock]
    app: str
    device: tuple[str, str, str, float, float]
    activity: float  # sessions per week
    engagement: float  # attendance propensity multiplier
    profile: StudentProfile | None = None


@dataclass
class GroupInfo:
    group: StudentGroup
    category: str
    tag_ids: set[int]
    meeting_building: CampusBuilding | None
    quality: float
    attendance_rate: float
    members: dict[int, datetime] = field(default_factory=dict)  # user_id -> joined_at
    events: list[Event] = field(default_factory=list)


class Simulation:
    def __init__(self, db: Session, now: datetime, students: int = STUDENTS) -> None:
        self.db = db
        self.now = now.replace(minute=0, second=0, microsecond=0)
        self.start = self.now - WEEKS * WEEK
        self.rng = random.Random(RANDOM_SEED)
        self.tz = campus_tz()
        self.student_count = students
        self.analytics: list[dict[str, Any]] = []
        self.rec_logs: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.saves: dict[tuple[int, int], datetime] = {}
        self.attendance: dict[tuple[int, int], datetime] = {}  # (event_id, user_id)
        self.join_entry: dict[tuple[int, int], EntryPoint] = {}
        self.admin_of: dict[int, int] = {}  # group_id -> admin user id
        self.messages: list[dict[str, Any]] = []

    # --- Orchestration -------------------------------------------------------------------

    def run(self) -> dict[str, int]:
        self._load_reference()
        self._create_students()
        self._create_initial_memberships()
        self._create_events()
        self._simulate_sessions()
        self._persist_memberships()
        self._simulate_attendance(until_weeks_ago=4)
        self._run_reengagement()
        self._simulate_notifications_and_chat()
        self._create_releases()
        self._flush_bulk()
        summary = train_group_model(self.db, self.now)
        logger.info(
            "Recommender trained on simulated logs: %s", summary.model_dump(exclude_none=True)
        )
        return {
            "students": len(self.students),
            "events": sum(len(g.events) for g in self.groups.values()),
            "analytics_events": self._analytics_count,
            "recommendation_logs": self._rec_count,
            "notifications": self._notification_count,
        }

    # --- Reference data --------------------------------------------------------------------

    def _load_reference(self) -> None:
        self.interests = {i.id: i for i in self.db.scalars(select(Interest))}
        self.interest_by_name = {i.name: i for i in self.interests.values()}
        self.buildings = {b.code: b for b in self.db.scalars(select(CampusBuilding))}
        groups = self.db.scalars(
            select(StudentGroup).options(
                selectinload(StudentGroup.tags),
                selectinload(StudentGroup.category),
                selectinload(StudentGroup.meeting_building),
            )
        ).all()
        self.groups: dict[int, GroupInfo] = {}
        for g in groups:
            quality = (
                0.55
                + 0.35 * (g.image_url is not None)
                + 0.25 * (g.instagram_url is not None)
                + 0.05 * (g.website_url is not None)
                + 0.05 * (g.contact_email is not None)
                + 0.1 * g.verified
            )
            self.groups[g.id] = GroupInfo(
                group=g,
                category=g.category.slug,
                tag_ids={t.id for t in g.tags},
                meeting_building=g.meeting_building,
                quality=quality,
                attendance_rate=self.rng.uniform(0.3, 0.55),
            )
        self.active_groups = [info for info in self.groups.values() if info.group.is_active]

    # --- Students ------------------------------------------------------------------------------

    def _create_students(self) -> None:
        interest_names = list(self.interest_by_name)
        weights = [INTEREST_WEIGHTS.get(name, 1.0) for name in interest_names]
        device_weights = [d[4] for d in DEVICES]
        used_emails: set[str] = {DEMO_STUDENT[0], ADMIN_EMAIL}

        people: list[tuple[str, str, list[str]]] = [DEMO_STUDENT]
        while len(people) < self.student_count:
            first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
            base = f"{_ascii(first.split()[0])[0]}.{_ascii(last)}".lower()
            email, n = f"{base}@uniandes.edu.co", 1
            while email in used_emails:
                n += 1
                email = f"{base}{n}@uniandes.edu.co"
            used_emails.add(email)
            chosen = _weighted_sample(self.rng, interest_names, weights, self.rng.randint(3, 6))
            people.append((email, f"{first} {last}", chosen))

        users = []
        for email, name, _ in people:
            users.append(
                User(
                    email=email,
                    full_name=name,
                    program=self.rng.choice(PROGRAMS),
                    semester=self.rng.randint(1, 10),
                    location_opt_in=self.rng.random() < 0.7 or email == DEMO_STUDENT[0],
                    notifications_opt_in=self.rng.random() < 0.88 or email == DEMO_STUDENT[0],
                    created_at=self.start - timedelta(days=self.rng.randint(1, 400)),
                )
            )
        users.append(User(email=ADMIN_EMAIL, full_name="SENECApp Admin"))
        self.db.add_all(users)
        self.db.flush()

        self.students: list[Student] = []
        for user, (_, _, interest_list) in zip(users, people, strict=False):
            interest_ids = {self.interest_by_name[name].id for name in interest_list}
            self.db.add_all(
                UserInterest(user_id=user.id, interest_id=iid, created_at=user.created_at)
                for iid in interest_ids
            )
            blocks = self._schedule_for(user.id)
            self.db.add_all(blocks)
            device = self.rng.choices(DEVICES, weights=device_weights)[0]
            app = "flutter" if device[2] == "ios" else self.rng.choice(["flutter", "kotlin"])
            self.students.append(
                Student(
                    user=user,
                    interest_ids=interest_ids,
                    blocks=blocks,
                    app=app,
                    device=device,
                    activity=min(5.0, self.rng.lognormvariate(0.5, 0.6)),
                    engagement=self.rng.uniform(0.6, 1.4),
                )
            )
        self.db.flush()
        self.demo = self.students[0]

    def _schedule_for(self, user_id: int) -> list[ScheduleBlock]:
        codes = list(CLASS_BUILDINGS)
        weights = list(CLASS_BUILDINGS.values())
        blocks = []
        for weekday in sorted(self.rng.sample(range(5), self.rng.randint(3, 5))):
            for slot in sorted(self.rng.sample(CLASS_SLOTS, self.rng.randint(1, 3))):
                end = (datetime.combine(date.today(), slot) + timedelta(minutes=80)).time()
                building = self.buildings[self.rng.choices(codes, weights=weights)[0]]
                blocks.append(
                    ScheduleBlock(
                        user_id=user_id,
                        weekday=weekday,
                        start_time=slot,
                        end_time=end,
                        title="Clase",
                        building_id=building.id,
                        building=building,
                    )
                )
        return blocks

    def _student_profile(self, student: Student) -> StudentProfile:
        if student.profile is None:
            categories: dict[int, int] = defaultdict(int)
            for iid in student.interest_ids:
                if self.interests[iid].category_id:
                    categories[self.interests[iid].category_id] += 1
            locations = {(b.building.latitude, b.building.longitude) for b in student.blocks}
            blocks = student.blocks
            student.profile = StudentProfile(
                interest_ids=frozenset(student.interest_ids),
                category_counts=dict(categories),
                class_locations=tuple(sorted(locations)),
                is_free_at=lambda moment: is_free_at(blocks, moment),
            )
        return student.profile

    # --- Memberships -----------------------------------------------------------------------

    def _create_initial_memberships(self) -> None:
        """Members who joined before the simulated window."""
        for info in self.active_groups:
            for student in self.students[1:]:
                match = _overlap(student.interest_ids, info.tag_ids)
                if self.rng.random() < 0.02 + 0.3 * match:
                    info.members[student.user.id] = self.start - timedelta(
                        days=self.rng.randint(14, 400)
                    )
            if info.members:
                self.admin_of[info.group.id] = next(iter(info.members))
        for name in DEMO_GROUPS:
            info = self._group_named(name)
            info.members[self.demo.user.id] = self.start - timedelta(days=200)
        self.admin_of[self._group_named("AI & Machine Learning").group.id] = self.demo.user.id

    def _persist_memberships(self) -> None:
        rows = []
        for info in self.groups.values():
            for user_id, joined_at in info.members.items():
                rows.append(
                    {
                        "user_id": user_id,
                        "group_id": info.group.id,
                        "joined_at": joined_at,
                        "role": (
                            MembershipRole.ADMIN
                            if self.admin_of.get(info.group.id) == user_id
                            else MembershipRole.MEMBER
                        ),
                        "entry_point": self.join_entry.get((user_id, info.group.id)),
                    }
                )
        _bulk_insert(self.db, Membership, rows)
        _bulk_insert(
            self.db,
            GroupSave,
            [{"user_id": u, "group_id": g, "created_at": t} for (u, g), t in self.saves.items()],
        )
        self.db.flush()

    # --- Events ----------------------------------------------------------------------------

    def _create_events(self) -> None:
        central = [self.buildings[c] for c in ("ML", "SD", "W", "AU", "C")]
        for info in self.active_groups:
            weekday = self.rng.randint(0, 4)
            start_time = self.rng.choice(MEETING_TIMES)
            building = info.meeting_building or self.rng.choice(central)
            titles = EVENT_TITLES[info.category]
            first_day = (self.start - timedelta(days=self.start.weekday())).date()
            for week in range(WEEKS + 3):
                day = first_day + timedelta(days=7 * week + weekday)
                starts_at = datetime.combine(day, start_time, tzinfo=self.tz).astimezone(UTC)
                if not self.start <= starts_at <= self.now + 2 * WEEK:
                    continue
                info.events.append(
                    Event(
                        group_id=info.group.id,
                        title=f"{self.rng.choice(titles)} · {info.group.name}",
                        starts_at=starts_at,
                        ends_at=starts_at + timedelta(minutes=self.rng.choice([90, 120])),
                        building_id=building.id,
                        building=building,
                        location_detail=f"Salón {self.rng.randint(100, 499)}",
                        created_at=starts_at - WEEK,
                    )
                )
        # Short lunch-time drop-in events on weekdays (free-block suggestions, BQ3).
        rotation = [g for g in self.active_groups if g.group.name not in DECLINING_GROUPS]
        day = self.start.astimezone(self.tz).date()
        while datetime.combine(day, time(0), tzinfo=self.tz) < self.now + 2 * WEEK:
            if day.weekday() < 5:
                for clock in (time(12, 15), time(16, 10)):
                    info = self.rng.choice(rotation)
                    building = self.rng.choice(central)
                    starts_at = datetime.combine(day, clock, tzinfo=self.tz).astimezone(UTC)
                    if self.start <= starts_at:
                        info.events.append(
                            Event(
                                group_id=info.group.id,
                                title=f"Drop-in · {info.group.name}",
                                starts_at=starts_at,
                                ends_at=starts_at + timedelta(minutes=50),
                                building_id=building.id,
                                building=building,
                                location_detail="Hall",
                                created_at=starts_at - timedelta(days=3),
                            )
                        )
            day += timedelta(days=1)
        self.db.add_all(e for info in self.groups.values() for e in info.events)
        self.db.flush()
        self.events_by_id = {e.id: e for info in self.groups.values() for e in info.events}

    # --- Sessions (discovery, search, recommendations, telemetry) ------------------------

    def _simulate_sessions(self) -> None:
        max_members = max((len(g.members) for g in self.active_groups), default=1)
        self.group_profiles = {
            info.group.id: GroupProfile(
                group_id=info.group.id,
                category_id=info.group.category_id,
                tag_ids=frozenset(info.tag_ids),
                meeting_location=(
                    (info.meeting_building.latitude, info.meeting_building.longitude)
                    if info.meeting_building
                    else None
                ),
                event_times=tuple(e.starts_at for e in info.events[:8]),
                member_count=len(info.members),
                verified=info.group.verified,
            )
            for info in self.active_groups
        }
        self.max_members = max_members
        for student in self.students:
            for week in range(WEEKS):
                sessions = _poisson(self.rng, student.activity)
                for _ in range(sessions):
                    moment = self._session_time(week)
                    if moment < self.now:
                        self._session(student, moment)

    def _session_time(self, week: int) -> datetime:
        day = self.start + timedelta(days=7 * week + self.rng.randint(0, 6))
        hour = self.rng.choices(
            range(7, 23), weights=[1, 2, 3, 3, 4, 5, 5, 4, 4, 4, 4, 5, 5, 4, 3, 2]
        )[0]
        local = datetime.combine(
            day.astimezone(self.tz).date(), time(hour, self.rng.randint(0, 59)), tzinfo=self.tz
        )
        return local.astimezone(UTC)

    def _session(self, student: Student, moment: datetime) -> None:
        ctx = self._context(student, moment)
        clock = [moment]

        def tick(seconds: tuple[int, int] = (5, 90)) -> datetime:
            clock[0] += timedelta(seconds=self.rng.randint(*seconds))
            return clock[0]

        self._screen(ctx, "discover", tick())
        if self.rng.random() < 0.45:
            self._recommendations(student, ctx, tick)
        if self.rng.random() < 0.55:
            self._explore(student, ctx, tick)
        if self.rng.random() < 0.35:
            self._search(student, ctx, tick)
        local = moment.astimezone(self.tz)
        if local.weekday() < 5 and 8 <= local.hour < 19 and self.rng.random() < 0.35:
            self._free_now(student, ctx, tick)
        for screen, probability in (("events", 0.3), ("notifications", 0.3), ("profile", 0.12)):
            if self.rng.random() < probability:
                self._screen(ctx, screen, tick())
        if self.rng.random() < 0.2 and any(
            student.user.id in g.members for g in self.active_groups
        ):
            self._screen(ctx, "chat", tick())

    def _context(self, student: Student, moment: datetime) -> dict[str, Any]:
        model, os_version, platform, speed, _ = student.device
        return {
            "user_id": student.user.id,
            "session_id": uuid.uuid4().hex,
            "app": student.app,
            "app_version": self._version_at(student, moment),
            "platform": platform,
            "device_model": model,
            "os_version": os_version,
            "speed": speed,
        }

    def _version_at(self, student: Student, moment: datetime) -> str:
        version = "1.2.0" if student.app == "flutter" else "1.0.0"
        # Each student updates a few days after a release (deterministic per student).
        lag = timedelta(days=student.user.id % 4)
        for app, candidate, weeks_ago, _, _ in RELEASES:
            if app == student.app and weeks_ago is not None:
                if moment >= self.now - weeks_ago * WEEK + lag:
                    version = candidate
        return version

    def _event(
        self,
        ctx: dict[str, Any],
        name: str,
        at: datetime,
        *,
        source: str,
        screen: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        self.analytics.append(
            {
                "event_id": uuid.uuid4(),
                "name": name,
                "source": source,
                "user_id": ctx["user_id"],
                "session_id": ctx["session_id"],
                "app": ctx["app"],
                "app_version": ctx["app_version"],
                "platform": ctx["platform"],
                "device_model": ctx["device_model"],
                "os_version": ctx["os_version"],
                "screen": screen,
                "occurred_at": at,
                "received_at": at + timedelta(seconds=self.rng.randint(1, 600)),
                "properties": properties or {},
            }
        )

    def _screen(self, ctx: dict[str, Any], screen: str, at: datetime) -> None:
        load = SCREEN_LOAD_MS[screen] * ctx["speed"] * self.rng.lognormvariate(0, 0.25)
        if ctx["os_version"] in ("10", "11") and screen in OLD_OS_SLOW_SCREENS:
            load *= 1.4
        self._event(
            ctx,
            "screen_view",
            at,
            source="client",
            screen=screen,
            properties={"load_time_ms": round(load)},
        )
        rate = SCREEN_ERROR_RATE.get(screen, BASE_ERROR_RATE)
        rate *= RELEASE_ERROR_EFFECTS.get((ctx["app"], ctx["app_version"]), {}).get(screen, 1.0)
        if self.rng.random() < rate:
            self._event(
                ctx,
                "app_error",
                at + timedelta(seconds=self.rng.randint(1, 20)),
                source="client",
                screen=screen,
                properties={
                    "feature": SCREEN_FEATURE[screen],
                    "error_type": self.rng.choice(
                        ERROR_TYPES.get(screen, ["NetworkError", "StateError", "NullPointer"])
                    ),
                    "fatal": self.rng.random() < 0.25,
                },
            )

    def _features(self, student: Student, info: GroupInfo) -> dict[str, float]:
        return compute_features(
            self._student_profile(student), self.group_profiles[info.group.id], self.max_members
        )

    def _recommendations(self, student: Student, ctx: dict[str, Any], tick) -> None:
        at = tick()
        self._screen(ctx, "recommendations", at)
        candidates = [g for g in self.active_groups if student.user.id not in g.members]
        scored = sorted(
            ((score(self._features(student, g), DEFAULT_WEIGHTS), g) for g in candidates),
            key=lambda pair: -pair[0],
        )[:5]
        request_id = uuid.uuid4()
        for rank, (value, info) in enumerate(scored, start=1):
            features = self._features(student, info)
            self.rec_logs.append(
                {
                    "request_id": request_id,
                    "user_id": student.user.id,
                    "kind": RecommendationKind.GROUP,
                    "item_id": info.group.id,
                    "rank": rank,
                    "score": round(value, 4),
                    "features": features,
                    "context": {"app": ctx["app"]},
                    "model_version": DEFAULT_MODEL_VERSION,
                    "created_at": at,
                }
            )
            # Real join drivers: interests, then schedule fit and proximity.
            p_open = (
                0.04
                + 0.4 * features["interest_match"]
                + 0.15 * features["schedule_fit"]
                + 0.15 * features["proximity"]
            )
            if self.rng.random() < p_open:
                self._view_group(
                    student, info, ctx, tick, EntryPoint.RECOMMENDATION, str(request_id), features
                )

    def _explore(self, student: Student, ctx: dict[str, Any], tick) -> None:
        self._screen(ctx, "explore", tick())
        weights = [CATEGORY_VIEW_WEIGHTS[g.category] for g in self.active_groups]
        for info in _weighted_sample(self.rng, self.active_groups, weights, self.rng.randint(1, 3)):
            if (
                self.rng.random()
                < 0.06 * CATEGORY_SAVE_BOOST.get(info.category, 1.0) * info.quality
            ):
                self._save(student, info, ctx, tick(), source="explore")
            self._view_group(student, info, ctx, tick, EntryPoint.EXPLORE)

    def _search(self, student: Student, ctx: dict[str, Any], tick) -> None:
        filters = [name for name, p in FILTER_USAGE.items() if self.rng.random() < p] or ["q"]
        my_interests = list(student.interest_ids) or list(self.interests)
        interest_id = self.rng.choice(my_interests)
        interest = self.interests[interest_id]
        category = next(
            (g.category for g in self.active_groups if g.group.category_id == interest.category_id),
            None,
        )
        results = [
            g
            for g in self.active_groups
            if ("interest_ids" not in filters or interest_id in g.tag_ids)
            and ("categories" not in filters or g.category == category)
            and ("q" not in filters or interest_id in g.tag_ids or g.category == category)
            and ("verified" not in filters or g.group.verified)
        ]
        at = tick()
        self._event(
            ctx,
            "group_searched",
            at,
            source="server",
            screen="explore",
            properties={
                "query": interest.name if "q" in filters else None,
                "filters": filters,
                "categories": [category] if "categories" in filters and category else [],
                "interest_ids": [interest_id] if "interest_ids" in filters else [],
                "verified": True if "verified" in filters else None,
                "has_upcoming_events": True if "has_upcoming_events" in filters else None,
                "buildings": [],
                "result_count": len(results),
                "result_group_ids": [g.group.id for g in results[:20]],
            },
        )
        boost = max(FILTER_OPEN_BOOST.get(f, 0.8) for f in filters)
        for info in results[:3]:
            if self.rng.random() < 0.3 * boost:
                self._view_group(student, info, ctx, tick, EntryPoint.SEARCH)

    def _view_group(
        self,
        student: Student,
        info: GroupInfo,
        ctx: dict[str, Any],
        tick,
        entry_point: EntryPoint,
        rec_request_id: str | None = None,
        features: dict[str, float] | None = None,
    ) -> None:
        at = tick()
        group = info.group
        is_member = student.user.id in info.members
        self._screen(ctx, "group_detail", at)
        self._event(
            ctx,
            "group_viewed",
            at,
            source="server",
            screen="group_detail",
            properties={
                "group_id": group.id,
                "category": info.category,
                "entry_point": entry_point.value,
                "rec_request_id": rec_request_id,
                "is_member": is_member,
                "profile": {
                    "has_image": group.image_url is not None,
                    "has_contact_email": group.contact_email is not None,
                    "has_instagram": group.instagram_url is not None,
                    "has_website": group.website_url is not None,
                    "has_meeting_building": group.meeting_building_id is not None,
                    "has_founded_year": group.founded_year is not None,
                    "has_upcoming_event": True,
                    "verified": group.verified,
                    "description_length": len(group.description),
                    "tag_count": len(info.tag_ids),
                },
            },
        )
        if is_member:
            return
        features = features or self._features(student, info)
        match = 0.4 + 1.2 * features["interest_match"]
        feasibility = (0.4 + 0.8 * features["schedule_fit"]) * (0.6 + 0.8 * features["proximity"])
        if self.rng.random() < 0.1 * info.quality * match * CATEGORY_SAVE_BOOST.get(
            info.category, 1
        ):
            self._save(student, info, ctx, tick(), source="group_detail")
        p_form = FORM_BASE_BY_ENTRY[entry_point] * info.quality * match * feasibility
        if group.name in DECLINING_GROUPS:
            p_form *= 0.2  # keeps their membership flat so the planted decline is visible
        if self.rng.random() >= min(0.95, p_form):
            return
        attempt = uuid.uuid4().hex
        at = tick((10, 60))
        self._screen(ctx, "join_form", at)
        self._event(
            ctx,
            "join_form_opened",
            at,
            source="client",
            screen="join_form",
            properties={"group_id": group.id, "join_attempt_id": attempt},
        )
        if self.rng.random() >= SUBMIT_PROBABILITY * min(1.5, match):
            return
        at = tick((30, 240))
        info.members[student.user.id] = at
        self.join_entry[(student.user.id, group.id)] = entry_point
        self._event(
            ctx,
            "group_joined",
            at,
            source="server",
            screen="join_form",
            properties={
                "group_id": group.id,
                "category": info.category,
                "entry_point": entry_point.value,
                "rec_request_id": rec_request_id,
                "join_attempt_id": attempt,
                "has_motivation": self.rng.random() < 0.6,
            },
        )

    def _save(
        self, student: Student, info: GroupInfo, ctx: dict[str, Any], at: datetime, source: str
    ) -> None:
        key = (student.user.id, info.group.id)
        if key in self.saves:
            return
        self.saves[key] = at
        self._event(
            ctx,
            "group_saved",
            at,
            source="server",
            properties={"group_id": info.group.id, "category": info.category, "source": source},
        )

    def _free_now(self, student: Student, ctx: dict[str, Any], tick) -> None:
        at = tick()
        self._screen(ctx, "free_now", at)
        local = at.astimezone(self.tz)
        free = next(
            (
                b
                for b in free_blocks_for_day(student.blocks, local.date(), self.tz)
                if b.starts_at <= local < b.ends_at
            ),
            None,
        )
        if free is None:
            return
        building = (
            next(
                (b.building for b in student.blocks if b.building_id == free.previous_building_id),
                None,
            )
            or self.buildings["ML"]
        )
        window_end = free.ends_at - timedelta(minutes=20)
        candidates = [
            e
            for info in self.active_groups
            for e in info.events
            if at - timedelta(minutes=30) <= e.starts_at <= window_end and e.ends_at > at
        ]
        if not candidates:
            return
        request_id = uuid.uuid4()
        context = {
            "weekday": local.weekday(),
            "hour": local.hour,
            "building_code": building.code,
            "location_source": "gps" if student.user.location_opt_in else "schedule",
            "on_campus": True,
            "free_block_minutes": int((free.ends_at - local).total_seconds() // 60),
            "schedule_known": True,
        }
        ranked = sorted(
            candidates,
            key=lambda e: (
                _distance(building, e.building)
                - 300 * _overlap(student.interest_ids, self.groups[e.group_id].tag_ids)
            ),
        )[:5]
        for rank, event in enumerate(ranked, start=1):
            distance = _distance(building, event.building)
            self.rec_logs.append(
                {
                    "request_id": request_id,
                    "user_id": student.user.id,
                    "kind": RecommendationKind.EVENT,
                    "item_id": event.id,
                    "rank": rank,
                    "score": round(math.exp(-distance / 300), 4),
                    "features": {
                        "distance_m": round(distance, 1),
                        "interest_match": 0.0,
                        "minutes_until_start": max(
                            0, round((event.starts_at - at).total_seconds() / 60)
                        ),
                        "is_member": student.user.id in self.groups[event.group_id].members,
                    },
                    "context": context,
                    "model_version": "free-now-v1",
                    "created_at": at,
                }
            )
            # BQ3: lunch time and central buildings convert best; early morning worst.
            p = 0.1
            if 12 <= local.hour < 14:
                p *= 2.6
            elif local.hour < 9:
                p *= 0.4
            if building.code in ("ML", "SD"):
                p *= 1.7
            p *= math.exp(-distance / 600)
            if self.rng.random() < p:
                view_at = tick((5, 40))
                self._screen(ctx, "event_detail", view_at)
                self._event(
                    ctx,
                    "event_viewed",
                    view_at,
                    source="server",
                    screen="event_detail",
                    properties={
                        "event_id": event.id,
                        "group_id": event.group_id,
                        "entry_point": "free_now",
                        "rec_request_id": str(request_id),
                    },
                )
                if (
                    event.starts_at < self.now
                    and self.rng.random() < 0.4
                    and self.groups[event.group_id].group.name not in DECLINING_GROUPS
                ):
                    self._check_in(student, event, ctx)

    def _check_in(self, student: Student, event: Event, ctx: dict[str, Any]) -> None:
        key = (event.id, student.user.id)
        if key in self.attendance:
            return
        at = event.starts_at + timedelta(minutes=self.rng.randint(-10, 25))
        self.attendance[key] = at
        if ctx is not None:
            self._screen(ctx, "check_in_scanner", at - timedelta(seconds=20))
            self._event(
                ctx,
                "event_checked_in",
                at,
                source="server",
                screen="check_in_scanner",
                properties={
                    "event_id": event.id,
                    "group_id": event.group_id,
                    "method": "qr",
                    "distance_m": round(self.rng.uniform(5, 120), 1),
                },
            )

    # --- Attendance & re-engagement ---------------------------------------------------------

    def _attends(self, student: Student, info: GroupInfo, event: Event, factor: float) -> bool:
        joined = info.members.get(student.user.id)
        if joined is None or joined > event.starts_at:
            return False
        free = is_free_at(student.blocks, event.starts_at)
        p = info.attendance_rate * student.engagement * factor * (1.0 if free else 0.15)
        return self.rng.random() < p

    def _simulate_attendance(self, until_weeks_ago: int) -> None:
        """Attendance for events older than ``until_weeks_ago`` weeks."""
        cutoff = self.now - until_weeks_ago * WEEK
        students = {s.user.id: s for s in self.students}
        for info in self.active_groups:
            declining = info.group.name in DECLINING_GROUPS
            for event in info.events:
                if event.starts_at >= cutoff:
                    continue
                weeks_ago = (self.now - event.starts_at) / WEEK
                factor = DECLINE_FACTOR if declining and weeks_ago <= 8 else 1.0
                for user_id in list(info.members):
                    if self._attends(students[user_id], info, event, factor):
                        self._check_in(students[user_id], event, None)
        self._insert_attendance()

    def _insert_attendance(self) -> None:
        _bulk_insert(
            self.db,
            Attendance,
            [
                {"event_id": e, "user_id": u, "checked_in_at": t, "method": CheckInMethod.QR}
                for (e, u), t in self.attendance.items()
            ],
        )
        self.db.flush()
        self._inserted_attendance = set(self.attendance)

    def _run_reengagement(self) -> None:
        """Detect declines 4 weeks ago with the real logic, then simulate the response."""
        detected_at = self.now - 4 * WEEK
        cases = reengagement.detect_declines(self.db, detected_at)
        logger.info("Simulation: %d declining groups detected", len(cases))
        students = {s.user.id: s for s in self.students}
        case_by_group = {c.group_id: c for c in cases}

        for info in self.active_groups:
            case = case_by_group.get(info.group.id)
            for event in info.events:
                if not detected_at <= event.starts_at < self.now:
                    continue
                if case is None:
                    for user_id in list(info.members):
                        if self._attends(students[user_id], info, event, 1.0):
                            self._check_in(students[user_id], event, None)
                    continue
                lapsed = set(case.lapsed_user_ids)
                for user_id in list(info.members):
                    student = students[user_id]
                    if user_id in lapsed:
                        if self.rng.random() < RETURN_PROBABILITY[case.arm] * student.engagement:
                            self._check_in(student, event, None)
                    elif self._attends(student, info, event, DECLINE_FACTOR):
                        self._check_in(student, event, None)
                if case.arm == ReengagementArm.EVENT_REMINDERS:
                    self._notify(
                        lapsed,
                        NotificationType.EVENT_REMINDER,
                        f"Coming up: {event.title}",
                        "We'd love to see you!",
                        event.starts_at - timedelta(hours=3),
                        group_id=info.group.id,
                        event_id=event.id,
                    )
            if case is not None and case.arm == ReengagementArm.GROUP_MESSAGES:
                for week in range(4):
                    at = detected_at + week * WEEK + timedelta(hours=10)
                    self._message(
                        info, None, reengagement.REENGAGEMENT_MESSAGE, at, MessageKind.SYSTEM
                    )

        new_rows = {k: v for k, v in self.attendance.items() if k not in self._inserted_attendance}
        _bulk_insert(
            self.db,
            Attendance,
            [
                {"event_id": e, "user_id": u, "checked_in_at": t, "method": CheckInMethod.QR}
                for (e, u), t in new_rows.items()
            ],
        )
        self.db.flush()
        reengagement.evaluate_cases(self.db, self.now)

    # --- Notifications & chat -----------------------------------------------------------------

    def _notify(
        self,
        user_ids: Iterable[int],
        type_: NotificationType,
        title: str,
        body: str,
        at: datetime,
        group_id: int | None = None,
        event_id: int | None = None,
    ) -> None:
        if at >= self.now:
            return
        opted_in = self._opted_in
        open_p, dismiss_p = NOTIFICATION_BEHAVIOUR[type_]
        for user_id in user_ids:
            if user_id not in opted_in:
                continue
            opened = dismissed = None
            roll = self.rng.random()
            if roll < open_p:
                opened = min(self.now, at + timedelta(minutes=self.rng.lognormvariate(3, 1.2)))
            elif roll < open_p + dismiss_p:
                dismissed = min(self.now, at + timedelta(minutes=self.rng.lognormvariate(4, 1)))
            self.notifications.append(
                {
                    "user_id": user_id,
                    "type": type_,
                    "title": title,
                    "body": body,
                    "data": {"group_id": group_id, "event_id": event_id},
                    "group_id": group_id,
                    "event_id": event_id,
                    "push_status": PushStatus.SKIPPED,
                    "created_at": at,
                    "opened_at": opened,
                    "dismissed_at": dismissed,
                }
            )

    def _message(
        self, info: GroupInfo, author: int | None, body: str, at: datetime, kind: MessageKind
    ) -> None:
        if at >= self.now:
            return
        self.messages.append(
            {
                "group_id": info.group.id,
                "author_id": author,
                "body": body,
                "kind": kind,
                "created_at": at,
            }
        )
        recipients = [u for u, joined in info.members.items() if joined <= at and u != author]
        self._notify(
            recipients,
            NotificationType.GROUP_MESSAGE,
            info.group.name,
            body[:120],
            at,
            group_id=info.group.id,
        )

    @property
    def _opted_in(self) -> set[int]:
        if not hasattr(self, "_opted_in_cache"):
            self._opted_in_cache = {s.user.id for s in self.students if s.user.notifications_opt_in}
        return self._opted_in_cache

    def _simulate_notifications_and_chat(self) -> None:
        for info in self.active_groups:
            for event in info.events:
                members = [u for u, joined in info.members.items() if joined <= event.created_at]
                self._notify(
                    members,
                    NotificationType.NEW_EVENT,
                    f"New event from {info.group.name}",
                    event.title,
                    event.created_at,
                    group_id=info.group.id,
                    event_id=event.id,
                )
            for week in range(WEEKS):
                for _ in range(_poisson(self.rng, 1.5)):
                    at = (
                        self.start
                        + week * WEEK
                        + timedelta(minutes=self.rng.randint(0, 7 * 24 * 60))
                    )
                    authors = [u for u, joined in info.members.items() if joined <= at]
                    if authors:
                        self._message(
                            info,
                            self.rng.choice(authors),
                            self.rng.choice(CHAT_LINES),
                            at,
                            MessageKind.USER,
                        )
        # "A new group you might like": periodic suggestions to students with matching interests.
        for week in range(WEEKS):
            for info in self.rng.sample(self.active_groups, 2):
                at = self.start + week * WEEK + timedelta(days=self.rng.randint(0, 6), hours=10)
                fans = [
                    s.user.id
                    for s in self.students
                    if s.interest_ids & info.tag_ids and s.user.id not in info.members
                ]
                self._notify(
                    self.rng.sample(fans, min(40, len(fans))),
                    NotificationType.GROUP_RECOMMENDATION,
                    "A new group you might like",
                    f"Check out {info.group.name}",
                    at,
                    group_id=info.group.id,
                )

    def _create_releases(self) -> None:
        for app, version, weeks_ago, area, notes in RELEASES:
            released_at = self.now - (weeks_ago * WEEK if weeks_ago is not None else 16 * WEEK)
            self.db.add(
                Release(
                    app=app,
                    version=version,
                    released_at=released_at,
                    feature_area=area,
                    notes=notes,
                )
            )

    # --- Persistence -----------------------------------------------------------------------------

    def _flush_bulk(self) -> None:
        self._analytics_count = len(self.analytics)
        self._rec_count = len(self.rec_logs)
        self._notification_count = len(self.notifications)
        for model, rows in (
            (AnalyticsEvent, self.analytics),
            (RecommendationLog, self.rec_logs),
            (Notification, self.notifications),
            (GroupMessage, self.messages),
        ):
            _bulk_insert(self.db, model, rows)
        self.db.flush()

    def _group_named(self, name: str) -> GroupInfo:
        return next(info for info in self.groups.values() if info.group.name == name)


# --- Entry point -----------------------------------------------------------------------------


def seed_simulation(db: Session, now: datetime | None = None, students: int = STUDENTS) -> bool:
    """Generate the synthetic history once. Returns False if it already exists."""
    if db.get(SeedRun, SEED_RUN_NAME) is not None:
        logger.info("Simulation already seeded; skipping (use --reset to regenerate)")
        return False
    now = now or datetime.now(UTC)
    logger.info("Simulating %d weeks of usage for %d students...", WEEKS, students)
    summary = Simulation(db, now, students).run()
    db.add(SeedRun(name=SEED_RUN_NAME, completed_at=datetime.now(UTC), details=summary))
    db.commit()
    logger.info("Simulation seeded: %s", summary)
    return True


# --- Helpers -----------------------------------------------------------------------------------


def _bulk_insert(db: Session, model: type, rows: list[dict[str, Any]], chunk: int = 1_000) -> None:
    """Insert rows as multi-row ``INSERT ... VALUES`` statements.

    Much faster than ORM bulk inserts (which send one statement per row through
    ``executemany``) when the database runs in Docker.
    """
    table = model.__table__
    for start in range(0, len(rows), chunk):
        db.execute(insert(table).values(rows[start : start + chunk]))


def _weighted_sample[T](
    rng: random.Random, items: list[T], weights: list[float], k: int
) -> list[T]:
    """Sample ``k`` distinct items with probability proportional to ``weights``
    (Efraimidis-Spirakis: keep the k largest ``u ** (1 / w)``)."""
    keys = [rng.random() ** (1 / weight) for weight in weights]
    ranked = sorted(zip(keys, range(len(items)), strict=True), reverse=True)
    return [items[index] for _, index in ranked[:k]]


def _poisson(rng: random.Random, mean: float) -> int:
    threshold, k, product = math.exp(-mean), 0, rng.random()
    while product > threshold:
        k += 1
        product *= rng.random()
    return k


def _overlap(student_interests: set[int], tags: set[int]) -> float:
    return len(student_interests & tags) / len(tags) if tags else 0.0


def _distance(a: CampusBuilding, b: CampusBuilding | None) -> float:
    from app.core.geo import haversine_m

    if b is None:
        return 1_000.0
    return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)


def _ascii(value: str) -> str:
    from app.core.text import slugify

    return slugify(value).replace("-", "")
