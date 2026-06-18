# Requesting a UW Canvas Developer Key (for "Sign in with Canvas")

OAuth2 against UW Canvas requires a **Developer Key** issued by UW's Canvas admins. You can't
self-serve this — you have to request it. This is the long pole (days–weeks, and they may ask
questions about data handling / FERPA), so send it first.

## Who to contact
UW-IT / the Canvas (LMS) team. Start with **help@uw.edu** (UW-IT Service Center) or the
Canvas/Academic Technologies group, and ask to be routed to whoever administers **Canvas
Developer Keys / API integrations**.

## What to ask for
> "I'm a UW student building **Dubly**, a read-only study assistant that helps students see their
> own Canvas assignments, grades, and announcements and generate study materials. I'd like to
> request an **OAuth2 Developer Key** so students can sign in with their own Canvas account
> (instead of pasting an access token). The app only **reads** Canvas data on the student's
> behalf and never writes. Could you issue a developer key, or tell me the process/requirements?"

## Technical details they'll need
- **Key type:** API / OAuth2 (not LTI)
- **Owner email:** vladbani@microsoft.com
- **Redirect URI (must match exactly):**
  `https://canvas-study-assistant-xohy.onrender.com/auth/canvas/callback`
  *(If you move to a custom domain later, the key's redirect URI must be updated to match.)*
- **Canvas base:** `https://canvas.uw.edu`
- **Access:** read-only. If they "enforce scopes," request these GET scopes:
  - `url:GET|/api/v1/users/self`
  - `url:GET|/api/v1/courses`
  - `url:GET|/api/v1/users/self/enrollments`
  - `url:GET|/api/v1/planner/items`
  - `url:GET|/api/v1/courses/:course_id/assignments`
  - `url:GET|/api/v1/courses/:course_id/assignment_groups`
  - `url:GET|/api/v1/announcements`
  - `url:GET|/api/v1/conversations`
  - `url:GET|/api/v1/calendar_events`
  - `url:GET|/api/v1/courses/:course_id` (syllabus, modules, files)

## What they give back (you paste these into Render env vars)
- **Client ID** → `CANVAS_CLIENT_ID`
- **Client Secret** → `CANVAS_CLIENT_SECRET`

Once those two are set, the OAuth flow I'm building goes live.

## Likely follow-up questions from UW (be ready)
- **Where is data stored / who can see it?** Each student's Canvas token is encrypted at rest;
  data is only used to answer that student's own questions; nothing is sold or shared.
- **FERPA:** the app shows a student *their own* records only, never anyone else's.
- **Privacy policy URL:** they'll likely want one (also required by Google Play). I'll draft it.

## If UW declines or it stalls
We ship the **paste-your-own-token** version (no UW approval needed) and switch to OAuth later —
the backend I'm building supports both behind the same login screen.
