"""The web app: receives your texts and replies.

`build_app(deps)` makes a FastAPI app from injected services (easy to test).
`create_app()` wires the real services from settings (used to run the server):

    uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import base64
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import BackgroundTasks, Body, FastAPI, Form, Header, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.canvas import is_real_class, short_course_code
from app.webchat import set_active_chat, reset_active_chat

STATIC_DIR = Path(__file__).parent / "static"

# Pick a sensible file extension from a WhatsApp media content-type.
_EXT_BY_TYPE = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "video/mp4": ".mp4", "application/zip": ".zip",
}


@dataclass
class AppDeps:
    sms: Any                 # SmsClient-like: is_allowed(), send(), download_media()
    brain: Any               # Brain-like: respond(text, history)
    conversation: Any        # ConversationStore
    study: Any               # StudyPageStore
    require_signature: bool
    validate: Callable[[str, dict, str], bool]
    public_sms_url: str = ""  # the public URL Twilio signs against
    public_base_url: str = ""  # this app's public root (for /file links)
    on_started: Callable[[], None] | None = None
    reminders: Any = None     # ReminderService (for the CLEAR command)
    onedrive: Any = None      # OneDriveClient (file bridge to the laptop folder)
    files: Any = None         # FileStore (serves outbound files to Twilio)
    webchat: Any = None       # WebChatStore (the web app's visible transcript)
    web_chat_secret: str = "" # passcode gating the web chat app
    demo_mode: bool = False   # public demo: open the chat, serve fake sample data
    push: Any = None          # PushService (browser notifications when app is closed)
    vapid_public_key: str = ""# the public key the browser subscribes with
    cancels: Any = None       # set[str] of cancelled web generation ids (lazily created)
    canvas: Any = None        # CanvasClient — lets the opening message list real classes
    study_service: Any = None # StudyService — regenerates a quiz/deck on demand
    chats: Any = None         # ChatStore — multiple conversations (web app)
    study_progress: Any = None # StudyProgressStore — flashcard SR + quiz attempts
    lectures: Any = None      # LectureStore — saved Panopto lecture transcripts
    transcriber: Any = None   # Transcriber — Whisper transcription of recordings
    notifications: Any = None # NotificationService — scheduled digests/alerts


def _filename_for(content_type: str, index: int = 0) -> str:
    ext = _EXT_BY_TYPE.get((content_type or "").split(";")[0].strip().lower(), "")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return f"{stamp}{f'-{index + 1}' if index else ''}{ext}"


def _handle_media(deps: AppDeps, media: list) -> None:
    """A file came in over WhatsApp — save it to the OneDrive folder."""
    if deps.onedrive is None:
        deps.sms.send("file sharing isn't set up yet.")
        return
    saved = []
    for i, (url, ctype) in enumerate(media):
        try:
            data, real_ctype = deps.sms.download_media(url)
            name = _filename_for(real_ctype or ctype, i)
            deps.onedrive.upload(name, data, real_ctype or ctype)
            saved.append(name)
        except Exception:
            pass
    if saved:
        deps.sms.send(
            f"saved {', '.join(saved)} to your OneDrive '{deps.onedrive.folder}' folder "
            "(it'll show up on your laptop once OneDrive syncs)"
        )
    else:
        deps.sms.send("hmm, couldn't save that file — mind trying again?")


def _handle_command(deps: AppDeps, body: str) -> bool:
    """If the message is a command, handle it (and send any reply). Returns True
    when handled, False when it should fall through to the brain."""
    cmd = " ".join(body.strip().lower().split())  # normalized

    if cmd in ("clear", "clear chat"):
        deps.conversation.clear()
        deps.sms.send(
            "cleared the chat — i've forgotten our conversation. (heads up: i can "
            "only reset my own memory; i can't delete the messages from your whatsapp.)"
        )
        return True
    if cmd in ("clear reminders", "clear all reminders"):
        n = deps.reminders.clear_all() if deps.reminders is not None else 0
        deps.sms.send(f"cleared your reminders — {n} cancelled.")
        return True
    if cmd in ("clear all", "clear everything", "reset"):
        deps.conversation.clear()
        deps.study.clear()
        if deps.reminders is not None:
            deps.reminders.clear_all()
        deps.sms.send("cleared everything: chat, reminders, and study pages. fresh start.")
        return True

    if cmd in ("files", "list files", "list", "my files") and deps.onedrive is not None:
        names = [f["name"] for f in deps.onedrive.list_files()]
        deps.sms.send(
            "your folder's empty — send me a file, or drop one in the OneDrive "
            f"'{deps.onedrive.folder}' folder on your laptop."
            if not names else "files: " + ", ".join(names)
        )
        return True

    if (cmd.startswith("send ") or cmd.startswith("get ")) and deps.onedrive is not None:
        query = body.strip().split(None, 1)[1].strip()
        got = deps.onedrive.download(query)
        if got:
            data, ctype, real_name = got
            fid = uuid.uuid4().hex
            deps.files.save(fid, real_name, ctype, data)
            deps.sms.send(
                f"here's {real_name}:", media_url=[f"{deps.public_base_url}/file/{fid}"]
            )
            return True
        # Only claim it as a file request if it looks like a filename; otherwise
        # let the brain answer things like "get me my grade".
        if re.search(r"\.\w{1,5}$", query):
            deps.sms.send(
                f"couldn't find '{query}' in your folder. text 'files' to see what's there."
            )
            return True
        return False

    return False


TYPING_REFRESH_SECONDS = 20  # each WhatsApp indicator lasts ~25s, so refresh before then


def _keep_typing(deps: AppDeps, message_sid: str, stop: threading.Event) -> None:
    """Keep the WhatsApp 'typing…' animation up until `stop` is set.

    Refreshes every ~20s since each indicator only lasts ~25s. If the native
    indicator isn't available (e.g. the sandbox), fall back to one quick text
    so a slow reply never looks like nothing's happening."""
    if not deps.sms.send_typing(message_sid):
        deps.sms.send("on it")
        return
    while not stop.wait(TYPING_REFRESH_SECONDS):
        if not deps.sms.send_typing(message_sid):
            return


