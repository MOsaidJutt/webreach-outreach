"""
Build marker.

Bump this whenever code is deployed. It is shown on the Admin page and
included in the diagnostics bundle, so "is the fix actually running on the
server?" is a question that can be answered in one glance instead of guessed at.
"""

BUILD = "2026-07-31.1"

# What this build contains, newest first. Keep it short — it is displayed
# in the UI and pasted into support messages.
CHANGES = [
    "Quietened APScheduler's per-minute log spam so the log is readable",
    "Paced bulk sending — the Sending Window and min/max gap now apply to every send path",
    "Schedulers run in a single process, so multi-worker deploys cannot double-send",
    "One-click diagnostics bundle on the Admin page",
    "Inbound webhook recovers leads by phone and by GHL contact lookup",
    "Templates are sent verbatim (Reply Mode: Templates only)",
]
