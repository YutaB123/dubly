"""The study-page maker: turn a course (or a topic within it) into flashcards or
a practice exam, publish it as a small interactive web page, and hand back a link.

Generating the cards/questions is its own Claude call (separate from the
conversational brain) using a forced JSON shape, so the result is structured.
The source material is a course-wide "study packet" (syllabus + topic outline +
assignments), so a final-exam deck reflects the whole course. The page is a
self-contained, full-screen flip-deck stored to be served at /study/{id} (and,
in the web app, opened in an in-app overlay rather than as a bare link).
"""

from __future__ import annotations

import json
import uuid

from jinja2 import Template
from markupsafe import Markup

# --- HTML deck (self-contained, no external assets) --------------------------

_DECK_TEMPLATE = Template(
    autoescape=True,
    source="""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>{{ title }}</title>
<style>
  :root { --bg:#efe9fb; --accent1:#8b6fd4; --accent2:#a98ee0; --text:#3a2f55; --muted:#8a7fa5; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; color:var(--text);
    background:radial-gradient(125% 75% at 50% -8%, #f3eefc 0%, #efe9fb 52%) fixed, var(--bg);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  #wrap { display:flex; flex-direction:column; height:100dvh;
    padding:max(env(safe-area-inset-top),12px) 14px calc(env(safe-area-inset-bottom) + 12px); }
  header { display:flex; align-items:baseline; gap:10px; }
  header .title { font-weight:600; font-size:16px; flex:1; min-width:0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  header .counter { color:var(--muted); font-size:14px; font-variant-numeric:tabular-nums; }
  .progress { height:4px; border-radius:3px; background:#ddd2f0; margin-top:8px; overflow:hidden; }
  .progress > div { height:100%; width:0; border-radius:3px;
    background:linear-gradient(90deg,var(--accent1),var(--accent2)); transition:width .25s; }
  main { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; }
  .scene { width:100%; max-width:520px; height:min(56vh,420px); perspective:1400px; cursor:pointer; }
  .flip { position:relative; width:100%; height:100%; transition:transform .5s cubic-bezier(.2,.8,.3,1);
    transform-style:preserve-3d; }
  .flip.flipped { transform:rotateY(180deg); }
  .face { position:absolute; inset:0; backface-visibility:hidden; -webkit-backface-visibility:hidden;
    border-radius:22px; padding:26px; display:flex; flex-direction:column; gap:14px;
    align-items:center; justify-content:center; text-align:center;
    border:1px solid #ddd2f0; box-shadow:0 18px 50px rgba(0,0,0,.45); }
  .face.front { background:linear-gradient(160deg,#ffffff,#e3dbf5); }
  .face.back { background:linear-gradient(160deg,#ffffff,#efe9fb); transform:rotateY(180deg); }
  .label { font-size:12px; letter-spacing:.14em; color:var(--muted); text-transform:uppercase; }
  .face.back .label { color:#8b6fd4; }
  .text { font-size:21px; line-height:1.4; white-space:pre-wrap; overflow-y:auto; max-height:100%; }
  .hint { color:var(--muted); font-size:13px; }
  footer { display:flex; align-items:center; justify-content:center; gap:12px; }
  .nav { width:58px; height:46px; border:none; border-radius:14px; font-size:18px; color:#fff; cursor:pointer;
    background:linear-gradient(135deg,var(--accent1),var(--accent2)); box-shadow:0 4px 14px rgba(139,111,212,.35); }
  .nav:disabled { opacity:.35; box-shadow:none; }
  .nav:active { transform:scale(.94); }
  .ghost { border:1px solid #cbbce8; background:#ffffff; color:var(--text); border-radius:14px;
    height:46px; padding:0 16px; font-size:14px; cursor:pointer; }
  .ghost:active { background:#e8def8; }
  .done { color:var(--muted); font-size:14px; text-align:center; }
</style></head>
<body>
<div id="wrap">
  <header><div class="title">{{ title }}</div><div class="counter" id="counter"></div></header>
  <div class="progress"><div id="bar"></div></div>
  <main>
    <div class="scene" id="scene">
      <div class="flip" id="flip">
        <div class="face front"><div class="label">{{ front_label }}</div><div class="text" id="q"></div></div>
        <div class="face back"><div class="label">{{ back_label }}</div><div class="text" id="a"></div></div>
      </div>
    </div>
    <div class="hint" id="hint">{{ hint }}</div>
  </main>
  <footer>
    <button class="ghost" id="miss" disabled style="background:linear-gradient(135deg,#ff8a8a,#e5484d);color:#fff;border:none;min-width:104px">&#128078; Missed</button>
    {% if page_id %}<button class="ghost" id="regen">&#128260; {{ regen_label }}</button>{% endif %}
    <button class="ghost" id="got" disabled style="background:linear-gradient(135deg,#43cf75,#2fb85a);color:#fff;border:none;min-width:104px">&#128077; Got it</button>
  </footer>
</div>
<script type="application/json" id="deck">{{ cards_json }}</script>
<script>
const CARDS = JSON.parse(document.getElementById('deck').textContent);
const PAGE_ID = "{{ page_id }}";
const flip = document.getElementById('flip'), scene = document.getElementById('scene');
const got = document.getElementById('got'), miss = document.getElementById('miss');
const hintEl = document.getElementById('hint');
let order = CARDS.map((_,k)=>k), pos = 0, flipped = false;
function setRate(on){ got.disabled = !on; miss.disabled = !on; got.style.opacity = miss.style.opacity = on?1:.4; }
function render(){
  if (pos >= order.length) return done();
  const c = CARDS[order[pos]] || {q:'', a:''};
  document.getElementById('q').textContent = c.q || '';
  document.getElementById('a').textContent = c.a || '';
  flip.classList.remove('flipped'); flipped = false; setRate(false);
  document.getElementById('counter').textContent = (pos+1)+' / '+order.length;
  document.getElementById('bar').style.width = (pos/order.length*100)+'%';
  hintEl.textContent = 'tap the card, then rate yourself';
}
function doFlip(){ flip.classList.toggle('flipped'); flipped = flip.classList.contains('flipped'); if (flipped) setRate(true); }
scene.addEventListener('click', doFlip);
async function rate(knew){
  if (!flipped) return;
  const card = order[pos];
  if (PAGE_ID){ try { fetch('/study/'+PAGE_ID+'/progress', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({card, knew})}); } catch(_){} }
  if (!knew) order.push(card);          // missed -> resurfaces again this session
  pos++; render();
}
got.addEventListener('click', e=>{ e.stopPropagation(); rate(true); });
miss.addEventListener('click', e=>{ e.stopPropagation(); rate(false); });
document.addEventListener('keydown', e=>{
  if (e.key===' '||e.key==='Enter'){ e.preventDefault(); doFlip(); }
  else if (flipped && e.key==='ArrowRight') rate(true);
  else if (flipped && e.key==='ArrowLeft') rate(false);
});
function done(){
  document.getElementById('counter').textContent='';
  document.getElementById('q').textContent='🎉'; document.getElementById('a').textContent='🎉';
  hintEl.textContent = 'deck complete - reviewed '+order.length+' cards. tap regenerate for a fresh set.';
  setRate(false);
}
const regen = document.getElementById('regen');
if (regen) regen.addEventListener('click', async ()=>{ const t=regen.textContent;
  regen.disabled=true; regen.textContent='generating…';
  try { const r=await fetch('/study/{{ page_id }}/regenerate',{method:'POST'});
    if(r.ok) location.reload(); else { regen.disabled=false; regen.textContent=t; } }
  catch(_){ regen.disabled=false; regen.textContent=t; } });
(async function init(){
  if (PAGE_ID){ try { const r=await fetch('/study/'+PAGE_ID+'/progress');
    const boxes=(await r.json()).boxes||{}; order.sort((a,b)=>(boxes[a]||0)-(boxes[b]||0)); } catch(_){} }
  render();
})();
</script>
</body></html>""",
)


