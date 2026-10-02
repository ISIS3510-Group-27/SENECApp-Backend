"""Business questions (BQ1-BQ14) answered from the operational tables and the
analytics event log.

Each question is a function ``(db, params) -> (data, answer)`` where ``data`` is
JSON-serializable and ``answer`` is a one-sentence takeaway for the dashboard.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ReengagementCase
from app.recommendations.model import compute_features, score
from app.services import recommendations as recommendation_service
from app.services import reengagement


@dataclass(frozen=True)
class Params:
    since: datetime
    until: datetime
    app: str | None = None
    user_id: int | None = None

    @property
    def sql(self) -> dict[str, Any]:
        return {"since": self.since, "until": self.until, "app": self.app}


Result = tuple[dict[str, Any], str]


@dataclass(frozen=True)
class BusinessQuestion:
    id: str
    type: int
    question: str
    compute: Callable[[Session, Params], Result]


def _rows(db: Session, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: _plain(value) for key, value in row.items()}
        for row in db.execute(text(sql), params).mappings()
    ]


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return round(float(value), 4)
    if isinstance(value, float):
        return round(value, 4)
    return value


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


APP_FILTER = "AND (CAST(:app AS text) IS NULL OR app = :app)"


# --- Type 1 ------------------------------------------------------------------------------


def bq1_errors_by_screen(db: Session, p: Params) -> Result:
    by_screen = _rows(
        db,
        f"""
        SELECT screen,
               count(*) FILTER (WHERE name = 'screen_view') AS views,
               count(*) FILTER (WHERE name = 'app_error') AS errors,
               count(*) FILTER (WHERE name = 'app_error'
                                AND (properties->>'fatal')::boolean) AS crashes
        FROM analytics_events
        WHERE name IN ('screen_view', 'app_error') AND screen IS NOT NULL
          AND occurred_at >= :since AND occurred_at < :until {APP_FILTER}
        GROUP BY screen
        ORDER BY errors DESC
        """,
        p.sql,
    )
    for row in by_screen:
        row["errors_per_1000_views"] = (
            round(1000 * row["errors"] / row["views"], 2) if row["views"] else None
        )
    by_feature = _rows(
        db,
        f"""
        SELECT coalesce(properties->>'feature', 'unknown') AS feature,
               count(*) AS errors,
               count(*) FILTER (WHERE (properties->>'fatal')::boolean) AS crashes
        FROM analytics_events
        WHERE name = 'app_error' AND occurred_at >= :since AND occurred_at < :until {APP_FILTER}
        GROUP BY 1 ORDER BY errors DESC
        """,
        p.sql,
    )
    if not by_screen or not by_screen[0]["errors"]:
        return {"by_screen": by_screen, "by_feature": by_feature}, "No errors recorded."
    top = by_screen[0]
    worst_rate = max(
        (r for r in by_screen if r["views"] >= 50),
        key=lambda r: r["errors_per_1000_views"] or 0,
        default=top,
    )
    answer = (
        f"Most errors happen on '{top['screen']}' ({top['errors']} errors, {top['crashes']} "
        f"crashes); the highest rate is '{worst_rate['screen']}' with "
        f"{worst_rate['errors_per_1000_views']} errors per 1,000 views."
    )
    return {"by_screen": by_screen, "by_feature": by_feature}, answer


def bq11_load_times(db: Session, p: Params) -> Result:
    by_screen = _rows(
        db,
        f"""
        SELECT screen, count(*) AS samples,
               avg((properties->>'load_time_ms')::numeric) AS avg_ms,
               percentile_cont(0.9) WITHIN GROUP (
                   ORDER BY (properties->>'load_time_ms')::numeric) AS p90_ms
        FROM analytics_events
        WHERE name = 'screen_view' AND properties ? 'load_time_ms'
          AND occurred_at >= :since AND occurred_at < :until {APP_FILTER}
        GROUP BY screen ORDER BY avg_ms DESC
        """,
        p.sql,
    )
    breakdown = _rows(
        db,
        f"""
        SELECT screen, device_model, os_version, count(*) AS samples,
               avg((properties->>'load_time_ms')::numeric) AS avg_ms
        FROM analytics_events
        WHERE name = 'screen_view' AND properties ? 'load_time_ms'
          AND occurred_at >= :since AND occurred_at < :until {APP_FILTER}
        GROUP BY screen, device_model, os_version
        HAVING count(*) >= 20
        ORDER BY avg_ms DESC
        LIMIT 30
        """,
        p.sql,
    )
    if not by_screen:
        return {"by_screen": [], "by_device": []}, "No screen load data."
    slowest = breakdown[0] if breakdown else None
    slowest_screen = by_screen[0]
    answer = (
        f"'{slowest_screen['screen']}' has the highest average load time "
        f"({slowest_screen['avg_ms']:.0f} ms)"
    )
    if slowest:
        answer += (
            f"; the worst combination is '{slowest['screen']}' on {slowest['device_model']} "
            f"(Android {slowest['os_version']}) at {slowest['avg_ms']:.0f} ms."
        )
    return {"by_screen": by_screen, "by_device": breakdown}, answer


# --- Type 2 ------------------------------------------------------------------------------

BUCKET_ORDER = ("low", "medium", "high")
DRIVERS = ("interest_match", "schedule_fit", "proximity")

JOINED_WITHIN_14_DAYS = """
    EXISTS (SELECT 1 FROM memberships m
            WHERE m.user_id = l.user_id AND m.group_id = l.item_id
              AND m.joined_at >= l.created_at
              AND m.joined_at <= l.created_at + interval '14 days')
