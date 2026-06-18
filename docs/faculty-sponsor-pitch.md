# Finding a UW faculty / department sponsor for Dubly

UW won't issue a Canvas developer key to a student, but **any UW employee, school, college, or
department can request one** (after completing UW's **Internal Data Sharing MOU** describing the
integration). So the goal is: find a faculty member or department willing to be the **named owner**
of the integration. You've done all the engineering — their role is to request the key via
**help@uw.edu** and sign the MOU.

## Who to approach (best → still good)
1. **An Informatics / iSchool faculty member or academic advisor.** Dubly *is* an informatics
   project (learning data, study analytics) — a natural fit, and UW-IT even named "learning
   analytics" as a relevant area. Start here.
2. **A professor whose class you're in** (e.g. your CSE 163 / INFO instructor) who'd find it cool
   and is comfortable with student data tools.
3. **An academic advising / student-success office** in your major — they want retention tools.
4. **The UW Center for Teaching & Learning** or UW-IT Academic Technologies.
5. **A faculty advisor of a relevant student org** (CS club, etc.).

Lead with someone who already knows you and teaches/works in a data or learning area.

## The ask (keep it small for them)
> "I've built and hosted everything; I just need a UW employee/department to be the named owner
> so UW-IT can issue the developer key. Your part is requesting the key and completing the
> Internal Data Sharing MOU describing the integration — I'll prepare all the technical details
> and answers for that form."

## Outreach email (paste & edit)
Subject: **Faculty sponsor for a student-built study tool (read-only Canvas integration)?**

> Hi Professor [Name],
>
> I'm Yuta, an Informatics student. I built **Dubly**, a study assistant that lets a student see
> *their own* Canvas assignments and grades and turn them into quizzes, flashcards (with spaced
> repetition), study plans, grade projections, and reminders. It's fully built and running.
>
> I'd love to open it to other UW students, but UW-IT only issues the required Canvas developer key
> to **employees or departments**, not students. I'm looking for a faculty/department **sponsor**
> to be the named owner of the integration so UW-IT can issue the key. I've handled all the
> engineering and security design — the sponsor's role is to request the key and complete UW's
> Internal Data Sharing MOU; I'll prepare everything for it.
>
> It's strictly **read-only**, each student only ever sees **their own** data, Canvas tokens are
> encrypted, and nothing is sold or shared.
>
> Could I give you a 5-minute demo and talk about whether you'd be open to sponsoring it?
>
> Thanks,
> Yuta · vladbani@microsoft.com

## One-pager to attach / show
- **What it is:** Dubly, a read-only UW Canvas study assistant (web app / installable PWA).
- **What it does:** answers a student's own "what's due / my grades / syllabus" questions; makes
  multiple-choice & typed quizzes, spaced-repetition flashcards, study plans, essay blueprints, and
  Word docs; "what do I need on the final" grade math; proactive due/grade reminders.
- **Who benefits:** UW students; departments get a retention/study tool with their name on it.
- **Built by:** a UW student; ~195 automated tests; running on Render.

## Data-security answers (for the MOU / UW-IT consultation)
- **Read-only:** the app never writes to Canvas (no grade changes, no posts).
- **Scope:** each authenticated student accesses **only their own** Canvas data — never a peer's.
- **Storage:** Canvas tokens encrypted at rest; chat/study data kept per-user, not shared; nothing
  sold or used for advertising.
- **Transport:** HTTPS only.
- **AI:** questions/uploaded files are sent to Anthropic (Claude) to generate answers/study
  materials; covered in the privacy policy.
- **Deletion:** a student can clear their data; tokens revocable in Canvas at any time.

## What happens once a sponsor says yes
1. Sponsor emails **help@uw.edu** requesting an OAuth2 developer key + completes the Internal Data
   Sharing MOU (I provide the technical details from `uw-canvas-oauth-request.md`).
2. UW-IT issues **Client ID + Client Secret**.
3. I build the OAuth + multi-tenant backend; you paste the credentials into Render.
4. Privacy policy + Play Data-Safety, then publish.