def _render_deck(title: str, cards: list[dict], front_label: str, back_label: str,
                 hint: str, page_id: str = "") -> str:
    # Embed the cards as JSON for the deck's JS. Escape "<" so the data can never
    # break out of the <script> block; the values are course-derived, but be safe.
    cards_json = Markup(json.dumps(cards, ensure_ascii=False).replace("<", "\\u003c"))
    return _DECK_TEMPLATE.render(
        title=title, cards_json=cards_json, page_id=page_id, regen_label="new cards",
        front_label=front_label, back_label=back_label, hint=hint,
    )


def render_flashcards(title: str, cards: list[dict], page_id: str = "") -> str:
    return _render_deck(title, cards, "Question", "Answer", "tap the card to flip", page_id)


def render_exam(title: str, cards: list[dict], page_id: str = "") -> str:
    return _render_deck(title, cards, "Question", "Answer",
                        "think it through, then tap to reveal", page_id)


# --- Interactive quizzes (graded multiple-choice / type-your-answer) ---------

_QUIZ_STYLE = """
  :root { --bg:#efe9fb; --accent1:#8b6fd4; --accent2:#a98ee0; --text:#3a2f55; --muted:#8a7fa5;
    --good:#31d158; --bad:#ff5d5d; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; color:var(--text);
    background:radial-gradient(125% 75% at 50% -8%, #f3eefc 0%, #efe9fb 52%) fixed, var(--bg);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  #wrap { display:flex; flex-direction:column; height:100dvh;
    padding:max(env(safe-area-inset-top),12px) 14px calc(env(safe-area-inset-bottom) + 12px); }
  header { display:flex; align-items:baseline; gap:10px; }
  header .title { font-weight:600; font-size:16px; flex:1; min-width:0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  header .counter { color:var(--muted); font-size:14px; font-variant-numeric:tabular-nums; }
  .progress { height:4px; border-radius:3px; background:#ddd2f0; margin-top:8px; overflow:hidden; }
  .progress > div { height:100%; width:0; border-radius:3px;
    background:linear-gradient(90deg,var(--accent1),var(--accent2)); transition:width .25s; }
  main { flex:1; display:flex; flex-direction:column; gap:14px; padding-top:14px; overflow-y:auto; }
  .question { font-size:20px; line-height:1.4; }
  .choices { display:flex; flex-direction:column; gap:10px; }
  .choice { text-align:left; padding:14px 16px; border-radius:14px; border:1px solid #cbbce8;
    background:#ffffff; color:var(--text); font-size:16px; cursor:pointer; transition:transform .08s; }
  .choice:active { transform:scale(.99); }
  .choice:disabled { cursor:default; }
  .choice.correct { background:rgba(49,209,88,.18); border-color:var(--good); }
  .choice.wrong { background:rgba(255,93,93,.16); border-color:var(--bad); }
  .explain { color:var(--muted); font-size:15px; line-height:1.45; display:none; }
  .explain.show { display:block; }
  textarea { width:100%; min-height:120px; resize:vertical; border-radius:14px; border:1px solid #cbbce8;
    background:#ffffff; color:var(--text); padding:12px 14px; font-size:16px; font-family:inherit; }
  .model { display:none; background:#ffffff; border:1px solid #cbbce8; border-radius:14px; padding:12px 14px;
    font-size:16px; line-height:1.45; }
  .model.show { display:block; }
  .model .lbl { color:#8b6fd4; font-size:12px; letter-spacing:.12em; text-transform:uppercase; display:block;
    margin-bottom:6px; }
  .grade { display:none; gap:10px; }
  .grade.show { display:flex; }
  footer { display:flex; align-items:center; justify-content:center; gap:12px; padding-top:12px; }
  button.btn { border:none; border-radius:14px; height:48px; padding:0 20px; font-size:16px; color:#fff;
    cursor:pointer; background:linear-gradient(135deg,var(--accent1),var(--accent2));
    box-shadow:0 4px 14px rgba(139,111,212,.35); }
  button.btn:disabled { opacity:.35; box-shadow:none; }
  button.btn:active { transform:scale(.97); }
  button.good { background:linear-gradient(135deg,#2fb85a,#31d158); }
  button.bad { background:linear-gradient(135deg,#ff7676,#ff5d5d); }
  .ghost { border:1px solid #cbbce8; background:#ffffff; color:var(--text); border-radius:14px;
    height:46px; padding:0 18px; font-size:15px; cursor:pointer; }
  .done { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; }
  .bigscore { font-size:48px; font-weight:700;
    background:linear-gradient(135deg,var(--accent1),var(--accent2)); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .sub { color:var(--muted); font-size:18px; }
"""