"""


def bq2_recommended_group_joins(db: Session, p: Params) -> Result:
    overall = _rows(
        db,
        f"""
        SELECT count(*) AS impressions, count(*) FILTER (WHERE {JOINED_WITHIN_14_DAYS}) AS joins
        FROM recommendation_logs l
        WHERE kind = 'group' AND created_at >= :since AND created_at < :until
        """,
        p.sql,
    )[0]
    overall["join_rate"] = _rate(overall["joins"], overall["impressions"])

    by_feature: dict[str, list[dict[str, Any]]] = {}
    for feature in DRIVERS:
        by_feature[feature] = _rows(
            db,
            f"""
            SELECT CASE WHEN (features->>'{feature}')::numeric < 0.34 THEN 'low'
                        WHEN (features->>'{feature}')::numeric < 0.67 THEN 'medium'
                        ELSE 'high' END AS bucket,
                   count(*) AS impressions,
                   count(*) FILTER (WHERE {JOINED_WITHIN_14_DAYS}) AS joins
            FROM recommendation_logs l
            WHERE kind = 'group' AND created_at >= :since AND created_at < :until
            GROUP BY 1
            """,
            p.sql,
        )
        by_feature[feature].sort(key=lambda row: BUCKET_ORDER.index(row["bucket"]))
        for row in by_feature[feature]:
            row["join_rate"] = _rate(row["joins"], row["impressions"])

    by_group = _rows(
        db,
        f"""
        SELECT g.name AS group_name, count(*) AS impressions,
               count(*) FILTER (WHERE {JOINED_WITHIN_14_DAYS}) AS joins
        FROM recommendation_logs l JOIN student_groups g ON g.id = l.item_id
        WHERE kind = 'group' AND l.created_at >= :since AND l.created_at < :until
        GROUP BY g.name HAVING count(*) >= 20
        ORDER BY count(*) FILTER (WHERE {JOINED_WITHIN_14_DAYS})::numeric / count(*) DESC
        LIMIT 10
        """,
        p.sql,
    )
    for row in by_group:
        row["join_rate"] = _rate(row["joins"], row["impressions"])

    version, weights = recommendation_service.active_model(db)
    data: dict[str, Any] = {
        "overall": overall,
        "join_rate_by_feature_bucket": by_feature,
        "best_converting_groups": by_group,
        "model": {"version": version, "weights": weights},
    }
    if p.user_id is not None:
        data["student_predictions"] = predict_for_student(db, p.user_id)

    strongest = max(DRIVERS, key=lambda f: weights.get(f, 0.0))
    high = {
        f: next((r for r in rows if r["bucket"] == "high"), None) for f, rows in by_feature.items()
    }
    low = {
        f: next((r for r in rows if r["bucket"] == "low"), None) for f, rows in by_feature.items()
    }
    lift = {
        f: (high[f]["join_rate"] or 0) / (low[f]["join_rate"] or 1e-9)
        for f in by_feature
        if high[f] and low[f] and low[f]["join_rate"]
    }
    answer = (
        f"{_pct(overall['join_rate'])} of recommended groups are joined within 14 days. "
        f"The strongest driver is {strongest.replace('_', ' ')}"
    )
    if strongest in lift:
        answer += (
            f" (high-match recommendations convert {lift[strongest]:.1f}x more than low-match)"
        )
    return data, answer + "."


def predict_for_student(db: Session, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Current join probability for each group the student hasn't joined (no logging)."""
    _version, weights = recommendation_service.active_model(db)
    student = recommendation_service.load_student_profile(db, user_id)
    joined = {
        row[0]
        for row in db.execute(
            text("SELECT group_id FROM memberships WHERE user_id = :u AND status = 'active'"),
            {"u": user_id},
        )
    }
    profiles = recommendation_service.load_group_profiles(db)
    max_members = max((g.member_count for g in profiles.values()), default=0)
    names = dict(db.execute(text("SELECT id, name FROM student_groups")).all())
    ranked = sorted(
        (
            (score(compute_features(student, g, max_members), weights), g)
            for g in profiles.values()
            if g.group_id not in joined
        ),
        key=lambda pair: -pair[0],
    )[:limit]
    return [
        {"group_id": g.group_id, "group_name": names[g.group_id], "join_probability": round(s, 4)}
        for s, g in ranked
    ]


