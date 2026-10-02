# Frontend integration guide

How the Flutter and Kotlin apps use the backend to implement the six required features. The full, always-up-to-date API reference is the Swagger UI at `http://<host>:8000/docs`.

## Connecting to the local backend

| Where the app runs | Base URL |
|---|---|
| Android emulator | `http://10.0.2.2:8000/api/v1` |
| Physical phone on the same Wi-Fi as the laptop | `http://<laptop LAN IP>:8000/api/v1` |
| Physical phone on Uniandes Wi-Fi (devices usually can't see each other) | Use a tunnel: `cloudflared tunnel --url http://localhost:8000` (or `ngrok http 8000`) and use the HTTPS URL it prints |

Make the base URL a build setting in both apps. Android blocks plain HTTP by default: allow cleartext traffic for the emulator and LAN hosts only in debug builds.

## Required features → endpoints

### (e) Authentication

1. Sign in with Firebase Authentication (Microsoft or Google provider, or email/password) in the app.
2. Send the Firebase ID token on every request: `Authorization: Bearer <idToken>`. Refresh it with `getIdToken()`; it expires after 1 hour.
3. Call `GET /me` right after sign-in. The first call creates the student profile.
4. Onboarding: `GET /interests`, then `PUT /me/interests`. Ask for consent with `PATCH /me` (`location_opt_in`, `notifications_opt_in`). Add the class schedule with `PUT /me/schedule`.

Only verified `@uniandes.edu.co` accounts are accepted. Locally, with `AUTH_PROVIDER=dev`, the token can simply be `dev:<email>` (for example in Swagger's **Authorize** dialog).

### (a) Sensors: QR check-in (camera) + proximity (GPS)

- Organizer: `GET /events/{id}/check-in-code` returns `qr_payload`. Render it as a QR code on the organizer's phone.
- Attendee: scan it with the **camera**, then send `POST /events/{id}/check-in` with `{ "code", "latitude", "longitude" }`. The **GPS** coordinates are optional; when sent, the student must be within 500 m of the venue.
- Check-in is open from 30 minutes before the event until 15 minutes after it ends. Repeated scans return `already_checked_in: true`.

### (b) Type 2 business questions: recommended groups + Explore with filters

- `GET /recommendations/groups`: personalized ranking with `reasons` to show on each card. Keep the `request_id`.
- `GET /groups?q=&category=&interest_id=&verified=&has_upcoming_events=&building=&sort=`: Explore and search. Every filter you apply is logged for BQ12.
- Open a profile with `GET /groups/{id}?entry_point=...&rec_request_id=...`.
- Save with `PUT /groups/{id}/save?source=explore|group_detail`. Join with `POST /groups/{id}/join`.

### (c) Context-aware: "Free right now"

`GET /recommendations/events/free-now?latitude=&longitude=` uses the student's **class schedule** (current or next free block), the **time of day** and the **location** (GPS if the student consented, otherwise the building of their last class). It returns the free block, the detected building and nearby events that fit, with walking minutes. Open suggestions with `entry_point=free_now&rec_request_id=...`.

### (d) Smart feature: learning recommender

The group recommender scores interest match, schedule fit, proximity and popularity with a logistic-regression model. A nightly job (`train_recommender`) re-trains it on its own logs (recommendation shown → joined within 14 days) and activates the new weights. Admins can inspect it at `GET /admin/recommender/models`.

### (f) External services

- **Firebase Authentication**: sign-in, verified by the backend.
- **Firebase Cloud Messaging**: push notifications. Register the device token with `POST /me/devices` after sign-in and whenever it refreshes, and send `DELETE /me/devices/{token}` on sign-out. The push `data` payload includes `notification_id`, `type`, `group_id` and `event_id`.
- **Feature connected to the backend (other than auth)**: recommendations, free-now suggestions, groups, events, chat and notifications are all served by this API.

## Other endpoints

| Screen | Endpoints |
|---|---|
| Events | `GET /events?mine=true`, `GET /events/{id}` |
| Create group / event | `POST /groups`, `PATCH /groups/{id}`, `POST /groups/{id}/events`, `PATCH /events/{id}` |
| Chat | `GET /groups/{id}/messages?before_id=`, `POST /groups/{id}/messages` |
| Notifications | `GET /me/notifications`, `POST /me/notifications/{id}/open`, `POST /me/notifications/{id}/dismiss` |
| Profile | `GET/PATCH /me`, `GET /me/groups`, `GET /me/saved-groups`, `GET/PUT /me/schedule` |
| Catalog | `GET /categories`, `GET /interests`, `GET /buildings` |

## Analytics

Send `screen_view`, `app_error` and `join_form_opened` to `POST /analytics/events`, and the `X-*` headers on every request. See [event-taxonomy.md](event-taxonomy.md).

## Demo accounts (after `SEED_MODE=full`)

| Token (`AUTH_PROVIDER=dev`) | Who |
|---|---|
| `dev:s.arango@uniandes.edu.co` | Sofía Arango, the prototype's student: member of Tennis, Emprendedores and AI & ML; admin of AI & ML |
| `dev:admin@uniandes.edu.co` | Platform admin: business questions, releases, jobs |
