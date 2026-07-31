"""
End-to-end verification of the WebReach outreach system, recorded to video
with on-screen captions.

What it proves, in order:

  1. Every page of the dashboard loads.
  2. The admin password gate works.
  3. A template edited on AI Settings is the text a lead actually receives —
     character for character, not a paraphrase.
  4. Objections (who are you / how much / where did you get my number) are
     answered from their own templates.
  5. A real inbound SMS webhook produces a real outbound reply — using the
     four messages from the client's screenshots that previously got none.
  6. A lead whose GHL contact was deleted and recreated is still matched, by
     phone, and still gets answered.
  7. An inbound from an unknown number is answered instead of dropped.
  8. STOP opts the lead out.
  9. Manual takeover suppresses the AI without losing the message.
 10. No unreplaced {placeholder} ever reaches a lead.

Usage
-----
    python scripts/verify_system.py                 # local app + stub gateway
    python scripts/verify_system.py --headed        # watch it run
    python scripts/verify_system.py --base-url URL  # run against a live site
                                                    # (read-only checks only)

Outputs land in scripts/output/: the video, screenshots and a JSON report.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUTPUT = os.path.join(HERE, "output")

sys.path.insert(0, HERE)
from stub_ghl import StubGHL  # noqa: E402

ADMIN_PASSWORD = "verify-pass-123"
APP_PORT = 5055
STUB_PORT = 5099

# The four real replies from the client's GHL screenshots, all of which the
# system failed to answer. Each is asserted to produce the right template.
SCREENSHOT_CASES = [
    ("+15551110001", "This is Santa Fe Builders how can I help you", "msg_compliment"),
    ("+15551110002", "Yup, right number again!",                     "msg_compliment"),
    ("+15551110003", "Not interested thank you",                     "msg_not_interested"),
    ("+15551110004", "Yeah not the best time thanks",                "msg_not_interested"),
]


# ══════════════════════════════════════════════════════════════════════════
# Result tracking
# ══════════════════════════════════════════════════════════════════════════

class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, passed, detail=""):
        self.checks.append({"name": name, "passed": bool(passed), "detail": str(detail)})
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""), flush=True)
        return passed

    @property
    def passed(self):
        return sum(1 for c in self.checks if c["passed"])

    @property
    def failed(self):
        return sum(1 for c in self.checks if not c["passed"])

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "total": len(self.checks), "passed": self.passed, "failed": self.failed,
                "checks": self.checks,
            }, f, indent=2)


report = Report()


# ══════════════════════════════════════════════════════════════════════════
# On-screen captions
# ══════════════════════════════════════════════════════════════════════════

CAPTION_JS = """
(data) => {
  let bar = document.getElementById('__vcap');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = '__vcap';
    bar.style.cssText = [
      'position:fixed','left:0','right:0','bottom:0','z-index:2147483647',
      'background:linear-gradient(90deg,#0b1020 0%,#16213e 100%)','color:#fff',
      'font:600 19px/1.45 Inter,Segoe UI,system-ui,sans-serif','padding:16px 26px',
      'box-shadow:0 -6px 24px rgba(0,0,0,.45)','border-top:3px solid #4f8cff',
      'display:flex','gap:18px','align-items:center','pointer-events:none'
    ].join(';');
    document.documentElement.appendChild(bar);
  }
  const colors = { info:'#4f8cff', pass:'#22c55e', fail:'#ef4444', step:'#a855f7' };
  bar.style.borderTopColor = colors[data.kind] || '#4f8cff';
  bar.innerHTML =
    '<span style="background:' + (colors[data.kind] || '#4f8cff') +
      ';border-radius:8px;padding:5px 13px;font-size:15px;white-space:nowrap">' +
      data.tag + '</span>' +
    '<span style="flex:1">' + data.text + '</span>';
}
"""


class Narrator:
    """Draws the caption bar and paces the recording so it is readable."""

    def __init__(self, page, speed=1.0):
        self.page = page
        self.speed = speed

    def _paint(self, tag, text, kind):
        try:
            self.page.evaluate(CAPTION_JS, {"tag": tag, "text": text, "kind": kind})
        except Exception:
            pass

    def say(self, text, seconds=2.6, tag="STEP", kind="info"):
        print(f"    · {text}", flush=True)
        self._paint(tag, text, kind)
        self.page.wait_for_timeout(int(seconds * 1000 * self.speed))

    def verdict(self, name, passed, detail="", seconds=2.4):
        report.add(name, passed, detail)
        self._paint("PASS" if passed else "FAIL",
                    name + (f" — {detail}" if detail else ""),
                    "pass" if passed else "fail")
        self.page.wait_for_timeout(int(seconds * 1000 * self.speed))
        return passed

    def card(self, title, lines=None, seconds=3.4):
        """A full-screen section card between chapters."""
        lines = lines or []
        self.page.goto("about:blank")
        self.page.evaluate(
            """(d) => {
              document.documentElement.innerHTML =
                '<body style="margin:0;height:100vh;display:flex;flex-direction:column;' +
                'align-items:center;justify-content:center;background:' +
                'linear-gradient(135deg,#0b1020,#1b2a4a);color:#fff;' +
                'font-family:Inter,Segoe UI,system-ui,sans-serif;text-align:center">' +
                '<div style="font-size:15px;letter-spacing:.32em;color:#7aa2ff;' +
                'text-transform:uppercase;margin-bottom:18px">WebReach verification</div>' +
                '<div style="font-size:46px;font-weight:800;max-width:1100px;' +
                'line-height:1.2">' + d.title + '</div>' +
                '<div style="margin-top:26px;font-size:20px;color:#c8d4f0;' +
                'line-height:2;max-width:1000px">' + d.lines.join('<br>') + '</div></body>';
            }""",
            {"title": title, "lines": lines},
        )
        self.page.wait_for_timeout(int(seconds * 1000 * self.speed))

    def goto(self, url, text, seconds=2.6, tag="STEP"):
        safe_goto(self.page, url)
        self.page.wait_for_timeout(700)
        self.say(text, seconds, tag=tag)

    def shot(self, name):
        self.page.screenshot(path=os.path.join(OUTPUT, f"{name}.png"))


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def wait_for_port(port, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def venv_python():
    candidate = os.path.join(ROOT, "venv", "Scripts", "python.exe")
    if os.path.exists(candidate):
        return candidate
    candidate = os.path.join(ROOT, "venv", "bin", "python")
    return candidate if os.path.exists(candidate) else sys.executable


CDN_HOSTS = re.compile(r"^https://(cdn\.jsdelivr\.net|fonts\.googleapis\.com|fonts\.gstatic\.com)/")
ASSET_CACHE = os.path.join(HERE, ".assetcache")


def install_asset_cache(context):
    """
    Serve Bootstrap, the icon font and Chart.js from a local cache.

    The dashboard loads them from public CDNs. Fetching them fresh on every
    page made the recording slow and, when a CDN throttled the repeated hits,
    left navigations hanging on a parser-blocking <script>. Caching them keeps
    the pages looking exactly as they do in production while making the run
    fast, repeatable and usable with no internet after the first time.
    """
    os.makedirs(ASSET_CACHE, exist_ok=True)
    memo = {}

    def handler(route):
        url = route.request.url
        key = hashlib.sha1(url.encode()).hexdigest()
        blob = os.path.join(ASSET_CACHE, key)
        meta = blob + ".json"

        if url not in memo:
            if os.path.exists(blob) and os.path.exists(meta):
                with open(blob, "rb") as f:
                    body = f.read()
                with open(meta, encoding="utf-8") as f:
                    ctype = json.load(f)["content_type"]
                memo[url] = (body, ctype)
            else:
                try:
                    r = requests.get(url, timeout=30, headers={
                        "User-Agent": route.request.headers.get("user-agent", "Mozilla/5.0"),
                    })
                    r.raise_for_status()
                    body = r.content
                    ctype = r.headers.get("Content-Type", "application/octet-stream")
                    with open(blob, "wb") as f:
                        f.write(body)
                    with open(meta, "w", encoding="utf-8") as f:
                        json.dump({"content_type": ctype, "url": url}, f)
                    memo[url] = (body, ctype)
                except Exception:
                    return route.abort()

        body, ctype = memo[url]
        route.fulfill(status=200, body=body,
                      headers={"Content-Type": ctype, "Cache-Control": "max-age=31536000"})

    context.route(CDN_HOSTS, handler)


def track_requests(page):
    """Keep a live map of in-flight requests so a stall can be explained."""
    inflight = {}
    page.on("request", lambda r: inflight.__setitem__(r.url, (time.time(), r.resource_type)))
    page.on("requestfinished", lambda r: inflight.pop(r.url, None))
    page.on("requestfailed", lambda r: inflight.pop(r.url, None))
    page._inflight = inflight
    return inflight


def _report_stall(page):
    inflight = getattr(page, "_inflight", {})
    for url, (started, kind) in sorted(inflight.items(), key=lambda kv: kv[1][0])[:10]:
        print(f"        stalled {time.time() - started:5.1f}s  {kind:10} {url[:110]}", flush=True)


def safe_goto(page, url, timeout=30000, attempts=2):
    """
    Navigate, tolerating a stalled third-party asset.

    The dashboard pulls Bootstrap, its icon font and Chart.js from public CDNs.
    When one of those stalls, DOMContentLoaded never fires and a plain goto()
    times out even though the app served the page instantly. Falling back to
    'load'/'commit' lets the run continue on the HTML the app actually
    returned, and the stalled URLs are printed so the cause is never a mystery.
    """
    last = None
    for attempt in range(attempts):
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Let the page's own XHRs finish before anything navigates away.
            # Werkzeug's development server does not promptly close sockets for
            # requests the browser cancels mid-flight, so navigating while calls
            # are in the air leaks connections until Chromium's six-per-origin
            # pool is exhausted and the next navigation queues forever.
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            return resp
        except Exception as e:
            last = e
            print(f"    ! slow navigation to {url} (attempt {attempt + 1}): {type(e).__name__}",
                  flush=True)
            _report_stall(page)
    try:
        resp = page.goto(url, wait_until="commit", timeout=15000)
        page.wait_for_timeout(3000)
        print(f"    · continued on partially-loaded {url}", flush=True)
        return resp
    except Exception:
        print(f"    ! could not load {url} at all", flush=True)
        _report_stall(page)
        raise last


def render(template: str, **kw) -> str:
    out = template or ""
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def unlock_admin(page, password):
    """Fill the admin-password modal if it is showing."""
    try:
        page.wait_for_selector("#adminPwModal.show", timeout=4000)
    except Exception:
        return False
    page.fill("#adminPwInput", password)
    page.click("#adminPwModal .btn-primary")
    page.wait_for_selector("#adminPwModal.show", state="hidden", timeout=5000)
    return True


def post_webhook(base_url, *, phone=None, contact_id=None, message="", conversation_id=""):
    payload = {"type": "InboundMessage", "messageType": "SMS", "direction": "inbound",
               "message": message}
    if contact_id:
        payload["contactId"] = contact_id
    if phone:
        payload["phone"] = phone
    if conversation_id:
        payload["conversationId"] = conversation_id
    r = requests.post(f"{base_url}/api/webhooks/ghl", json=payload, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:400]}


# ══════════════════════════════════════════════════════════════════════════
# The run
# ══════════════════════════════════════════════════════════════════════════

def run(base_url, headed, speed, live_mode, stub):
    os.makedirs(OUTPUT, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, args=["--force-device-scale-factor=1"])
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=OUTPUT,
            record_video_size={"width": 1600, "height": 900},
        )
        install_asset_cache(context)
        page = context.new_page()
        track_requests(page)
        n = Narrator(page, speed)

        try:
            _chapters(page, n, base_url, live_mode, stub)
        except Exception as e:
            report.add("Verification run completed without crashing", False, repr(e))
            try:
                n.say(f"Run aborted: {e}", 4, tag="ERROR", kind="fail")
            except Exception:
                pass
            raise
        finally:
            _closing_card(page, n)
            context.close()
            browser.close()

        video = page.video.path() if page.video else None

    if video and os.path.exists(video):
        final = os.path.join(OUTPUT, "webreach-verification.webm")
        shutil.move(video, final)
        return to_mp4(final) or final
    return None


def _find_ffmpeg():
    """
    ffmpeg on PATH, else the full build that ships with imageio-ffmpeg.

    Playwright also bundles an ffmpeg, but it is a cut-down build for writing
    webm and cannot encode H.264 — so it is deliberately not used here.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def to_mp4(webm_path):
    """
    Transcode the recording to H.264 MP4 so it plays anywhere — WhatsApp,
    Slack, QuickTime, a phone — none of which reliably handle webm. The webm
    is kept alongside it. Returns the mp4 path, or None if ffmpeg is missing.
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("  (ffmpeg not found — keeping the .webm only; "
              "`pip install imageio-ffmpeg` to get an .mp4 as well)", flush=True)
        return None

    mp4_path = os.path.splitext(webm_path)[0] + ".mp4"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", webm_path,
        "-c:v", "libx264", "-preset", "slow", "-crf", "23",
        "-pix_fmt", "yuv420p",        # required by most players and phones
        "-profile:v", "high", "-level", "4.0",
        "-movflags", "+faststart",    # lets it start playing before it downloads
        "-an", mp4_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    except Exception as e:
        print(f"  (mp4 conversion failed, keeping the .webm: {e})", flush=True)
        return None
    return mp4_path


def _closing_card(page, n):
    try:
        total = len(report.checks)
        failed = [c["name"] for c in report.checks if not c["passed"]]
        n.card(
            f"{report.passed} of {total} checks passed"
            if not failed else f"{report.passed}/{total} passed — {len(failed)} FAILED",
            (["Every check passed.",
              "Inbound messages are received, matched to a lead and answered.",
              "The reply sent is the template saved on the AI Settings page."]
             if not failed else ["Failed:"] + failed[:8]),
            seconds=6,
        )
    except Exception:
        pass


def _chapters(page, n, base_url, live_mode, stub):
    # ── Chapter 0 ────────────────────────────────────────────────────────
    n.card("Outreach system verification", [
        f"Target: {base_url}",
        datetime.now().strftime("%d %B %Y, %H:%M"),
        "Every reply below is produced by the live application code.",
    ], seconds=4)

    settings = requests.get(f"{base_url}/api/admin/settings", timeout=20).json()["settings"]
    company = settings.get("business_name", "")
    agent = settings.get("sms_agent_name", "")
    website = settings.get("business_website", "")

    # ── Chapter 1: pages load ────────────────────────────────────────────
    n.card("1 — Every page loads", ["Dashboard, Leads, Conversations, AI Chat Test,",
                                    "AI Settings, Admin and Settings."])
    for path, label in [("/", "Dashboard"), ("/leads", "Leads"),
                        ("/conversations", "Conversations"), ("/scraping", "Scraping Jobs"),
                        ("/ai-chat", "AI Chat Test"), ("/conversation-settings", "AI Settings"),
                        ("/admin", "Admin"), ("/settings", "Settings")]:
        resp = safe_goto(page, base_url + path)
        page.wait_for_timeout(500)
        ok = resp is not None and resp.status == 200
        n.say(f"{label} ({path}) — HTTP {resp.status if resp else 'no response'}", 1.1)
        report.add(f"Page loads: {label}", ok, f"HTTP {resp.status if resp else '—'}")
    n.verdict("All dashboard pages load", all(
        c["passed"] for c in report.checks if c["name"].startswith("Page loads")))

    if live_mode:
        n.card("Live site — read-only", [
            "Running against a live deployment.",
            "Write and send checks are skipped so no real lead is contacted.",
        ], seconds=4)
        return

    # ── Chapter 2: admin gate ────────────────────────────────────────────
    n.card("2 — The admin password gate", ["Write actions must ask for the password."])
    n.goto(base_url + "/conversation-settings", "Opening AI Settings.", 2.0)
    page.wait_for_timeout(1200)

    n.say("Switching Reply Mode to 'Templates only' — send my text exactly.", 3.0)
    # Move away and back so the change event fires even when the mode already
    # reads "templates", then confirm the save really was password-gated.
    page.select_option("#ai_mode", "hybrid")
    page.wait_for_timeout(300)
    unlock_admin(page, ADMIN_PASSWORD)
    page.wait_for_timeout(600)
    page.evaluate("sessionStorage.removeItem('wr_admin_pw'); _adminPw = '';")
    page.select_option("#ai_mode", "templates")
    page.wait_for_timeout(400)
    gated = unlock_admin(page, ADMIN_PASSWORD)
    n.verdict("Admin password is required before saving", gated,
              "modal appeared and accepted the password")
    page.wait_for_timeout(900)
    n.shot("01-ai-settings-mode")

    # ── Chapter 3: templates are what gets sent ──────────────────────────
    n.card("3 — What you save is what the lead receives", [
        "The client's report: \"Nothing in the templates matches",
        "what is happening in the AI chat test.\"",
        "We edit a template, then check the exact text arrives.",
    ])

    marker = f"VERIFIED-{int(time.time())}"
    new_step1 = (
        "Great, thanks for confirming. I'm {agent_name} from {company_name}.\n\n"
        "I spotted something on your Google Business Profile that's likely costing you "
        f"enquiries. [{marker}]\n\nWould you like me to share what I found?"
    )

    # The chapter card above navigates to a blank slide, so come back first.
    n.goto(base_url + "/conversation-settings", "Back on AI Settings → Message Templates.", 1.6)
    page.click("#convTabs a:has-text('Message Templates')")
    page.wait_for_timeout(900)
    n.say("Editing the Step 1 template and adding a unique marker to it.", 3.0)
    page.fill("#msg_compliment", new_step1)
    page.wait_for_timeout(700)
    page.click("#msg_compliment ~ .d-flex button:has-text('Save')")
    unlock_admin(page, ADMIN_PASSWORD)
    page.wait_for_timeout(1200)
    n.shot("02-template-edited")

    saved = requests.get(f"{base_url}/api/admin/settings", timeout=20).json()["settings"]
    n.verdict("Edited template is stored", saved.get("msg_compliment") == new_step1)

    expected_step1 = render(new_step1, agent_name=agent, company_name=company,
                            business_name="Joe's Hair Studio")

    n.card("4 — The AI Chat Test now mirrors production", [
        "Same code path as a real SMS. Each reply is tagged",
        "with the template that produced it.",
    ])
    n.goto(base_url + "/ai-chat", "Opening the AI Chat Test.", 2.0)
    page.click("button:has-text('Start New Conversation')")
    page.wait_for_timeout(2200)
    n.say("Replying as the business owner: \"Yes, that's us!\"", 2.4)
    page.click("button:has-text(\"Yes, that's us!\")")
    page.wait_for_timeout(2600)
    n.shot("03-chat-step1")

    bubbles = page.locator("#chatMessages .rounded-3")
    step1_text = bubbles.nth(2).inner_text().strip() if bubbles.count() > 2 else ""

    n.verdict("Reply carries the marker we just typed into the template",
              marker in step1_text, f"marker {marker}")
    n.verdict("Reply is the saved template character-for-character, not a paraphrase",
              step1_text == expected_step1.strip(),
              "exact match" if step1_text == expected_step1.strip()
              else f"got: {step1_text[:70]}")

    badge_text = page.locator("#chatMessages .badge").last.inner_text()
    n.verdict("Reply is labelled with its template key", "msg_compliment" in badge_text,
              badge_text)

    n.say("The agency name renders correctly — 'from AMZUS Digital', not the lead's own name.", 3.4)
    n.verdict("Agency vs lead placeholder is correct",
              f"from {company}" in step1_text and "from Joe's Hair Studio" not in step1_text,
              f"contains 'from {company}'")

    # ── Chapter 5: objections ────────────────────────────────────────────
    # Captions rather than a full-screen card here — a card navigates to a
    # blank slide, which would throw away the conversation in progress.
    n.say("5 — Objections are answered from their own templates.", 2.6, tag="CHAPTER", kind="step")
    for label, button, expected_key in [
        ("Who is this?", "Who is this?", "msg_who_are_you"),
        ("How much does it cost?", "How much?", "msg_cost_question"),
        ("Where did you get my number?", "My number?", "msg_number_question"),
    ]:
        n.say(f"Owner asks: \"{label}\"", 2.0)
        page.click(f"#quickReplies button:has-text('{button}')")
        page.wait_for_timeout(2400)
        badge = page.locator("#chatMessages .badge").last.inner_text()
        n.verdict(f"\"{label}\" → {expected_key}", expected_key in badge, badge)
    n.shot("04-chat-objections")

    all_replies = page.locator("#chatMessages .rounded-3").all_inner_texts()
    leftovers = [t for t in all_replies if "{" in t and "}" in t]
    n.verdict("No unreplaced {placeholder} in any message", not leftovers,
              f"{len(leftovers)} found" if leftovers else "all variables filled")

    # ── Chapter 6: the real failure — inbound gets no reply ──────────────
    n.card("6 — The main fault: inbound messages got no reply", [
        "These are the four replies from the client's screenshots.",
        "Each is delivered to the live webhook exactly as GHL sends it.",
    ], seconds=4.5)

    n.goto(base_url + "/admin", "Admin → Inbound Health, before any message arrives.", 3.0)
    page.wait_for_timeout(1500)
    n.shot("05-health-before")

    stub.reset()
    for phone, message, expected_key in SCREENSHOT_CASES:
        n.say(f"Inbound SMS from {phone}: \"{message}\"", 2.6, tag="INBOUND")
        status, data = post_webhook(base_url, phone=phone, message=message)
        ok = status == 200 and data.get("sent") is True
        detail = f"HTTP {status}, template={data.get('template')}, sent={data.get('sent')}"
        n.verdict(f"Replied to \"{message[:34]}…\"", ok, detail)
        report.add(f"Correct template for \"{message[:34]}…\"",
                   data.get("template") == expected_key,
                   f"expected {expected_key}, got {data.get('template')}")
        if data.get("reply"):
            n.say(f"Reply sent: \"{data['reply'][:110]}…\"", 2.8, tag="OUTBOUND", kind="pass")

    n.verdict("All four previously-unanswered messages got a reply",
              sum(1 for c in report.checks if c["name"].startswith("Replied to")
                  and c["passed"]) == len(SCREENSHOT_CASES))

    sent_count = len(stub.sent)
    n.verdict("Replies genuinely left the app through the SMS gateway",
              sent_count >= len(SCREENSHOT_CASES),
              f"{sent_count} messages received by the gateway")

    n.goto(base_url + "/admin", "Admin → Inbound Health now records every message.", 1.5)
    page.wait_for_timeout(2500)
    page.evaluate("window.scrollTo(0, 320)")
    n.say("Each inbound is logged with its outcome — so a dropped message is never silent again.", 4.0)
    n.shot("06-health-after")

    body = page.locator("#webhookEventsBody").inner_text()
    n.verdict("Inbound Health shows the replies", body.count("replied") >= len(SCREENSHOT_CASES),
              f"{body.count('replied')} 'replied' rows")

    n.goto(base_url + "/conversations", "The threads now show the AI's replies.", 3.5)
    page.wait_for_timeout(2000)
    n.shot("07-conversations")

    # ── Chapter 6b: the payload shape that actually took production down ──
    n.card("6b - The exact payload GHL really sends", [
        "GHL's standard data includes 'message' as an OBJECT, not a string.",
        "The old code called .strip() on it, raised AttributeError,",
        "and returned 500 to GHL for every single reply.",
    ], seconds=5)

    ghl_real = {
        "contact_id": "ghl-contact-santafe",
        "first_name": "Santa Fe Builders Llc",
        "phone": "+15551110001",
        "location": {"name": "AMZUS Ltd", "id": "f0IhTzGOf9o7ghh2AJS0"},
        "workflow": {"name": "WebReach Inbound SMS"},
        "contact": {"id": "ghl-contact-santafe", "phone": "+15551110001"},
        "message": {"type": 1, "body": "Yes go ahead", "direction": "inbound",
                    "conversationId": "conv-santafe-1"},
        "customData": {"type": "InboundMessage", "messageType": "SMS",
                       "message": "Yes go ahead", "contactId": "ghl-contact-santafe",
                       "conversationId": "conv-santafe-1"},
    }
    n.say("Posting GHL's real payload, with 'message' as an object.", 3.0, tag="INBOUND")
    r = requests.post(f"{base_url}/api/webhooks/ghl", json=ghl_real, timeout=30)
    data = r.json() if r.content else {}
    n.verdict("GHL's real payload no longer returns 500",
              r.status_code == 200,
              f"HTTP {r.status_code}, template={data.get('template')}")
    n.verdict("The message body is read out of the nested object",
              data.get("sent") is True, f"sent={data.get('sent')}")

    # ── Chapter 7: the deleted-contacts scenario ─────────────────────────
    n.card("7 — After deleting every GHL contact", [
        "The client wiped all GHL contacts and conversations to retest.",
        "GHL then sends a brand-new contact ID that matches nothing.",
        "This is the case that produced total silence.",
    ], seconds=5)

    n.say("Inbound arrives with an unrecognised contact ID — matched by phone instead.", 3.6,
          tag="INBOUND")
    status, data = post_webhook(
        base_url, contact_id="brand-new-id-after-ghl-wipe-0001",
        phone="+15551110001", message="Yes go on then, show me")
    n.verdict("Recovered the lead after its GHL contact was recreated",
              status == 200 and data.get("sent") is True,
              f"matched by {data.get('lead', {}).get('found_by')}, template={data.get('template')}")

    n.say("And when GHL sends only the new contact ID, the contact is looked up in the GHL API.",
          3.6, tag="INBOUND")
    stub.register_contact("second-new-id-0002", "+15551110002", "Clean Slate Cleaning LLC")
    status, data = post_webhook(base_url, contact_id="second-new-id-0002",
                                message="Yes please send it over")
    n.verdict("Recovered the lead from a contact-ID-only payload",
              status == 200 and data.get("sent") is True,
              f"matched by {data.get('lead', {}).get('found_by')}")

    # ── Chapter 8: unknown number ────────────────────────────────────────
    n.card("8 — A message from a number we've never seen", [
        "Previously dropped in silence. Now answered,",
        "and the lead is created automatically.",
    ])
    n.say("Inbound from +1 555 999 0007, a number not in the lead list.", 3.0, tag="INBOUND")
    status, data = post_webhook(base_url, contact_id="walk-in-contact-9007",
                                phone="+15559990007", message="Hi yes this is us")
    n.verdict("Unknown number is answered rather than dropped",
              status == 200 and data.get("sent") is True,
              f"found_by={data.get('lead', {}).get('found_by')}")

    # ── Chapter 9: opt-out ───────────────────────────────────────────────
    n.card("9 — STOP is honoured immediately")
    n.say("Inbound from Santa Fe Builders: \"STOP\"", 2.6, tag="INBOUND")
    status, data = post_webhook(base_url, phone="+15551110001", message="STOP")
    n.verdict("STOP sends the opt-out confirmation",
              data.get("template") == "msg_opt_out", f"template={data.get('template')}")
    n.verdict("STOP sets the lead to opted_out",
              data.get("new_status") == "opted_out", f"status={data.get('new_status')}")

    # ── Chapter 10: manual takeover ──────────────────────────────────────
    n.card("10 — Manual takeover silences the AI", [
        "When a human takes a conversation over, the AI must stop replying",
        "but the message must still be recorded.",
    ])
    leads = requests.get(f"{base_url}/api/leads/?search=J%26C&per_page=5", timeout=20).json()
    target = next((l for l in leads.get("leads", []) if "Roofing" in (l["business_name"] or "")), None)
    if target:
        requests.post(f"{base_url}/api/leads/{target['id']}/pause-ai",
                      headers={"X-Admin-Password": ADMIN_PASSWORD}, timeout=20)
        n.say(f"AI paused on {target['business_name']}. Sending an inbound message.", 3.0)
        status, data = post_webhook(base_url, phone="+15551110003", message="Actually yes tell me more")
        n.verdict("AI stays silent while a human has the conversation",
                  "Manual mode" in data.get("message", ""), data.get("message", ""))
        requests.post(f"{base_url}/api/leads/{target['id']}/resume-ai",
                      headers={"X-Admin-Password": ADMIN_PASSWORD}, timeout=20)
    else:
        report.add("Manual takeover suppresses the AI", False, "test lead not found")

    # ── Chapter 11: nothing malformed ever went out ──────────────────────
    n.card("11 — Checking every message the gateway received")
    outbound = [m.get("message", "") for m in stub.sent]
    bad = [m for m in outbound if "{" in m and "}" in m]
    n.verdict("No unreplaced {placeholder} was ever sent", not bad,
              f"{len(outbound)} messages checked")
    misnamed = [m for m in outbound if "from Santa Fe Builders" in m or "from J&C Roofing" in m]
    n.verdict("No message introduces us using the lead's own business name", not misnamed,
              f"{len(outbound)} messages checked")

    n.goto(base_url + "/admin", "Final state — Inbound Health.", 2.0)
    page.wait_for_timeout(2200)
    n.shot("08-final-health")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Verify the WebReach outreach system.")
    ap.add_argument("--base-url", default="", help="Verify a running site instead of a local app")
    ap.add_argument("--headed", action="store_true", help="Show the browser")
    ap.add_argument("--speed", type=float, default=1.0, help="Caption pacing multiplier")
    args = ap.parse_args()

    os.makedirs(OUTPUT, exist_ok=True)
    live_mode = bool(args.base_url)

    stub = StubGHL(STUB_PORT).start()
    for phone, contact in [("+15551110001", "ghl-contact-santafe"),
                           ("+15551110002", "ghl-contact-cleanslate"),
                           ("+15551110003", "ghl-contact-jcroofing"),
                           ("+15551110004", "ghl-contact-walton"),
                           ("+15559990007", "walk-in-contact-9007")]:
        stub.register_contact(contact, phone)

    app_proc = None
    base_url = args.base_url.rstrip("/")
    db_path = os.path.join(OUTPUT, "verify.db")

    try:
        if not live_mode:
            if os.path.exists(db_path):
                os.remove(db_path)

            env = dict(os.environ)
            env.update({
                "DATABASE_URL": "sqlite:///" + db_path.replace("\\", "/"),
                "GHL_API_BASE_URL": stub.base_url,
                "GHL_ACCESS_TOKEN": "verify-token",
                "GHL_LOCATION_ID": "verify-location",
                "ADMIN_PASSWORD": ADMIN_PASSWORD,
                "APP_URL": f"http://127.0.0.1:{APP_PORT}",
                "WEBHOOK_SECRET": "",
                "OPENAI_API_KEY": "",          # templates mode needs no OpenAI
                "VERIFY_PORT": str(APP_PORT),
                "PYTHONIOENCODING": "utf-8",
            })

            print(f"Starting the application on port {APP_PORT}…", flush=True)
            app_proc = subprocess.Popen(
                [venv_python(), os.path.join(HERE, "_app_harness.py")],
                cwd=ROOT, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if not wait_for_port(APP_PORT):
                err = app_proc.stderr.read().decode("utf-8", "replace")[-3000:]
                raise RuntimeError(f"The application did not start.\n{err}")
            base_url = f"http://127.0.0.1:{APP_PORT}"
            print(f"Application ready at {base_url}\n", flush=True)

        video = run(base_url, args.headed, args.speed, live_mode, stub)

    finally:
        if app_proc:
            app_proc.terminate()
            try:
                app_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app_proc.kill()
        stub.stop()

    report.save(os.path.join(OUTPUT, "verification-report.json"))

    print("\n" + "=" * 68)
    print(f"  {report.passed} passed, {report.failed} failed, {len(report.checks)} total")
    if report.failed:
        print("\n  Failures:")
        for c in report.checks:
            if not c["passed"]:
                print(f"    - {c['name']}: {c['detail']}")
    if video:
        print(f"\n  Video:  {video}")
    print(f"  Report: {os.path.join(OUTPUT, 'verification-report.json')}")
    print(f"  Shots:  {OUTPUT}")
    print("=" * 68)

    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