EVENT_REC_INTERACTIONS = """
    WITH viewed AS (
        SELECT DISTINCT user_id, properties->>'rec_request_id' AS request_id,
               (properties->>'event_id')::int AS event_id
        FROM analytics_events
        WHERE name = 'event_viewed' AND properties->>'rec_request_id' IS NOT NULL
          AND occurred_at >= :since
    ),
    logs AS (
        SELECT l.*, (v.user_id IS NOT NULL OR a.id IS NOT NULL) AS interacted
        FROM recommendation_logs l
        LEFT JOIN viewed v ON v.user_id = l.user_id AND v.request_id = l.request_id::text
                          AND v.event_id = l.item_id
        LEFT JOIN event_attendance a ON a.user_id = l.user_id AND a.event_id = l.item_id
        WHERE l.kind = 'event' AND l.created_at >= :since AND l.created_at < :until
    )
"""


def bq3_free_block_event_interactions(db: Session, p: Params) -> Result:
    by_hour = _rows(
        db,
        EVENT_REC_INTERACTIONS
        + """
        SELECT (context->>'hour')::int AS hour, count(*) AS impressions,
               count(*) FILTER (WHERE interacted) AS interactions
        FROM logs GROUP BY 1 ORDER BY 1
        """,
        p.sql,
    )
    by_building = _rows(
        db,
        EVENT_REC_INTERACTIONS
        + """
        SELECT coalesce(context->>'building_code', 'unknown') AS building,
               count(*) AS impressions, count(*) FILTER (WHERE interacted) AS interactions
        FROM logs GROUP BY 1 ORDER BY 3 DESC
        """,
        p.sql,
    )
    top_cells = _rows(
        db,
        EVENT_REC_INTERACTIONS
        + """
        SELECT (context->>'hour')::int AS hour,
               coalesce(context->>'building_code', 'unknown') AS building,
               count(*) AS impressions, count(*) FILTER (WHERE interacted) AS interactions
        FROM logs GROUP BY 1, 2 HAVING count(*) >= 15
        ORDER BY count(*) FILTER (WHERE interacted)::numeric / count(*) DESC LIMIT 10
        """,
        p.sql,
    )
    for rows in (by_hour, by_building, top_cells):
        for row in rows:
            row["interaction_rate"] = _rate(row["interactions"], row["impressions"])
    data = {"by_hour": by_hour, "by_building": by_building, "best_time_and_place": top_cells}
    if not top_cells:
        return data, "Not enough free-block suggestions logged yet."
    best = top_cells[0]
    best_hour = max(by_hour, key=lambda r: r["interaction_rate"] or 0)
    answer = (
        f"Free-block suggestions get the most interaction around {best_hour['hour']}:00 "
        f"({_pct(best_hour['interaction_rate'])}); the best time and place is "
        f"{best['hour']}:00 near {best['building']} ({_pct(best['interaction_rate'])})."
    )
    return data, answer