_QUIZ_MC_TEMPLATE = Template(
    autoescape=True,
    source="""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark"><title>{{ title }}</title>
<style>""" + _QUIZ_STYLE + """</style></head>
<body>
<div id="wrap">
  <header><div class="title">{{ title }}</div><div class="counter" id="counter"></div></header>
  <div class="progress"><div id="bar"></div></div>
  <main id="main"></main>
  <footer>
    {% if page_id %}<button class="ghost" id="regen">&#128260; new questions</button>{% endif %}
    <button class="btn" id="next" disabled>Next &#9654;</button>
  </footer>
</div>
<script type="application/json" id="quiz">{{ questions_json }}</script>
<script>
let Q = JSON.parse(document.getElementById('quiz').textContent);
const ALL = Q.slice(), PAGE_ID = "{{ page_id }}";
let i = 0, score = 0, answered = false, missedQ = [];
const main = document.getElementById('main'), nextBtn = document.getElementById('next');
const counter = document.getElementById('counter'), bar = document.getElementById('bar');
function esc(s){ const d=document.createElement('div'); d.textContent = s==null?'':s; return d.innerHTML; }
function render(){
  answered = false; nextBtn.disabled = true;
  if (i >= Q.length) return done();
  const q = Q[i];
  counter.textContent = (i+1)+' / '+Q.length;
  bar.style.width = (i/Q.length*100)+'%';
  let h = '<div class="question">'+esc(q.q)+'</div><div class="choices">';
  (q.choices||[]).forEach((c,idx)=>{ h += '<button class="choice" data-idx="'+idx+'">'+esc(c)+'</button>'; });
  h += '</div><div class="explain" id="explain"></div>';
  main.innerHTML = h;
  main.querySelectorAll('.choice').forEach(b=> b.addEventListener('click', ()=> pick(parseInt(b.dataset.idx), q)));
  nextBtn.textContent = (i === Q.length-1) ? 'Finish' : 'Next ▶';
}
function pick(idx, q){
  if (answered) return; answered = true;
  const correct = q.answer_index;
  main.querySelectorAll('.choice').forEach((b,k)=>{ b.disabled = true;
    if (k === correct) b.classList.add('correct');
    else if (k === idx) b.classList.add('wrong'); });
  if (idx === correct) score++; else missedQ.push(q);
  const ex = document.getElementById('explain');
  ex.textContent = (idx === correct ? '✓ correct. ' : '✗ not quite. ') + (q.explanation || '');
  ex.classList.add('show');
  bar.style.width = ((i+1)/Q.length*100)+'%';
  nextBtn.disabled = false;
}
async function done(){
  counter.textContent = '';
  const total = Q.length, miss = missedQ.slice();
  if (PAGE_ID){ try { fetch('/study/'+PAGE_ID+'/attempt', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({score, total, missed: miss.length})}); } catch(_){} }
  let best = '';
  if (PAGE_ID){ try { const r = await fetch('/study/'+PAGE_ID+'/attempts'); const a = await r.json();
    if (a.count > 1){ best = '<div class="sub">best so far: '+a.best+' · '+a.count+' attempts</div>'; } } catch(_){} }
  const pct = total ? Math.round(score/total*100) : 0;
  let html = '<div class="done"><div class="bigscore">'+score+' / '+total+'</div>'
    + '<div class="sub">you scored '+pct+'%</div>' + best;
  if (miss.length) html += '<button class="ghost" id="review">🎯 review '+miss.length+' missed</button>';
  html += '<button class="ghost" id="again">↺ try again</button></div>';
  main.innerHTML = html; nextBtn.disabled = true;
  const rv = document.getElementById('review');
  if (rv) rv.addEventListener('click', ()=>{ Q = miss; i=0; score=0; missedQ=[]; render(); });
  document.getElementById('again').addEventListener('click', ()=>{ Q = ALL.slice(); i=0; score=0; missedQ=[]; render(); });
}
nextBtn.addEventListener('click', ()=>{ if (!answered && i < Q.length) return; i++; render(); });
const regen = document.getElementById('regen');
if (regen) regen.addEventListener('click', async ()=>{ const t=regen.textContent;
  regen.disabled=true; regen.textContent='generating…';
  try { const r=await fetch('/study/{{ page_id }}/regenerate',{method:'POST'});
    if(r.ok) location.reload(); else { regen.disabled=false; regen.textContent=t; } }
  catch(_){ regen.disabled=false; regen.textContent=t; } });
render();
</script></body></html>""",
)

