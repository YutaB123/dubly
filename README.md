# 🐺 Dubly

**Your husky study buddy — wired to your UW Canvas. Go Dawgs.**

Dubly is a chat app connected to your real University of Washington Canvas
account, so it answers from your actual classes, assignments, grades, and due
dates. Ask it anything about your courses and it can also **generate quizzes,
flashcards, study plans, and Word documents** on the spot — then you study right
inside the chat.

Powered by Anthropic's Claude. Talk to it through the **web app** (a husky-themed
PWA) or, optionally, over **SMS**.

## What it does

- **Knows your quarter** — on open, it lists the classes you're actually enrolled in.
- **Answers from Canvas** — "what's due this week?", "what's my grade in stats?",
  "what's on my philosophy syllabus?" — all from your real data.
- **Makes quizzes** — generates a multiple-choice quiz for any class, with instant
  answer checking and explanations.
- **Makes flashcards** — turns a topic or reading into a study deck you flip through.
- **Drafts documents** — builds a Word doc (e.g. "things to know for my CSE 163
  presentation") you can download.
- **Reminders & push** — schedules reminders and can send web-push notifications.

## What's inside

```
app/
  main.py        FastAPI app — serves /chat (web UI), /sms webhook, /study pages, push, health
  webchat.py     the web chat backend (the husky PWA)
  brain.py       Claude's personality + the tool-calling loop
  canvas.py      UW Canvas API — courses, assignments, grades, due dates, syllabus
  tools.py       the tools Claude can call + how Canvas data is formatted
  study.py       quizzes / flashcards / study pages
  documents.py   generates downloadable Word documents
  reminders.py   schedule reminders (survives restarts)
  push.py        web-push notifications
  onedrive.py    OneDrive integration for saved files
  sms.py         optional SMS channel via Twilio
  db.py          small local store (chat memory + generated study material)
  config.py      settings loaded from .env
  static/        chat.html, the husky avatar, PWA manifest + service worker, icons
tests/           the test suite
```

The bare domain redirects to **`/chat`**, where the app lives.

## Setup

1. **Install** (Python 3.11+):
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
2. **Configure** — copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY` — from console.anthropic.com
   - `CANVAS_TOKEN` — UW Canvas → Account → Settings → New Access Token
   - `PUBLIC_BASE_URL` — your public URL (a tunnel locally, the host URL when deployed)
   - *Optional (SMS):* `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `MY_PHONE_NUMBER`

   Keys live only in `.env`, which is git-ignored — nothing secret is committed.

## Demo mode

Set **`DEMO_MODE=true`** to serve a fake sample student — believable UW classes,
grades, due dates, and syllabi — instead of a real Canvas account. Quizzes,
flashcards, and documents still generate for real. This is what powers the public
"try it live" link, so no one's actual Canvas data is exposed. In demo mode
`CANVAS_TOKEN` isn't needed (only `ANTHROPIC_API_KEY`).

## Run it locally

```
.venv\Scripts\uvicorn app.main:create_app --factory --reload
```

Open **http://localhost:8000/** — it redirects to the chat. For push
notifications or SMS testing, expose it with a tunnel (`ngrok http 8000`) and set
`PUBLIC_BASE_URL` to the HTTPS tunnel URL.

*(SMS, optional)* point your Twilio number's **Messaging webhook** at
`https://<your-tunnel>/sms`, then text it "what's due this week?".

## Run the tests

```
.venv\Scripts\pytest
```

## Deploy (always-on)

Push to a cloud host (Render / Railway / Fly.io). The `Procfile` has the start
command. After deploying:

- Set the same environment variables in the host's dashboard.
- Set `PUBLIC_BASE_URL` to the host's URL.
- *(If using SMS)* point the Twilio Messaging webhook at `https://<host>/sms`.

Two notes:

- **Run a single instance** — reminders are scheduled in-process, so multiple
  copies would double-fire them.
- **US SMS registration** — texting from a US number needs a one-time A2P 10DLC
  registration in Twilio (a trial number works right away for your own verified
  phone). Not needed if you only use the web chat.