def bq12_search_filters(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        WITH searches AS (
            SELECT id, user_id, occurred_at, properties->'filters' AS filters
            FROM analytics_events
            WHERE name = 'group_searched' AND occurred_at >= :since AND occurred_at < :until
        ),
        outcomes AS (
            SELECT s.id,
                   bool_or(e.name = 'group_viewed') AS opened,
                   bool_or(e.name = 'group_joined') AS joined
            FROM searches s
            JOIN analytics_events e
              ON e.user_id = s.user_id AND e.name IN ('group_viewed', 'group_joined')
             AND e.properties->>'entry_point' = 'search'
             AND e.occurred_at BETWEEN s.occurred_at AND s.occurred_at + interval '30 minutes'
            GROUP BY s.id
        )
        SELECT f.filter, count(*) AS searches,
               count(*) FILTER (WHERE o.opened) AS followed_by_open,
               count(*) FILTER (WHERE o.joined) AS followed_by_join
        FROM searches s
        CROSS JOIN LATERAL jsonb_array_elements_text(s.filters) AS f(filter)
        LEFT JOIN outcomes o ON o.id = s.id
        GROUP BY f.filter
        ORDER BY count(*) FILTER (WHERE o.opened OR o.joined) DESC
        """,
        p.sql,
    )
    for row in rows:
        row["open_rate"] = _rate(row["followed_by_open"], row["searches"])
        row["join_rate"] = _rate(row["followed_by_join"], row["searches"])
    if not rows:
        return {"filters": []}, "No searches recorded."
    top = rows[0]
    return {"filters": rows}, (
        f"'{top['filter']}' is the filter most used before opening or joining a group "
        f"({top['followed_by_open']} opens, {top['followed_by_join']} joins after "
        f"{top['searches']} searches)."
    )


def bq13_category_views_saves(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        SELECT c.label AS category,
               count(*) FILTER (WHERE e.name = 'group_viewed'
                                AND e.properties->>'entry_point' = 'explore') AS explore_views,
               count(*) FILTER (WHERE e.name = 'group_saved') AS saves,
               count(*) FILTER (WHERE e.name = 'group_saved'
                                AND e.properties->>'source' = 'explore') AS saves_from_explore
        FROM analytics_events e
        JOIN categories c ON c.slug = e.properties->>'category'
        WHERE e.name IN ('group_viewed', 'group_saved')
          AND e.occurred_at >= :since AND e.occurred_at < :until
        GROUP BY c.label
        ORDER BY explore_views DESC
        """,
        p.sql,
    )
    for row in rows:
        row["save_rate"] = _rate(row["saves"], row["explore_views"])
    if not rows:
        return {"categories": []}, "No Explore activity recorded."
    most_saved = max(rows, key=lambda r: r["saves"])
    return {"categories": rows}, (
        f"{rows[0]['category']} gets the most Explore views ({rows[0]['explore_views']}) and "
        f"{most_saved['category']} the most saves ({most_saved['saves']})."
    )


# --- Type 3 ------------------------------------------------------------------------------