_QUIZ_WRITTEN_TEMPLATE = Template(
    autoescape=True,
    source="""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark"><title>{{ title }}</title>
<style>""" + _QUIZ_STYLE + """</style></head>
<body>
<div id="wrap">
  <header><div class="title">{{ title }}</div><div class="counter" id="counter"></div></header>
  <div class="progress"><div id="bar"></div></div>
  <main id="main"></main>
  <footer>
    {% if page_id %}<button class="ghost" id="regen">&#128260; new questions</button>{% endif %}
    <button class="btn" id="submit">Check answer</button>
    <button class="btn" id="next" disabled>Next &#9654;</button>
  </footer>
</div>
<script type="application/json" id="quiz">{{ questions_json }}</script>
<script>
let Q = JSON.parse(document.getElementById('quiz').textContent);
const ALL = Q.slice(), PAGE_ID = "{{ page_id }}";
let i = 0, score = 0, revealed = false, missedQ = [];
const main = document.getElementById('main'), nextBtn = document.getElementById('next');
const submitBtn = document.getElementById('submit');
const counter = document.getElementById('counter'), bar = document.getElementById('bar');
function esc(s){ const d=document.createElement('div'); d.textContent = s==null?'':s; return d.innerHTML; }
function render(){
  revealed = false; nextBtn.disabled = true; submitBtn.style.display = '';
  if (i >= Q.length) return done();
  const q = Q[i];
  counter.textContent = (i+1)+' / '+Q.length;
  bar.style.width = (i/Q.length*100)+'%';
  main.innerHTML = '<div class="question">'+esc(q.q)+'</div>'
    + '<textarea id="ans" placeholder="type your answer..."></textarea>'
    + '<div class="model" id="model"><span class="lbl">model answer</span>'+esc(q.answer||'')+'</div>'
    + '<div class="grade" id="grade"><button class="btn good" id="got">✓ I got it</button>'
    + '<button class="btn bad" id="missed">✗ I missed it</button></div>';
  document.getElementById('ans').focus();
  nextBtn.textContent = (i === Q.length-1) ? 'Finish' : 'Next ▶';
}
function reveal(){
  if (revealed) return; revealed = true;
  document.getElementById('ans').setAttribute('readonly','');
  document.getElementById('model').classList.add('show');
  document.getElementById('grade').classList.add('show');
  submitBtn.style.display = 'none';
  document.getElementById('got').addEventListener('click', ()=>{ score++; nextBtn.disabled=false; lockGrade(); });
  document.getElementById('missed').addEventListener('click', ()=>{ missedQ.push(Q[i]); nextBtn.disabled=false; lockGrade(); });
}
function lockGrade(){ document.querySelectorAll('#grade .btn').forEach(b=> b.disabled=true); }
async function done(){
  counter.textContent = ''; submitBtn.style.display='none';
  const total = Q.length, miss = missedQ.slice();
  if (PAGE_ID){ try { fetch('/study/'+PAGE_ID+'/attempt', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({score, total, missed: miss.length})}); } catch(_){} }
  let best = '';
  if (PAGE_ID){ try { const r = await fetch('/study/'+PAGE_ID+'/attempts'); const a = await r.json();
    if (a.count > 1){ best = '<div class="sub">best so far: '+a.best+' · '+a.count+' attempts</div>'; } } catch(_){} }
  const pct = total ? Math.round(score/total*100) : 0;
  let html = '<div class="done"><div class="bigscore">'+score+' / '+total+'</div>'
    + '<div class="sub">you got '+pct+'% right</div>' + best;
  if (miss.length) html += '<button class="ghost" id="review">🎯 review '+miss.length+' missed</button>';
  html += '<button class="ghost" id="again">↺ try again</button></div>';
  main.innerHTML = html; nextBtn.disabled = true;
  const rv = document.getElementById('review');
  if (rv) rv.addEventListener('click', ()=>{ Q = miss; i=0; score=0; missedQ=[]; render(); });
  document.getElementById('again').addEventListener('click', ()=>{ Q = ALL.slice(); i=0; score=0; missedQ=[]; render(); });
}
submitBtn.addEventListener('click', reveal);
nextBtn.addEventListener('click', ()=>{ if (!revealed && i < Q.length) return; i++; render(); });
const regen = document.getElementById('regen');
if (regen) regen.addEventListener('click', async ()=>{ const t=regen.textContent;
  regen.disabled=true; regen.textContent='generating…';
  try { const r=await fetch('/study/{{ page_id }}/regenerate',{method:'POST'});
    if(r.ok) location.reload(); else { regen.disabled=false; regen.textContent=t; } }
  catch(_){ regen.disabled=false; regen.textContent=t; } });
render();
</script></body></html>""",
)


