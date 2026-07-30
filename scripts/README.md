# Verification scripts

An automated, recorded proof that the outreach system works end to end.

## Run it

```bash
python scripts/verify_system.py            # headless, records a video
python scripts/verify_system.py --headed   # watch the browser as it goes
python scripts/verify_system.py --speed 2  # slower captions, easier to read
```

Install the tooling first (it is kept out of `requirements.txt` so none of it
lands on the production server):

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
```

The Flask app itself runs from the project `venv`, which the script launches
for you.

The recording is transcoded to MP4 at the end using `ffmpeg` if it is on PATH,
otherwise the copy bundled with `imageio-ffmpeg`. If neither is present the run
still succeeds and keeps the `.webm`.

Everything lands in `scripts/output/`:

| File | What it is |
|---|---|
| `webreach-verification.mp4` | The narrated recording — H.264, plays anywhere |
| `webreach-verification.webm` | The same recording as Playwright wrote it |
| `verification-report.json` | Every check with pass/fail and detail |
| `0*.png` | Screenshots at the key moments |
| `verify.db` | The throwaway database the run used |

The command exits non-zero if any check fails, so it can be wired into CI.

## What it checks

1. **Every page loads** — Dashboard, Leads, Conversations, Scraping Jobs,
   AI Chat Test, AI Settings, Admin, Settings.
2. **The admin password gate** stops an unauthenticated write.
3. **Templates are what gets sent.** A template is edited through the UI with a
   unique marker in it, then the AI Chat Test is checked for that exact text,
   character for character. This is the check that covers the report that
   *"nothing in the templates matches what is happening in the AI chat test"*.
4. **Objections** — "who is this?", "how much does it cost?", "where did you get
   my number?" — each resolve to their own template.
5. **Inbound messages get a reply.** The four real replies from the GHL
   screenshots that previously went unanswered are delivered to the live
   webhook and asserted to produce an outbound SMS.
6. **Deleted GHL contacts recover.** A webhook carrying a brand-new contact ID
   still finds the lead, by phone and by GHL contact lookup.
7. **Unknown numbers** are answered rather than dropped.
8. **STOP** opts the lead out.
9. **Manual takeover** silences the AI while still logging the message.
10. **No unreplaced `{placeholder}`** and no message that introduces us using
    the lead's own business name.

## No real business is ever texted

The app under test is the real application — real routes, real reply engine,
real database writes. Only the outbound SMS gateway is redirected: `stub_ghl.py`
stands in for Go High Level on `127.0.0.1:5099`, and `GHL_API_BASE_URL` points
the app at it. Messages the app "sends" are captured there and asserted, which
is how the run proves a reply genuinely left the application.

The run also uses its own throwaway SQLite database, so the live lead list is
untouched.

## Running against the live site

```bash
python scripts/verify_system.py --base-url https://outreach.sms2cartdemo.com
```

This performs the read-only checks only — it will not edit settings or send
anything. Use the local run for the full suite.

## Files

| File | Purpose |
|---|---|
| `verify_system.py` | The suite and the video narration |
| `_app_harness.py` | Boots the real app on a throwaway DB, seeded with the leads from the screenshots |
| `stub_ghl.py` | Stand-in GHL API that captures outbound SMS |
| `.assetcache/` | Cached Bootstrap/Chart.js/font files so runs are fast and repeatable |