def bq4_profile_fields(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        WITH views AS (
            SELECT e.id, e.user_id, (e.properties->>'group_id')::int AS group_id,
                   e.occurred_at, e.properties->'profile' AS profile
            FROM analytics_events e
            WHERE e.name = 'group_viewed' AND e.properties ? 'profile'
              AND NOT coalesce((e.properties->>'is_member')::boolean, false)
              AND e.occurred_at >= :since AND e.occurred_at < :until
        ),
        conversions AS (
            SELECT user_id, (properties->>'group_id')::int AS group_id, occurred_at
            FROM analytics_events
            WHERE name IN ('group_saved', 'group_joined') AND occurred_at >= :since
        ),
        labeled AS (
            -- A view converts if the student saved or joined that group within 7 days.
            SELECT v.profile, count(c.user_id) > 0 AS converted
            FROM views v
            LEFT JOIN conversions c
              ON c.user_id = v.user_id AND c.group_id = v.group_id
             AND c.occurred_at BETWEEN v.occurred_at AND v.occurred_at + interval '7 days'
            GROUP BY v.id, v.profile
        ),
        fields AS (
            SELECT kv.key AS field,
                   CASE jsonb_typeof(kv.value)
                        WHEN 'boolean' THEN (kv.value::text = 'true')::int::numeric
                        WHEN 'number' THEN kv.value::text::numeric END AS value,
                   converted::int AS converted
            FROM labeled CROSS JOIN LATERAL jsonb_each(profile) AS kv
        )
        SELECT field, count(*) AS views,
               corr(value, converted) AS correlation,
               avg(converted) FILTER (WHERE value > 0) AS conversion_with,
               avg(converted) FILTER (WHERE value = 0) AS conversion_without
        FROM fields WHERE value IS NOT NULL
        GROUP BY field
        ORDER BY abs(coalesce(corr(value, converted), 0)) DESC
        """,
        p.sql,
    )
    if not rows:
        return {"fields": []}, "No group profile views recorded."
    for row in rows:
        row["lift"] = (
            round(row["conversion_with"] / row["conversion_without"], 2)
            if row["conversion_with"] and row["conversion_without"]
            else None
        )
    top = [r for r in rows if r["correlation"] is not None][:3]

    def describe(row: dict[str, Any]) -> str:
        lift = f", {row['lift']}x conversion" if row["lift"] else ""
        return f"{row['field']} (r={row['correlation']:.2f}{lift})"

    return {"fields": rows}, (
        "Fields most correlated with saving or joining: " + ", ".join(map(describe, top)) + "."
    )


def bq6_entry_points(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        SELECT coalesce(properties->>'entry_point', 'direct') AS entry_point,
               count(*) FILTER (WHERE name = 'group_viewed') AS profile_views,
               count(*) FILTER (WHERE name = 'group_joined') AS joins
        FROM analytics_events
        WHERE name IN ('group_viewed', 'group_joined')
          AND occurred_at >= :since AND occurred_at < :until
        GROUP BY 1 ORDER BY joins DESC
        """,
        p.sql,
    )
    for row in rows:
        row["view_to_join_rate"] = _rate(row["joins"], row["profile_views"])
    by_entry = {r["entry_point"]: r for r in rows}
    rec, search = by_entry.get("recommendation"), by_entry.get("search")
    if not rec or not search:
        return {"entry_points": rows}, "Not enough joins from both entry points yet."
    winner, loser = (rec, search) if rec["joins"] >= search["joins"] else (search, rec)
    return {"entry_points": rows}, (
        f"{winner['entry_point'].capitalize()} produces more joins ({winner['joins']} vs "
        f"{loser['joins']}; view-to-join {_pct(winner['view_to_join_rate'])} vs "
        f"{_pct(loser['view_to_join_rate'])})."
    )


def bq7_join_funnel(db: Session, p: Params) -> Result:
    row = _rows(
        db,
        """
        WITH opened AS (
            SELECT DISTINCT user_id, (properties->>'group_id')::int AS group_id
            FROM analytics_events
            WHERE name = 'group_viewed'
              AND NOT coalesce((properties->>'is_member')::boolean, false)
              AND occurred_at >= :since AND occurred_at < :until
        ),
        form AS (
            SELECT DISTINCT user_id, (properties->>'group_id')::int AS group_id
            FROM analytics_events
            WHERE name = 'join_form_opened' AND occurred_at >= :since AND occurred_at < :until
        ),
        submitted AS (
            SELECT DISTINCT user_id, (properties->>'group_id')::int AS group_id
            FROM analytics_events
            WHERE name = 'group_joined' AND occurred_at >= :since AND occurred_at < :until
        )
        SELECT (SELECT count(*) FROM opened) AS profile_opened,
               (SELECT count(*) FROM form JOIN opened USING (user_id, group_id)) AS form_opened,
               (SELECT count(*) FROM submitted JOIN form USING (user_id, group_id)) AS submitted
        """,
        p.sql,
    )[0]
    steps = [
        {
            "step": "open profile -> open join form",
            "entered": row["profile_opened"],
            "continued": row["form_opened"],
        },
        {
            "step": "open join form -> submit",
            "entered": row["form_opened"],
            "continued": row["submitted"],
        },
    ]
    for step in steps:
        step["abandonment_rate"] = (
            round(1 - step["continued"] / step["entered"], 4) if step["entered"] else None
        )
    data = {"funnel": row, "steps": steps}
    if not row["profile_opened"]:
        return data, "No join-flow activity recorded."
    worst = max(steps, key=lambda s: s["abandonment_rate"] or 0)
    return data, (
        f"Students abandon most at '{worst['step']}' "
        f"({_pct(worst['abandonment_rate'])} drop off there)."
    )


