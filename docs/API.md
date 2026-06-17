# Study Assistant — Web API

For driving the app programmatically (e.g. a tester agent). The web chat UI uses these same endpoints.

## Base & auth
- **Base URL:** a deployment URL. In local dev it's the Cloudflare tunnel in `PUBLIC_BASE_URL`
  (e.g. `https://<random>.trycloudflare.com`). **This changes on every dev restart — read it from
  config, never hard-code.**
- **Auth:** every `/chat/*` endpoint requires header `X-Chat-Key: <WEB_CHAT_SECRET>`.
- **Content-Type:** `application/json`.
- **Latency:** `/chat/send` runs Canvas lookups + a Claude call; allow **5–30s** (timeouts ≥ 90s).
- **Single-user / shared state:** all requests hit the one configured Canvas account and a shared
  transcript. **Do not fire requests in parallel** — run sequentially.

## Endpoints

### `POST /chat/send`
Send a message; blocks until the reply is ready. Returns the new transcript rows (your message + reply).
```json
// request
{ "text": "what are my grades?", "gen_id": "t1", "attachments": [] }
// response 200
{ "messages": [
  { "id": 96, "role": "user",      "text": "what are my grades?", "media_url": "" },
  { "id": 97, "role": "assistant", "text": "CSE 163: 96.47%, STAT 311: 93.21%, PHIL 149: 87.5%.", "media_url": "" }
] }
```
- `gen_id` (optional): id so the turn can be cancelled.
- `attachments` (optional): `[{ "name": "hw.png", "content_type": "image/png", "data": "<base64>" }]`
  — images → vision, PDFs → read natively, Word/txt/html → extracted text.
- Empty `text` and no attachments → `{ "messages": [] }`.
- Bad/missing key → `401 {"error":"unauthorized"}`.

### `GET /chat/messages?after=<id>`
Poll for messages after `<id>`. `after=0` loads full history (and seeds the greeting on an empty chat).
```json
{ "messages": [ { "id": 98, "role": "assistant", "text": "…", "media_url": "" } ] }
```

### `POST /chat/cancel`
```json
{ "gen_id": "t1" }   // -> { "ok": true }
```
The matching `/chat/send` discards its reply (won't appear in the transcript or polling).

### `POST /study/{id}/regenerate`
No auth (the id is the secret). Rebuilds the quiz/deck with a fresh set from its stored recipe.
`200 {"ok":true}`; unknown id → `404 {"error":"not found"}`.

### `GET /study/{id}`
Returns the quiz/flashcard **HTML page** (not JSON). Quiz links arrive inside the assistant `text`.

### `GET /health`
No auth → `{ "ok": true, "model": "claude-..." }`.

---

## Test results (2026-06-16, full sweep — 23 scenarios)

**23/23 passing.** Verified live: health; auth 401s; Canvas Q&A (classes, grades, due, syllabus
link, nickname resolution, announcements, exams); multiple-choice / typed / flashcard study pages
render correct markers; regenerate + 404; cancel discards the reply; PNG (vision) and text-file
uploads; guardrails (read-only grade refusal, off-topic, empty input).

Good smoke-test prompts:
- `"what classes am I taking?"` → only `CSE 163, MATH 124, PHIL 149, STAT 311`.
- `"what are my grades?"` → `CLASS: %` list, no junk classes, no commentary.
- `"what's due this week?"`, `"what's on the PHIL 149 syllabus?"` (expect a canvas.uw.edu link).
- `"make me a multiple choice quiz for STAT 311"` → reply contains a `/study/<id>` link.

## Known issues / notes
- **Grades commentary — FIXED.** A grades reply previously ranked classes ("your highest",
  "close behind"). The system prompt now forbids ranking/comparisons; output is a bare
  `CLASS: %` list. Re-verified live.
- **Malformed upload → 422 with empty body.** If the JSON body is malformed (e.g. a client sends
  a stray newline in the base64), FastAPI returns `422` with **no body**, giving no validation
  detail. Well-formed requests are fine. Minor; could add a custom 422 handler later.
- **Stale transcript data.** The persisted chat log can still contain old assistant replies from
  before a behavior change (e.g. an old grades reply that named a non-class and editorialized).
  Live calls no longer produce these. Type `clear` to wipe the transcript; the opening message
  only regenerates on an empty chat.
- **Single-user.** Every request uses the one configured Canvas token — there is no per-user
  isolation yet (that's the multi-tenant work needed before a public launch).