def _process_incoming(
    deps: AppDeps,
    body: str,
    media: list | None = None,
    message_sid: str = "",
    attachments: list | None = None,
    cancel_check=None,
    history: list | None = None,
    save_memory: bool = True,
) -> None:
    """The real work, run in the background after we've ack'd Twilio.

    `history` overrides the brain's memory source (the web app passes the active
    chat's history). `save_memory=False` skips ConversationStore writes (the web
    app's per-chat transcript is its own memory)."""
    # Show a 'typing…' animation while we dig, so a slow reply doesn't look dead.
    stop = threading.Event()
    typer = None
    if message_sid and getattr(deps.sms, "channel", "") == "whatsapp":
        typer = threading.Thread(
            target=_keep_typing, args=(deps, message_sid, stop), daemon=True
        )
        typer.start()

    try:
        if media:
            _handle_media(deps, media)
            return
        if _handle_command(deps, body):
            return

        if history is None:
            history = deps.conversation.recent()
        reply = deps.brain.respond(body, history=history, attachments=attachments)
        # If the user hit "stop" while we were thinking, drop the reply entirely.
        if cancel_check is not None and cancel_check():
            return
        if save_memory:
            deps.conversation.save("user", body)
            deps.conversation.save("assistant", reply)
        deps.sms.send(reply)
    finally:
        stop.set()
        if typer is not None:
            typer.join(timeout=2)


class ChatAttachment(BaseModel):
    name: str = ""
    content_type: str = ""
    data: str = ""  # base64-encoded file bytes


class ChatIn(BaseModel):
    text: str = ""
    attachments: list[ChatAttachment] = []
    gen_id: str = ""  # client-chosen id so this turn can be cancelled mid-flight
    chat_id: int | None = None  # which conversation this belongs to


class CancelIn(BaseModel):
    gen_id: str = ""


class TranscribeIn(BaseModel):
    audio: str = ""           # base64-encoded recorded clip
    content_type: str = ""    # e.g. audio/webm
    name: str = "speech.webm"


class LectureIn(BaseModel):
    title: str = ""
    text: str = ""  # pasted transcript
    attachments: list[ChatAttachment] = []  # transcript file(s) and/or a recording


_AV_EXTS = {"mp3", "m4a", "mp4", "wav", "webm", "ogg", "oga", "mpeg", "mpga", "mov", "flac", "aac"}