def bq8_notifications(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        SELECT type, count(*) AS sent,
               count(opened_at) AS opened,
               count(*) FILTER (WHERE dismissed_at IS NOT NULL AND opened_at IS NULL) AS dismissed,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM opened_at - created_at) / 60
               ) FILTER (WHERE opened_at IS NOT NULL) AS median_minutes_to_open
        FROM notifications
        WHERE created_at >= :since AND created_at < :until
          AND type IN ('new_event', 'group_recommendation', 'group_message')
        GROUP BY type ORDER BY count(opened_at)::numeric / count(*) DESC
        """,
        p.sql,
    )
    for row in rows:
        row["open_rate"] = _rate(row["opened"], row["sent"])
        row["dismiss_rate"] = _rate(row["dismissed"], row["sent"])
    if len(rows) < 2:
        return {"types": rows}, "Not enough notifications sent yet."
    labels = {
        "new_event": "new events from your organizations",
        "group_recommendation": "new groups you might like",
        "group_message": "chat messages from your organizations",
    }
    best, worst = rows[0], rows[-1]
    return {"types": rows}, (
        f"Most interaction: {labels[best['type']]} ({_pct(best['open_rate'])} opened); "
        f"least: {labels[worst['type']]} ({_pct(worst['open_rate'])})."
    )


# --- Type 4 ------------------------------------------------------------------------------


def bq5_interest_search_to_join(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        WITH searches AS (
            SELECT properties AS props FROM analytics_events
            WHERE name = 'group_searched' AND occurred_at >= :since AND occurred_at < :until
        ),
        search_counts AS (
            SELECT i.id AS interest_id, count(s.props) AS searches
            FROM interests i
            LEFT JOIN searches s
              ON s.props->'interest_ids' @> to_jsonb(i.id)
              OR s.props->>'query' ILIKE '%' || i.name || '%'
            GROUP BY i.id
        ),
        join_counts AS (
            SELECT gi.interest_id, count(*) AS joins
            FROM analytics_events e
            JOIN group_interests gi ON gi.group_id = (e.properties->>'group_id')::int
            WHERE e.name = 'group_joined' AND e.properties->>'entry_point' = 'search'
              AND e.occurred_at >= :since AND e.occurred_at < :until
            GROUP BY gi.interest_id
        ),
        students AS (
            SELECT interest_id, count(*) AS students FROM user_interests GROUP BY interest_id
        )
        SELECT i.name AS interest, coalesce(st.students, 0) AS students,
               sc.searches, coalesce(j.joins, 0) AS joins
        FROM interests i
        JOIN search_counts sc ON sc.interest_id = i.id
        LEFT JOIN join_counts j ON j.interest_id = i.id
        LEFT JOIN students st ON st.interest_id = i.id
        ORDER BY i.name
        """,
        p.sql,
    )
    for row in rows:
        row["search_to_join_ratio"] = _rate(row["joins"], row["searches"])
    ranked = sorted(
        (r for r in rows if r["searches"] >= 10),
        key=lambda r: r["search_to_join_ratio"] or 0,
        reverse=True,
    )
    if not ranked:
        return {"interests": rows}, "Not enough searches recorded."
    top = ranked[0]
    return {"interests": ranked + [r for r in rows if r["searches"] < 10]}, (
        f"{top['interest']} has the highest search-to-join ratio "
        f"({top['search_to_join_ratio']:.2f} joins per search) and represents "
        f"{top['students']} students."
    )


def bq9_interest_supply_demand(db: Session, p: Params) -> Result:
    rows = _rows(
        db,
        """
        WITH active_groups AS (
            SELECT g.id FROM student_groups g
            WHERE g.is_active AND g.review_status = 'approved' AND EXISTS (
                SELECT 1 FROM events e
                WHERE e.group_id = g.id AND NOT e.is_cancelled
                  AND e.starts_at >= :until - interval '30 days')
        )
        SELECT i.name AS interest, c.label AS category,
               (SELECT count(*) FROM user_interests ui WHERE ui.interest_id = i.id)
                   AS opted_in_students,
               (SELECT count(*) FROM group_interests gi
                JOIN active_groups ag ON ag.id = gi.group_id
                WHERE gi.interest_id = i.id) AS active_groups
        FROM interests i LEFT JOIN categories c ON c.id = i.category_id
        ORDER BY opted_in_students DESC
        """,
        p.sql,
    )
    for row in rows:
        row["students_per_active_group"] = _rate(row["opted_in_students"], row["active_groups"])
        row["unmet_demand"] = row["active_groups"] == 0 and row["opted_in_students"] > 0
    if not rows:
        return {"interests": []}, "No interests configured."
    unmet = [r["interest"] for r in rows if r["unmet_demand"]][:3]
    served = [r for r in rows if r["active_groups"]]
    worst = max(served, key=lambda r: r["students_per_active_group"] or 0) if served else None
    answer = (
        f"{rows[0]['interest']} has the most opted-in students ({rows[0]['opted_in_students']})."
    )
    if worst:
        answer += (
            f" {worst['interest']} has the highest ratio "
            f"({worst['students_per_active_group']:.0f} students per active group)."
        )
    if unmet:
        answer += f" No active group covers: {', '.join(unmet)}."
    return {"interests": rows}, answer