def _render_quiz(template: Template, title: str, questions: list[dict], page_id: str = "") -> str:
    questions_json = Markup(
        json.dumps(questions, ensure_ascii=False).replace("<", "\\u003c")
    )
    return template.render(title=title, questions_json=questions_json, page_id=page_id)


def render_quiz_mc(title: str, questions: list[dict], page_id: str = "") -> str:
    """A graded multiple-choice quiz: pick an option, see if it's right, get a score."""
    return _render_quiz(_QUIZ_MC_TEMPLATE, title, questions, page_id)


def render_quiz_written(title: str, questions: list[dict], page_id: str = "") -> str:
    """A type-your-answer quiz: write an answer, reveal the model answer, self-grade."""
    return _render_quiz(_QUIZ_WRITTEN_TEMPLATE, title, questions, page_id)


# --- Tool schemas ------------------------------------------------------------

STUDY_TOOLS = [
    {
        "name": "make_flashcards",
        "description": "Make a flashcard deck and return a link to an interactive deck. "
        "Build from a COURSE (pass 'course', e.g. 'STAT 311') OR from a SAVED LECTURE "
        "(pass 'lecture_id' from list_lectures — use this when they say 'make flashcards "
        "from that lecture'). Provide course OR lecture_id, not both. Optionally 'topic' "
        "to focus and 'count'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Course code or name, e.g. 'STAT 311'."},
                "lecture_id": {"type": "string", "description": "Build from a saved lecture's transcript instead of a course. Pass the id from list_lectures; when set, ignore 'course'."},
                "topic": {"type": "string", "description": "Optional: focus the deck on one topic/unit."},
                "count": {"type": "integer", "description": "Roughly how many cards (default 15)."},
            },
        },
    },
    {
        "name": "make_practice_exam",
        "description": "Make a short interactive quiz and return a link to it. Build from a "
        "COURSE (pass 'course') OR from a SAVED LECTURE (pass 'lecture_id' from list_lectures "
        "— use this when they say 'quiz me on that lecture'). Provide course OR lecture_id, "
        "not both. Optionally 'topic' and 'count'. Use 'format': 'multiple_choice' (default) "
        "builds a graded pick-the-right-option quiz with a score; 'written' builds a quiz the "
        "student types answers into, then reveals the model answer to self-check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Course code or name, e.g. 'STAT 311'."},
                "lecture_id": {"type": "string", "description": "Build from a saved lecture's transcript instead of a course. Pass the id from list_lectures; when set, ignore 'course'."},
                "topic": {"type": "string", "description": "Optional: focus on one topic/unit."},
                "count": {"type": "integer", "description": "Roughly how many questions (default 10)."},
                "format": {
                    "type": "string",
                    "enum": ["multiple_choice", "written"],
                    "description": "'multiple_choice' (graded options) or 'written' (type your answer).",
                },
            },
        },
    },
]