def _is_av(name: str, content_type: str) -> bool:
    """True for audio/video that needs transcription (vs. a text transcript)."""
    ct = (content_type or "").lower()
    if ct.startswith("audio/") or ct.startswith("video/"):
        return True
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    return ext in _AV_EXTS


# Setup page for the one-click "Add to Dubly" bookmarklet. The bookmarklet runs
# ON the Panopto page, fetches the official caption transcript (same-origin, using
# the student's login), opens Dubly, and postMessages the transcript across. The
# Dubly origin is injected client-side via location.origin (replaces __DUBLY__).
_BOOKMARKLET_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Add to Dubly — bookmarklet</title>
<style>body{font-family:ui-sans-serif,system-ui,Arial;background:#efe9fb;color:#2a2342;margin:0;padding:32px;line-height:1.6}
.card{max-width:560px;margin:0 auto;background:#fff;border-radius:18px;padding:28px;box-shadow:0 10px 40px rgba(80,50,140,.15)}
h1{margin:0 0 6px}.bm{display:inline-block;background:linear-gradient(180deg,#7a5fc4,#9279d6);color:#fff;
text-decoration:none;font-weight:700;padding:12px 20px;border-radius:12px;margin:14px 0;cursor:grab}
ol{padding-left:20px}b{color:#5b3aa0}</style></head>
<body><div class="card">
<h1>Add to Dubly</h1>
<p>One click to send a UW Panopto lecture's transcript straight to Dubly.</p>
<p><b>Set up once:</b> drag this button up to your bookmarks bar:</p>
<p><a class="bm" id="bm" href="#">Add to Dubly</a></p>
<p><b>Then, on any lecture:</b></p>
<ol><li>Open the lecture in Panopto (the viewer page).</li>
<li>Click the <b>Add to Dubly</b> bookmark.</li>
<li>It grabs the transcript and saves it to Dubly for you.</li></ol>
<p style="color:#6b6385;font-size:14px">No captions on a video? Use Dubly's <b>⋯ Add lecture</b> and paste the transcript instead.</p>
</div>
<script>
var BM="javascript:(function(){var D='__DUBLY__';var m=location.href.match(/[?&]id=([0-9a-fA-F-]{20,})/);if(!m){alert('Open a Panopto lecture (the viewer page) first, then click this.');return;}fetch('/Panopto/Pages/Transcription/GenerateSRT.ashx?id='+m[1]+'&language=0',{credentials:'include'}).then(function(r){return r.text();}).then(function(s){var t=s.split(/\\r?\\n/).filter(function(l){l=l.trim();return l&&!/^\\d+$/.test(l)&&l.indexOf('-->')<0;}).join(' ').replace(/\\s+/g,' ').trim();if(!t){alert('No captions found for this lecture. Use Add lecture in Dubly and paste the transcript.');return;}var ti=(document.title||'Lecture').replace(/\\s*[-|].*$/,'').trim()||'Lecture';var w=window.open(D+'/chat?addlecture=1','dubly');var sent=false,iv;function go(){if(sent||!w)return;try{w.postMessage({type:'dubly-lecture',title:ti,transcript:t},D);sent=true;if(iv)clearInterval(iv);}catch(e){}}window.addEventListener('message',function(e){if(e.origin===D&&e.data&&e.data.type==='dubly-ready')go();});var n=0;iv=setInterval(function(){n++;if(n>20){clearInterval(iv);return;}go();},500);}).catch(function(){alert('Could not fetch the transcript. Make sure the lecture has captions, or paste it via Add lecture.');});})();";
document.getElementById('bm').setAttribute('href', BM.replace('__DUBLY__', location.origin));
</script></body></html>'''


class RenameIn(BaseModel):
    title: str = ""


def _web_authed(deps: AppDeps, key: str) -> bool:
    """The web chat is gated by a shared passcode (not a phone whitelist).

    In demo mode there's no real data to protect, so the chat is open to anyone."""
    if deps.demo_mode:
        return True
    return bool(deps.web_chat_secret) and key == deps.web_chat_secret


_CLEAR_CMDS = {"clear", "clear chat", "clear all", "clear everything", "reset"}

GREETING = (
    "hey, i'm Dubly, your husky study buddy. ask me what's due, your grades, the syllabus, "
    "anything canvas, or i can build you a study guide, quiz, or add a lecture."
)


def _course_label(course) -> str:
    """A clean "CSE 163: Intermediate Data Programming" from Canvas's messy code+name."""
    short = short_course_code(course.code or "")
    # Canvas names are like "CSE 163 A Sp 26: Intermediate Data Programming";
    # the real title is after the colon (some sites have no title).
    title = course.name.split(":", 1)[-1].strip() if ":" in course.name else ""
    if title and short.lower() not in title.lower():
        return f"{short}: {title}"
    return short


def _greeting_text(deps: AppDeps) -> str:
    """The opening hello. Greets the student by name and lists their classes."""
    first, courses = "", []
    if deps.canvas is not None:
        try:
            full = deps.canvas.get_user_name() if hasattr(deps.canvas, "get_user_name") else ""
            first = (full or "").split()[0] if full else ""
        except Exception:
            first = ""
        try:
            courses = [c for c in deps.canvas.list_courses() if is_real_class(c.code)]
        except Exception:
            courses = []
    hello = f"hey {first}" if first else "hey"
    if not courses:
        return (
            f"{hello} i'm Dubly, your husky study buddy. ask me what's due, your grades, the "
            "syllabus, anything canvas, or i can build you a study guide, quiz, or add a lecture."
        )
    lines = "\n".join(f"• {_course_label(c)}" for c in courses)
    return (
        f"{hello} i'm Dubly. here are the classes i see you're enrolled in this quarter:\n"
        f"{lines}\n\n"
        "ask me what's due, your grades, the syllabus, anything canvas, "
        "or i can build you a study guide, quiz, or add a lecture."
    )


def _ensure_greeting(deps: AppDeps, chat_id: int) -> None:
    """Seed a hello so a fresh/empty chat greets you first."""
    if deps.chats is not None and deps.chats.max_id(chat_id) == 0:
        deps.chats.append(chat_id, "assistant", _greeting_text(deps))


def build_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(title="Study Assistant")

    # Tracks web generations the user cancelled (hit "stop") so their late
    # replies are discarded instead of popping into the transcript.
    if deps.cancels is None:
        deps.cancels = set()

    if deps.on_started is not None:
        @app.on_event("startup")
        def _startup():
            deps.on_started()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/sms")
    async def sms_webhook(
        background: BackgroundTasks,
        request: Request,
        Body: str = Form(""),
        From: str = Form(""),
    ):
        form = dict((await request.form()))

        # 1. Make sure it's really Twilio (not a faker).
        if deps.require_signature:
            signature = request.headers.get("X-Twilio-Signature", "")
            url = deps.public_sms_url or str(request.url)
            if not deps.validate(url, form, signature):
                return PlainTextResponse("forbidden", status_code=403)

        # 2. Only answer my own number; ignore everyone else.
        if not deps.sms.is_allowed(From):
            return Response(content="", media_type="application/xml")

        # 3. Pull any attached files (WhatsApp media).
        media = []
        try:
            num_media = int(form.get("NumMedia", "0") or 0)
        except ValueError:
            num_media = 0
        for i in range(num_media):
            murl = form.get(f"MediaUrl{i}")
            if murl:
                mtype = form.get(f"MediaContentType{i}", "application/octet-stream")
                media.append((murl, mtype))

        # 4. Ack instantly; do the slow work in the background. Pass the inbound
        #    message SID so we can show a WhatsApp 'typing…' indicator while we work.
        message_sid = (
            form.get("MessageSid") or form.get("SmsMessageSid") or form.get("SmsSid") or ""
        )
        background.add_task(_process_incoming, deps, Body, media, message_sid)
        return Response(content="", media_type="application/xml")

    @app.api_route("/voice", methods=["GET", "POST"])
    def voice():
        # Temporary helper: when a call comes in (e.g. Meta's WhatsApp verification
        # call), answer and record + transcribe it so we can read the spoken code.
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Pause length=\"1\"/>"
            "<Record maxLength=\"40\" playBeep=\"false\" transcribe=\"true\" timeout=\"30\"/>"
            "</Response>"
        )
        return Response(content=twiml, media_type="text/xml")

    @app.get("/study/{page_id}", response_class=HTMLResponse)
    def study_page(page_id: str):
        html = deps.study.get(page_id)
        if html is None:
            return HTMLResponse("Not found", status_code=404)
        return HTMLResponse(html)

    @app.post("/study/{page_id}/regenerate")
    def study_regenerate(page_id: str):
        """Rebuild a quiz/deck with a fresh set (the page's 'new questions' button)."""
        if deps.study_service is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if not deps.study_service.regenerate(page_id):
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"ok": True}

    # ---- study progress: flashcard spaced repetition + quiz attempts --------

    @app.get("/study/{page_id}/progress")
    def get_study_progress(page_id: str):
        if deps.study_progress is None:
            return {"boxes": {}}
        return {"boxes": deps.study_progress.get_boxes(page_id)}

    @app.post("/study/{page_id}/progress")
    def post_study_progress(page_id: str, payload: dict = Body(...)):
        if deps.study_progress is None:
            return {"ok": False}
        box = deps.study_progress.rate_card(page_id, int(payload.get("card", 0)), bool(payload.get("knew")))
        return {"ok": True, "box": box}

    @app.get("/study/{page_id}/attempts")
    def get_study_attempts(page_id: str):
        if deps.study_progress is None:
            return {"count": 0, "best": 0, "list": []}
        return deps.study_progress.attempts(page_id)

    @app.post("/study/{page_id}/attempt")
    def post_study_attempt(page_id: str, payload: dict = Body(...)):
        if deps.study_progress is None:
            return {"ok": False}
        deps.study_progress.add_attempt(
            page_id, int(payload.get("score", 0)), int(payload.get("total", 0)),
            int(payload.get("missed", 0)),
        )
        return {"ok": True}

    @app.get("/file/{file_id}")
    def serve_file(file_id: str):
        rec = deps.files.get(file_id) if deps.files is not None else None
        if rec is None:
            return Response(content="Not found", status_code=404)
        filename, ctype, data = rec
        return Response(
            content=data,
            media_type=ctype,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    # ---- Web chat app (your own private "texting" interface) ----------------

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def root():
        # The app lives at /chat; send the bare domain there so the link "just works".
        return RedirectResponse(url="/chat")

    @app.get("/chat")
    def chat_page():
        # no-cache: always revalidate so a redeploy's new inline JS lands on the
        # next load instead of being served stale from the browser's HTTP cache.
        return FileResponse(
            STATIC_DIR / "chat.html",
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/chat/config")
    def chat_config(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        push_on = deps.push is not None and getattr(deps.push, "enabled", False)
        return {
            "vapidPublicKey": deps.vapid_public_key if push_on else "",
            "demo": deps.demo_mode,
        }

    @app.post("/chat/subscribe")
    def chat_subscribe(sub: dict = Body(...), x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.push is not None and sub.get("endpoint"):
            deps.push.store.save(sub)
            # Immediately confirm with a real notification (shown even if focused).
            deps.push.notify(
                "Study Assistant", "Notifications are on. You're all set.", force=True
            )
        return {"ok": True}

    @app.get("/chat/notifications")
    def notifications_list(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.notifications is None:
            return {"rules": []}
        return {"rules": deps.notifications.list_rules()}

    @app.post("/chat/notifications")
    def notifications_add(payload: dict = Body(...), x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.notifications is None:
            return JSONResponse({"error": "notifications unavailable"}, status_code=400)
        kind = (payload.get("kind") or "").strip().lower()
        if kind not in ("daily", "weekly", "due"):
            return JSONResponse({"error": "bad kind"}, status_code=400)
        rule = deps.notifications.add_rule(
            kind=kind,
            time=payload.get("time", ""),
            weekday=payload.get("weekday", ""),
            hours_before=payload.get("hours_before", 24),
            message=payload.get("message", ""),
        )
        from app.notifications import describe
        return {"id": rule["id"], "label": describe(rule)}

    @app.post("/chat/notifications/{rule_id}/toggle")
    def notifications_toggle(rule_id: str, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.notifications is None:
            return JSONResponse({"error": "notifications unavailable"}, status_code=400)
        return {"ok": deps.notifications.toggle(rule_id)}

    @app.delete("/chat/notifications/{rule_id}")
    def notifications_delete(rule_id: str, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.notifications is None:
            return JSONResponse({"error": "notifications unavailable"}, status_code=400)
        return {"ok": deps.notifications.remove_rule(rule_id)}

    @app.get("/chat/pushdebug")
    def push_debug(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        subs = deps.push.store.all() if deps.push is not None else []
        return {
            "subscriptions": len(subs),
            "enabled": bool(deps.push is not None and getattr(deps.push, "enabled", False)),
            "vapid_tail": (deps.vapid_public_key or "")[-10:],
            "endpoints": [(s.get("endpoint", "") or "")[-18:] for s in subs],
        }

    @app.post("/chat/testpush")
    def test_push(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.push is None:
            return {"results": [{"error": "no push service"}]}
        return {"results": deps.push.send_sync("Study Assistant", "test notification", force=True)}

    # ---- Conversations (ChatGPT-style: list / new / rename / delete) ---------

    @app.get("/chats")
    def list_chats(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        deps.chats.ensure_chat()  # never present an empty sidebar
        return {"chats": deps.chats.list_chats()}

    @app.get("/chats/search")
    def search_chats(q: str = "", x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        q = (q or "").strip()
        chats = deps.chats.search(q) if q else deps.chats.list_chats()
        return {"chats": chats}

    @app.post("/chats")
    def new_chat(x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        cid = deps.chats.create_chat()
        _ensure_greeting(deps, cid)  # a brand-new chat greets you right away
        return {"id": cid, "title": deps.chats.title_of(cid)}

    @app.patch("/chats/{chat_id}")
    def rename_chat(chat_id: int, payload: RenameIn, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.chats.title_of(chat_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        deps.chats.rename(chat_id, payload.title)
        return {"id": chat_id, "title": deps.chats.title_of(chat_id)}

    @app.delete("/chats/{chat_id}")
    def delete_chat(chat_id: int, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        deps.chats.delete(chat_id)
        return {"ok": True}

    def _resolve_chat(chat_id: int | None) -> int:
        """For reads: use the given chat, else fall back to the most recent."""
        if chat_id and deps.chats.title_of(chat_id) is not None:
            return chat_id
        return deps.chats.ensure_chat()

    def _resolve_chat_for_write(chat_id: int | None) -> int:
        """For writes: never hijack another chat. A missing id starts a fresh chat
        so a stale/bogus id can't inject messages into an unrelated conversation."""
        if chat_id is None:
            return deps.chats.ensure_chat()
        if deps.chats.title_of(chat_id) is not None:
            return chat_id
        return deps.chats.create_chat()

    @app.get("/chat/messages")
    def chat_messages(after: int = 0, chat_id: int | None = None,
                      x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        cid = _resolve_chat(chat_id)
        if after == 0:
            _ensure_greeting(deps, cid)  # first open of a fresh chat → say hello
        return {"chat_id": cid, "messages": deps.chats.since(cid, after)}

    @app.post("/chat/send")
    def chat_send(payload: ChatIn, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        cid = _resolve_chat_for_write(payload.chat_id)
        text = (payload.text or "").strip()
        # Decode any uploaded files (base64) into (name, content_type, bytes).
        files: list = []
        for a in payload.attachments or []:
            try:
                raw = base64.b64decode(a.data or "")
            except Exception:
                continue
            if raw:
                files.append((a.name or "file", a.content_type or "", raw))
        if not text and not files:
            return {"chat_id": cid, "messages": []}
        norm = " ".join(text.lower().split())
        # "clear" empties THIS chat (and re-greets); uploads are never commands.
        if text and not files and norm in _CLEAR_CMDS:
            deps.chats.clear(cid)
            if norm in ("clear all", "clear everything", "reset"):
                if deps.reminders is not None:
                    deps.reminders.clear_all()
                if deps.study is not None:
                    deps.study.clear()
                if deps.lectures is not None:
                    deps.lectures.clear()
            _ensure_greeting(deps, cid)
            return {"chat_id": cid, "messages": deps.chats.since(cid, 0), "cleared": True}
        # Brain memory = this chat's prior messages (captured before this turn).
        history = deps.chats.recent_for_brain(cid)
        start = deps.chats.max_id(cid)
        bubble = text
        if files:
            note = ", ".join(name for name, _, _ in files)
            bubble = f"{text}\n{note}" if text else note
        deps.chats.append(cid, "user", bubble)
        # Route the reply (and any tool-sent files/reminders) into THIS chat.
        token = set_active_chat(cid)
        try:
            gid = (payload.gen_id or "").strip()
            cancel_check = (lambda: gid in deps.cancels) if gid else None
            _process_incoming(
                deps, text, attachments=files or None, cancel_check=cancel_check,
                history=history, save_memory=False,
            )
        finally:
            reset_active_chat(token)
            if (payload.gen_id or "").strip():
                deps.cancels.discard(payload.gen_id.strip())
        return {"chat_id": cid, "messages": deps.chats.since(cid, start)}

    @app.post("/chat/cancel")
    def chat_cancel(payload: CancelIn, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        gid = (payload.gen_id or "").strip()
        if gid:
            deps.cancels.add(gid)
        return {"ok": True}

    @app.post("/chat/transcribe")
    def chat_transcribe(payload: TranscribeIn, x_chat_key: str = Header(default="")):
        """Voice input: transcribe a short recorded clip with Whisper. More
        reliable than the Web Speech API (which doesn't work on iOS)."""
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.transcriber is None or not getattr(deps.transcriber, "enabled", False):
            return JSONResponse({"error": "voice input isn't set up here"}, status_code=400)
        from app.transcribe import TranscribeError
        try:
            raw = base64.b64decode(payload.audio or "")
        except Exception:
            raw = b""
        if not raw:
            return JSONResponse({"error": "no audio captured"}, status_code=400)
        try:
            text = deps.transcriber.transcribe(payload.name or "speech.webm", raw)
        except TranscribeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"text": text}

    @app.get("/lectures/bookmarklet", response_class=HTMLResponse)
    def lecture_bookmarklet():
        return _BOOKMARKLET_PAGE

    @app.post("/lectures")
    def add_lecture(payload: LectureIn, x_chat_key: str = Header(default="")):
        if not _web_authed(deps, x_chat_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if deps.lectures is None:
            return JSONResponse({"error": "lectures aren't available here"}, status_code=503)
        import uuid
        from datetime import datetime, timezone
        from app.attachments import extract_text
        from app.transcribe import TranscribeError

        title = (payload.title or "").strip() or "Untitled lecture"
        parts: list[str] = []
        if (payload.text or "").strip():
            parts.append(payload.text.strip())
        source = "transcript"
        for a in payload.attachments or []:
            try:
                raw = base64.b64decode(a.data or "")
            except Exception:
                continue
            if not raw:
                continue
            if _is_av(a.name, a.content_type):
                if deps.transcriber is None or not getattr(deps.transcriber, "enabled", False):
                    return JSONResponse(
                        {"error": "audio transcription isn't set up here — paste or upload "
                                  "the lecture transcript/captions instead."},
                        status_code=400,
                    )
                try:
                    text = deps.transcriber.transcribe(a.name or "lecture", raw)
                except TranscribeError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=400)
                source = "recording"
                if text:
                    parts.append(text)
            else:
                text = extract_text(a.name or "file", a.content_type or "", raw)
                if text:
                    parts.append(text)
        transcript = "\n\n".join(p for p in parts if p).strip()
        if not transcript:
            return JSONResponse(
                {"error": "couldn't read any transcript — paste the text or attach a "
                          ".txt/.vtt/.srt/.pdf/.docx (or a recording)."},
                status_code=400,
            )
        lid = uuid.uuid4().hex
        deps.lectures.save(
            lid, title, transcript, source,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return {"id": lid, "title": title, "chars": len(transcript), "source": source}

    return app


def create_app() -> FastAPI:
    """Wire the real services from settings and return the app."""
    from app.config import load_settings
    from app.canvas import CanvasClient
    from app.sms import SmsClient
    from app.tools import ToolBox
    from app.brain import Brain, SYSTEM_PROMPT
    from app.db import (
        ConversationStore,
        StudyPageStore,
        FileStore,
        ChatStore,
        PushStore,
        AlertStore,
        StudyProgressStore,
        LectureStore,
        NotificationStore,
    )
    from app.transcribe import Transcriber
    from app.alerts import AlertService
    from app.reminders import ReminderService
    from app.notifications import NotificationService
    from app.study import StudyService
    from app.onedrive import OneDriveClient
    from app.documents import DocumentService
    from app.webchat import WebClient
    from app.push import PushService

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    import anthropic

    settings = load_settings(require_secrets=True)

    if settings.demo_mode:
        from app.demo_canvas import DemoCanvasClient
        canvas = DemoCanvasClient(settings.canvas_base_url)
    else:
        canvas = CanvasClient(settings.canvas_base_url, settings.canvas_token)
    conversation = ConversationStore(settings.data_dir / "conversation.sqlite")
    study_store = StudyPageStore(settings.data_dir / "study.sqlite")
    lecture_store = LectureStore(settings.data_dir / "lectures.sqlite")
    transcriber = Transcriber(api_key=settings.openai_api_key)
    file_store = FileStore(settings.data_dir / "files.sqlite")
    chat_store = ChatStore(settings.data_dir / "chats.sqlite")
    push_store = PushStore(settings.data_dir / "push.sqlite")
    push_service = PushService(
        push_store, settings.vapid_private_key, settings.vapid_claim_email
    )

    # The channel client the brain/documents/reminders push messages through.
    # "web" routes everything into the active web chat; otherwise it's Twilio.
    if settings.channel == "web":
        sms = WebClient(chat_store, push=push_service)
    else:
        sms = SmsClient(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
            my_number=settings.my_phone_number,
            channel=settings.channel,
            whatsapp_from=settings.whatsapp_from,
        )

    onedrive = None
    if settings.onedrive_refresh_token:
        onedrive = OneDriveClient(
            client_id=settings.onedrive_client_id,
            refresh_token=settings.onedrive_refresh_token,
            tenant=settings.onedrive_tenant,
            folder=settings.onedrive_folder,
            token_path=str(settings.data_dir / "onedrive_token.txt"),
        )

    # Persistent scheduler so reminders survive restarts.
    jobs_path = settings.data_dir / "reminders.sqlite"
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{jobs_path}")},
        timezone="UTC",
    )
    reminders = ReminderService(scheduler=scheduler, sms=sms)

    # Proactive alerts: poll Canvas for new grades / due-soon items and push them.
    alerts = AlertService(
        canvas=canvas,
        push=push_service,
        store=AlertStore(settings.data_dir / "alerts.sqlite"),
    )

    # Student-configured notifications: daily/weekly digests, due-soon alerts,
    # one-off "remind me in N min". Shares the persistent scheduler.
    notifications = NotificationService(
        scheduler=scheduler,
        store=NotificationStore(settings.data_dir / "notifications.sqlite"),
        canvas=canvas,
        chats=chat_store,
        push=push_service,
    )

    def _on_started():
        scheduler.start()
        alerts.start()
        notifications.start()

    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    study = StudyService(
        canvas=canvas,
        client=anthropic_client,
        model=settings.anthropic_model,
        pages=study_store,
        public_base_url=settings.public_base_url,
        lectures=lecture_store,
    )

    documents = DocumentService(
        sms=sms,
        files=file_store,
        public_base_url=settings.public_base_url,
        onedrive=onedrive,
    )
    toolbox = ToolBox(canvas=canvas, reminders=reminders, study=study, documents=documents, lectures=lecture_store, notifications=notifications)
    # Personalize: greet the student by name instead of a generic "Dawg".
    try:
        _full = canvas.get_user_name()
        _first = _full.split()[0] if _full else ""
    except Exception:
        _first = ""
    sys_prompt = SYSTEM_PROMPT
    if _first:
        sys_prompt += (
            f"\n- The student's name is {_first}. When you greet them, use their name "
            f'("hey {_first}"); never call them "Dawg".'
        )
    brain = Brain(
        client=anthropic_client,
        model=settings.brain_model,
        toolbox=toolbox,
        system_prompt=sys_prompt,
    )

    deps = AppDeps(
        sms=sms,
        brain=brain,
        conversation=conversation,
        study=study_store,
        require_signature=True,
        validate=sms.validate_signature,
        public_sms_url=f"{settings.public_base_url}/sms",
        public_base_url=settings.public_base_url,
        on_started=_on_started,
        reminders=reminders,
        onedrive=onedrive,
        files=file_store,
        web_chat_secret=settings.web_chat_secret,
        demo_mode=settings.demo_mode,
        push=push_service,
        vapid_public_key=settings.vapid_public_key,
        canvas=canvas,
        study_service=study,
        chats=chat_store,
        study_progress=StudyProgressStore(settings.data_dir / "study_progress.sqlite"),
        lectures=lecture_store,
        transcriber=transcriber,
        notifications=notifications,
    )
    return build_app(deps)
