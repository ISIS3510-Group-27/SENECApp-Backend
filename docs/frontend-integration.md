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

## Creating a group (Create RSO): review flow

Student-created groups are **proposals**: Uniandes Student Affairs reviews them before they go public.

1. **Submit** with `POST /groups`. The minimal payload matches the form:

   ```json
   {
     "name": "Ciberseguridad Uniandes",
     "category": "technology",
     "description": "CTF practice, security talks and disclosure workshops.",
     "contact_email": "ciber@uniandes.edu.co"
   }
   ```

   - `category` is a slug from `GET /categories`. You can send `category_id` instead, but **exactly one** of the two (422 otherwise, also for unknown values).
   - `tag_ids` (0–8 interest ids) is optional. Without it, up to 3 tags are inferred from the name and description (interests of that category that are mentioned), or the category's most popular interest.
   - Optional fields still accepted: `color`, `image_url`, `founded_year`, `instagram_url`, `website_url`, `meeting_building_id`.
   - 201 returns the group (`GroupDetail`) with `review_status: "pending"` and `my_role: "admin"`. Show "Proposal Submitted!". If no founded year is sent, it's the current year.
   - 409 if the name is taken; 422 for an invalid `contact_email`.
2. **While pending**, the group is only visible to its creator: it appears in `GET /me/groups` and `GET /groups/{id}`, but not in search, recommendations or event listings. Nobody can join it (409), and it can't publish events (409). Use `review_status` to show a "Pending review" badge.
3. **Review** (platform admins): `GET /admin/groups/pending`, then `POST /admin/groups/{id}/approve` or `POST /admin/groups/{id}/reject` with `{ "reason": "..." }`.
4. **Outcome:** the creator gets an in-app notification of type `group_review`, with `data.review_status` set to `approved` or `rejected`.
   - **Approved:** the group becomes public, and students whose interests match its tags get a `group_recommendation` notification.
   - **Rejected:** the group stays hidden, and the creator sees `review_status: "rejected"` with `rejection_reason` in `GET /me/groups`.

New fields on every `GroupSummary` / `GroupDetail`: `review_status` (`pending` | `approved` | `rejected`) and `rejection_reason` (null unless rejected).

## Admin requests (per group)

- Group profile (`GET /groups/{id}`) has `admin_request_status` (`"pending"` while the student's own request waits) and, for the group's admins, `pending_admin_requests` (a count).
- A member who isn't admin: `POST /groups/{id}/admin-requests` with an optional `{"note": "..."}` (max 300). 409 if already admin or a request is open.
- The group's admins: `GET /groups/{id}/admin-requests` (oldest first), then `POST /groups/{id}/admin-requests/{request_id}/approve` or `/reject`.
- Notifications of type `admin_request` go to the admins (new request) and to the member (decision); `data.status` is `pending`, `approved` or `rejected`, and `group_id` opens the group.

## Other endpoints

| Screen | Endpoints |
|---|---|
| Events | `GET /events?mine=true`, `GET /events/{id}` |
| Create group / event | `POST /groups` (pending until approved, see above), `PATCH /groups/{id}`, `POST /groups/{id}/events` (approved groups only), `PATCH /events/{id}` |
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
| `dev:admin@uniandes.edu.co` | Platform admin: business questions, releases, jobs, group review |