# --- Type 5 ------------------------------------------------------------------------------


def bq10_declining_groups(db: Session, p: Params) -> Result:
    names = dict(db.execute(text("SELECT id, name FROM student_groups")).all())
    cases = [reengagement.case_summary(db, case, p.until) for case in db.query(ReengagementCase)]
    for case in cases:
        case["group_name"] = names.get(case["group_id"])

    arms: dict[str, dict[str, Any]] = {}
    for case in cases:
        arm = arms.setdefault(
            case["arm"], {"arm": case["arm"], "groups": 0, "lapsed": 0, "returned": 0}
        )
        arm["groups"] += 1
        arm["lapsed"] += case["lapsed_members"]
        arm["returned"] += case["returned_members"]
    for arm in arms.values():
        arm["return_to_attendance_rate"] = _rate(arm["returned"], arm["lapsed"])

    group_ids = [
        row[0]
        for row in db.execute(
            text("SELECT id FROM student_groups WHERE is_active AND review_status = 'approved'")
        )
    ]
    live = reengagement.weekly_attendance(db, group_ids, p.until)
    currently_declining = []
    for group_id, series in live.items():
        declining, baseline, decline = reengagement.is_declining(series)
        if declining:
            currently_declining.append(
                {
                    "group_id": group_id,
                    "group_name": names[group_id],
                    "weekly_attendance": series,
                    "decline_pct": decline,
                }
            )

    data = {
        "declining_groups": cases,
        "currently_declining": currently_declining,
        "arms": sorted(
            arms.values(), key=lambda a: a["return_to_attendance_rate"] or 0, reverse=True
        ),
    }
    if not cases:
        return data, "No group has shown a >30% attendance decline yet."
    ranked = data["arms"]
    answer = (
        f"{len(cases)} groups declined >30% over four weeks "
        f"({', '.join(sorted(c['group_name'] for c in cases))}). "
    )
    if len(ranked) == 2:
        best, other = ranked
        answer += (
            f"{best['arm'].replace('_', ' ').capitalize()} produced the higher "
            f"return-to-attendance rate ({_pct(best['return_to_attendance_rate'])} vs "
            f"{_pct(other['return_to_attendance_rate'])})."
        )
    return data, answer


AFFECTED_SCREENS = {
    "recommendations": ("discover", "recommendations", "free_now"),
    "notifications": ("notifications",),
}


def bq14_release_impact(db: Session, p: Params) -> Result:
    releases = _rows(
        db,
        """
        SELECT app, version, released_at, feature_area FROM releases
        WHERE feature_area IN ('recommendations', 'notifications')
          AND released_at >= :since AND released_at < :until
        ORDER BY released_at
        """,
        p.sql,
    )
    window = timedelta(days=7)
    results = []
    for release in releases:
        screens = list(AFFECTED_SCREENS[release["feature_area"]])
        periods = {}
        for label, start, end in (
            ("before", release["released_at"] - window, release["released_at"]),
            ("after", release["released_at"], release["released_at"] + window),
        ):
            stats = _rows(
                db,
                """
                SELECT count(DISTINCT session_id) AS sessions,
                       count(*) FILTER (WHERE name = 'app_error') AS errors,
                       count(*) FILTER (WHERE name = 'app_error'
                                        AND (properties->>'fatal')::boolean) AS crashes,
                       count(*) FILTER (WHERE name = 'app_error'
                                        AND screen = ANY(:screens)) AS feature_errors
                FROM analytics_events
                WHERE source = 'client' AND app = :app
                  AND occurred_at >= :start AND occurred_at < :end
                """,
                {"app": release["app"], "start": start, "end": end, "screens": screens},
            )[0]
            sessions = stats["sessions"] or 0
            stats["errors_per_100_sessions"] = (
                round(100 * stats["errors"] / sessions, 2) if sessions else None
            )
            stats["crashes_per_100_sessions"] = (
                round(100 * stats["crashes"] / sessions, 2) if sessions else None
            )
            periods[label] = stats
        before = periods["before"]["errors_per_100_sessions"]
        after = periods["after"]["errors_per_100_sessions"]
        results.append(
            {
                **release,
                "affected_screens": screens,
                "before": periods["before"],
                "after": periods["after"],
                "error_rate_change": _rate((after or 0) - (before or 0), before or 0),
            }
        )
    if not results:
        return {"releases": []}, "No recommendation/notification releases registered."
    worst = max(results, key=lambda r: r["error_rate_change"] or 0)
    return {"releases": results}, (
        f"The largest change followed {worst['app']} {worst['version']} "
        f"({worst['feature_area']}): errors per 100 sessions went from "
        f"{worst['before']['errors_per_100_sessions']} to "
        f"{worst['after']['errors_per_100_sessions']} "
        f"({'+' if (worst['error_rate_change'] or 0) >= 0 else ''}"
        f"{_pct(worst['error_rate_change'])})."
    )


