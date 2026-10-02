# Analytics event taxonomy

This is the contract between the mobile apps (Flutter and Kotlin) and the analytics pipeline. **Both apps must use exactly these names and properties**, or the business-question (BQ) queries will miss their data.

Most analytics are recorded **by the backend automatically** when the apps call the regular API (searches, profile views, saves, joins, check-ins, notification opens). The apps only send a few UI-level events themselves.

## 1. Headers on every API request

Send these on **every** request (not only analytics). They let server-side events carry the same context as client events.

| Header | Example | Notes |
|---|---|---|
| `Authorization` | `Bearer <firebase-id-token>` | Required for most endpoints |
| `X-Session-Id` | `9f1c…` | Random id, new each time the app comes to the foreground after 30 min in background |
| `X-App` | `flutter` / `kotlin` | Which client |
| `X-App-Version` | `1.3.1` | Must match the versions registered in `/admin/releases` (BQ14) |
| `X-Platform` | `android` / `ios` | |
| `X-Device-Model` | `Samsung Galaxy A14` | |
| `X-OS-Version` | `13` | Major OS version |

## 2. Events the apps send

`POST /api/v1/analytics/events` with a batch (1–500 events). Auth is optional (so errors before sign-in aren't lost). Queue events locally and flush every ~30 s, when the app goes to the background, and on reconnect. Re-sending a batch is safe: events are de-duplicated by `event_id`.

```json
{
  "events": [
    {
      "event_id": "3b2f1c4e-8d7a-4f61-9a55-0c9b8e7d6a51",
      "name": "screen_view",
      "occurred_at": "2026-10-02T17:03:12.512Z",
      "session_id": "9f1c…",
      "screen": "group_detail",
      "app": "kotlin",
      "app_version": "1.2.0",
      "platform": "android",
      "device_model": "Samsung Galaxy A14",
      "os_version": "13",
      "properties": { "load_time_ms": 842 }
    }
  ]
}
```

| Event | When | `screen` | `properties` | Used by |
|---|---|---|---|---|
| `screen_view` | A screen finished its first meaningful render | screen name (below) | `load_time_ms` (int): from navigation start to first render with data | BQ1 (denominator), BQ11, BQ14 (sessions) |
| `app_error` | Any caught exception shown to the user, or a crash (report on next launch) | screen where it happened | `feature` (below), `error_type` (exception class), `fatal` (bool), optional `message` (**no personal data**) | BQ1, BQ14 |
| `join_form_opened` | The join form is shown | `join_form` | `group_id` (int), `join_attempt_id` (string you generate; send the same id in `POST /groups/{id}/join`) | BQ7 |

### Screen names

`login`, `discover`, `explore`, `group_detail`, `join_form`, `recommendations`, `free_now`, `events`, `event_detail`, `check_in_scanner`, `notifications`, `chat`, `profile`, `create_group`

### Feature names (for `app_error.properties.feature`)

`auth`, `home`, `explore`, `group_profile`, `join`, `recommendations`, `free_now`, `events`, `check_in`, `notifications`, `chat`, `profile`, `create_group`

## 3. What the apps must pass to the API so server events are attributed

| Action | What to send | Server event | Used by |
|---|---|---|---|
| Search / filter in Explore | Just call `GET /groups?...` with the filters | `group_searched` (filters used, results) | BQ5, BQ12 |
| Open a group profile | `GET /groups/{id}?entry_point=<explore\|search\|recommendation\|notification\|event\|direct>` plus `rec_request_id` when it came from recommendations | `group_viewed` (with a snapshot of which profile fields are filled) | BQ4, BQ6, BQ7, BQ13 |
| Save a group | `PUT /groups/{id}/save?source=<explore\|group_detail>` | `group_saved` | BQ4, BQ13 |
| Submit the join form | `POST /groups/{id}/join` with `entry_point`, `rec_request_id`, `join_attempt_id` | `group_joined` | BQ2, BQ5, BQ6, BQ7 |
| Open a suggested event | `GET /events/{id}?entry_point=free_now&rec_request_id=<request_id>` | `event_viewed` | BQ3 |
| Scan the QR code at an event | `POST /events/{id}/check-in` | `event_checked_in` + attendance row | BQ3, BQ10 |
| Tap a notification | `POST /me/notifications/{id}/open` (the push payload includes `notification_id`) | `notification_opened` | BQ8 |
| Swipe a notification away | `POST /me/notifications/{id}/dismiss` | `notification_dismissed` | BQ8 |

Other server events: `group_unsaved`, `group_left`, `group_created`, `event_created`, `group_message_posted`.

## 4. Other analytics sources (no app work needed)

| Table | Content | Used by |
|---|---|---|
| `recommendation_logs` | Every recommended group/event shown, with rank, score, features and context | BQ2, BQ3 |
| `notifications` | Every notification sent, with opened/dismissed timestamps | BQ8, BQ10 |
| `event_attendance` | Check-ins | BQ3, BQ10 |
| `memberships` | Joins with `entry_point` | BQ2, BQ6 |
| `user_interests` | Opt-ins with timestamps | BQ5, BQ9 |
| `reengagement_cases` | Declining groups and the re-engagement feature assigned to each | BQ10 |
| `releases` | App releases and the feature area they changed | BQ14 |

## 5. Privacy

- Never put names, emails, message text or coordinates in event properties.
- Location is only used when `location_opt_in` is true. The backend stores the nearest building or a distance, never raw coordinates.