_FLASHCARD_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cards"],
    "additionalProperties": False,
}

_EXAM_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

# Type-your-answer quiz: a question and a concise model answer to self-check against.
_WRITTEN_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["q", "answer"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

# Multiple-choice quiz: a question, several options, the correct index, a why.
_MC_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer_index": {"type": "integer"},
                    "explanation": {"type": "string"},
                },
                "required": ["q", "choices", "answer_index", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


class StudyService:
    def __init__(self, canvas, client, model: str, pages, public_base_url: str, lectures=None):
        self.canvas = canvas
        self.client = client
        self.model = model
        self.pages = pages
        self.public_base_url = public_base_url.rstrip("/")
        self.lectures = lectures

    def tool_names(self) -> list[str]:
        return [t["name"] for t in STUDY_TOOLS]

    def schemas(self) -> list[dict]:
        return list(STUDY_TOOLS)

    def dispatch(self, name: str, tool_input: dict) -> str:
        if name == "make_flashcards":
            return self._make(tool_input, kind="flashcards")
        if name == "make_practice_exam":
            return self._make(tool_input, kind="exam")
        return f"(unknown study tool: {name})"

    # --- internals -----------------------------------------------------------

    def _course_arg(self, tool_input: dict) -> str:
        course = (tool_input.get("course") or "").strip()
        if not course and tool_input.get("ref"):
            # Legacy 'courseId:assignmentId' ref → fall back to the course id.
            course = str(tool_input["ref"]).split(":")[0].strip()
        return course

    def _instruction(self, kind: str, count: int, label: str, topic: str, is_lecture: bool = False) -> str:
        focus = (
            f"the topic \"{topic}\" in {label}" if topic
            else (f"the lecture \"{label}\"" if is_lecture
                  else f"{label} (whole-course review for the final exam)")
        )
        material_line = (
            "Below is the transcript of the lecture. "
            if is_lecture else
            "Below is the course material gathered from Canvas: the syllabus, a topic "
            "outline (the course modules), and the assignment list. "
        )
        if kind == "flashcards":
            what = f"about {count} study flashcards (focused question / concise answer pairs)"
        elif kind == "mc":
            what = (
                f"a {count}-question multiple-choice quiz. Give each question exactly 4 options "
                "in 'choices', set 'answer_index' (0-based) to the single correct option, and a "
                "one-line 'explanation' of why it's right. Make the wrong options plausible, not silly"
            )
        else:  # written
            what = (
                f"a {count}-question short-answer quiz, with a concise model 'answer' for each "
                "question that a student can check their typed answer against"
            )
        return (
            f"Make {what} to help a student prepare for {focus}. "
            f"{material_line}Cover the most "
            "important concepts a student at this level should know. If the material "
            "names topics but is thin on detail, use your own accurate knowledge of the "
            "subject to write correct, useful items on exactly those topics. Do NOT "
            "invent course-specific facts (exam dates, grading policies, instructor "
            "names, page numbers) that are not in the material. Keep answers correct "
            "and concise."
        )

    def _generate(self, instruction: str, source: str, schema: dict) -> dict:
        if not source.strip():
            source = "(No course material was available from Canvas.)"
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": f"{instruction}\n\nCourse material:\n{source}",
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            "{}",
        )
        return json.loads(text)

    def _build(self, kind, course, topic, count, fmt, page_id, lecture_id=""):
        """Generate + render a study page. Returns (title, html, note)."""
        is_lec = bool(lecture_id)
        if is_lec:
            hit = self.lectures.get(lecture_id) if self.lectures is not None else None
            if not hit:
                raise ValueError(f"lecture {lecture_id} not found")
            label, source = hit[0], hit[1][:40000]
        else:
            label, source = self.canvas.get_study_material(course)
        suffix = f" - {topic}" if topic else ""

        if kind == "flashcards":
            data = self._generate(
                self._instruction("flashcards", count, label, topic, is_lec), source, _FLASHCARD_SCHEMA
            )
            cards = data.get("cards", [])
            title = f"{label}{suffix} - flashcards"
            return title, render_flashcards(title, cards, page_id), "made you flashcards"

        if fmt in ("written", "typed", "short_answer", "free_response"):
            data = self._generate(
                self._instruction("written", count, label, topic, is_lec), source, _WRITTEN_SCHEMA
            )
            questions = data.get("questions", [])
            title = f"{label}{suffix} - Quiz"
            return title, render_quiz_written(title, questions, page_id), "here you go"

        data = self._generate(
            self._instruction("mc", count, label, topic, is_lec), source, _MC_SCHEMA
        )
        questions = data.get("questions", [])
        title = f"{label}{suffix} - Quiz"
        return title, render_quiz_mc(title, questions, page_id), "here you go"

    def _make(self, tool_input: dict, kind: str) -> str:
        lecture_id = (tool_input.get("lecture_id") or "").strip()
        course = self._course_arg(tool_input)
        topic = (tool_input.get("topic") or "").strip()
        count = int(tool_input.get("count") or (15 if kind == "flashcards" else 10))
        fmt = (tool_input.get("format") or "multiple_choice").strip().lower()

        page_id = uuid.uuid4().hex
        try:
            title, html, note = self._build(kind, course, topic, count, fmt, page_id, lecture_id)
        except ValueError:
            return "couldn't find that lecture - run list_lectures first to get its id."
        # Remember the recipe so the page's "new questions" button can rebuild it.
        meta = json.dumps(
            {"kind": kind, "course": course, "topic": topic, "count": count,
             "format": fmt, "lecture_id": lecture_id}
        )
        self.pages.save(page_id, title, html, meta)
        link = f"{self.public_base_url}/study/{page_id}"
        return f"{note}: {link}"

    def regenerate(self, page_id: str) -> bool:
        """Rebuild a study page in place from its stored recipe (a fresh set). """
        raw = self.pages.get_meta(page_id)
        if not raw:
            return False
        try:
            m = json.loads(raw)
        except (ValueError, TypeError):
            return False
        title, html, _ = self._build(
            m.get("kind", "exam"), m.get("course", ""), m.get("topic", ""),
            int(m.get("count") or 10), m.get("format", "") or "multiple_choice", page_id,
            m.get("lecture_id", ""),
        )
        self.pages.save(page_id, title, html, raw)
        return True