QUESTIONS: dict[str, BusinessQuestion] = {
    q.id: q
    for q in (
        BusinessQuestion(
            "1",
            1,
            "In which SENECApp features or screens do crashes and errors happen most often?",
            bq1_errors_by_screen,
        ),
        BusinessQuestion(
            "11",
            1,
            "Which screens have the highest average load time, broken down by device model and "
            "OS version?",
            bq11_load_times,
        ),
        BusinessQuestion(
            "2",
            2,
            "Which recommended groups is a student most likely to join after viewing them, given "
            "their interests, schedule, and campus location?",
            bq2_recommended_group_joins,
        ),
        BusinessQuestion(
            "3",
            2,
            "For a student with a free block on campus, at what times and locations do nearby "
            "event recommendations get the most interaction?",
            bq3_free_block_event_interactions,
        ),
        BusinessQuestion(
            "12",
            2,
            "Which search filters do students use the most before opening or joining a student "
            "group?",
            bq12_search_filters,
        ),
        BusinessQuestion(
            "13",
            2,
            "Which group categories get the most views and saves in the Explore screen?",
            bq13_category_views_saves,
        ),
        BusinessQuestion(
            "4",
            3,
            "Which fields of a group profile are most correlated with students saving or joining "
            "the group?",
            bq4_profile_fields,
        ),
        BusinessQuestion(
            "6",
            3,
            "Which entry point produces more joins: personalized recommendations, or search and "
            "filters?",
            bq6_entry_points,
        ),
        BusinessQuestion(
            "7",
            3,
            "At which step of the join flow do students most often abandon the process?",
            bq7_join_funnel,
        ),
        BusinessQuestion(
            "8",
            3,
            "Which notifications get the most and least user interaction?",
            bq8_notifications,
        ),
        BusinessQuestion(
            "5",
            4,
            "Which student interests have the highest search-to-join ratio, and how many students "
            "does each of them represent?",
            bq5_interest_search_to_join,
        ),
        BusinessQuestion(
            "9",
            4,
            "Which student interests have the highest ratio of opted-in students to active groups, "
            "ranked by number of opted-in students?",
            bq9_interest_supply_demand,
        ),
        BusinessQuestion(
            "10",
            5,
            "Which active student groups show a decline in participation (>30% over four "
            "consecutive weeks), and which re-engagement feature produces the higher return to "
            "attendance rate: event reminders or group messages?",
            bq10_declining_groups,
        ),
        BusinessQuestion(
            "14",
            5,
            "How do crash and error rates change after updating recommendation or notification "
            "features?",
            bq14_release_impact,
        ),
    )
}


def answer_question(
    db: Session, question_id: str, days: int, app: str | None, user_id: int | None = None
) -> dict[str, Any]:
    question = QUESTIONS[question_id]
    until = datetime.now(UTC)
    params = Params(since=until - timedelta(days=days), until=until, app=app, user_id=user_id)
    data, answer = question.compute(db, params)
    return {
        "id": question.id,
        "type": question.type,
        "question": question.question,
        "answer": answer,
        "params": {"days": days, "app": app, "user_id": user_id},
        "generated_at": until,
        "data": data,
    }
