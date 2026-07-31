"""
Knock-on effects of a settings change.

Several settings quietly override or depend on others. Raising the Daily SMS
Limit does nothing while a warm-up is running; narrowing the sending window
parks whatever is already queued; switching Reply Mode to AI stops the saved
templates being sent verbatim. None of that was visible at the moment of the
change, so it surfaced later as "it isn't working".

advise() is called after a save and returns warnings about what else the change
touched, each with somewhere to go and fix it.
"""

import logging

logger = logging.getLogger(__name__)


def _warn(level, message, where=None, url=None):
    return {"level": level, "message": message, "where": where, "url": url}


def advise(changed: dict) -> list:
    """
    Warnings for the settings just saved. `changed` maps key -> new value.
    Only fires when the interaction is genuinely live: nothing is said about a
    warm-up that isn't running, or a queue that is empty.
    """
    from models import AppSettings
    from flask import current_app

    notes = []
    if not changed:
        return notes

    try:
        from services.smart_sender import get_queue_state, get_limit_info
        app = current_app._get_current_object()
        queue = get_queue_state(app)
        limit_info = get_limit_info(app)
    except Exception as e:
        logger.warning(f"Could not build settings advice: {e}")
        return notes

    pending = queue.get("pending", 0)
    warmup_running = bool(AppSettings.get("warmup_start_date", ""))
    smart_on = AppSettings.get("smart_send_enabled", "false").lower() == "true"

    # --- The one that cost an hour -------------------------------------
    if "daily_send_limit" in changed and warmup_running:
        notes.append(_warn(
            "blocker",
            f"This has no effect right now. A warm-up is running, so today's limit is "
            f"{limit_info['limit']} from the warm-up curve, not the "
            f"{changed['daily_send_limit']} you just set. To use your own number, "
            f"turn Smart Send off or raise the warm-up settings.",
            "Admin -> Smart Send Scheduler", "/admin"))

    if any(k.startswith("warmup_") for k in changed) and not warmup_running:
        notes.append(_warn(
            "info",
            "Warm-up settings saved, but no warm-up is running — they take effect "
            "only after you press Start Warm-Up.",
            "Admin -> Smart Send Scheduler", "/admin"))

    # --- Sending window and pace ---------------------------------------
    window_keys = {"send_start_time", "send_end_time", "send_timezone"}
    if window_keys & set(changed) and pending:
        if queue.get("within_window"):
            notes.append(_warn(
                "info",
                f"{pending} queued message(s) are inside the new sending window and "
                f"will continue to go out."))
        else:
            opens = queue.get("window_opens_at") or "the next window"
            notes.append(_warn(
                "blocker",
                f"{pending} message(s) are already queued and are now OUTSIDE your "
                f"sending hours. They will not send until {opens[11:16] if len(opens) > 16 else opens}"
                f" ({queue.get('timezone')}).",
                "Leads -> Send now, to push one through immediately", "/leads"))

    if {"send_min_interval_mins", "send_max_interval_mins"} & set(changed) and pending:
        notes.append(_warn(
            "info",
            f"{pending} queued message(s) will now go out "
            f"{queue['min_gap_mins']}-{queue['max_gap_mins']} minutes apart — roughly "
            f"{queue['estimated_minutes']} minutes in total."))

    # --- Smart Send vs the manual queue --------------------------------
    if "smart_send_enabled" in changed:
        if str(changed["smart_send_enabled"]).lower() == "true":
            notes.append(_warn(
                "info",
                "Smart Send is on. It sends continuously on its own, and the manual "
                "Send Bulk SMS button is disabled while it runs."
                + (f" Your {pending} already-queued message(s) will still go out." if pending else ""),
                "Admin -> Smart Send Scheduler", "/admin"))
        elif warmup_running:
            notes.append(_warn(
                "info",
                "Smart Send is off, but the warm-up start date is still set, so the "
                "warm-up curve continues to cap the daily limit. Clear it if you want "
                "your Daily SMS Limit to apply.",
                "Admin -> Smart Send Scheduler", "/admin"))

    # --- Reply behaviour ------------------------------------------------
    if "ai_mode" in changed:
        mode = str(changed["ai_mode"])
        if mode == "ai":
            notes.append(_warn(
                "blocker",
                "Replies will now be written by OpenAI and will NOT match the templates "
                "you have saved. Use Templates only if you want your exact wording sent.",
                "AI Settings -> Reply Mode", "/conversation-settings"))
        elif mode in ("hybrid", "ai"):
            notes.append(_warn("info", "OpenAI will be used for off-script questions."))

        if mode in ("hybrid", "ai"):
            try:
                from services.conversation_ai import openai_available
                if not openai_available():
                    notes.append(_warn(
                        "blocker",
                        "No OpenAI key is configured, so this mode cannot work — the saved "
                        "templates will be used regardless. Add OPENAI_API_KEY on the server.",
                        "Settings", "/settings"))
            except Exception:
                pass

    # --- Templates ------------------------------------------------------
    for key in ("msg_compliment", "msg_who_are_you", "ai_system_prompt"):
        value = str(changed.get(key, ""))
        if "from {business_name}" in value:
            notes.append(_warn(
                "blocker",
                f"'{key}' says \"from {{business_name}}\", which is the LEAD's name — it "
                f"would read \"I'm Sarah from Joe's Hair Studio\". Use {{company_name}} "
                f"for your own agency.",
                "AI Settings -> Message Templates", "/conversation-settings"))

    if AppSettings.get("ai_mode", "templates") == "ai" and any(
            k.startswith("msg_") for k in changed):
        notes.append(_warn(
            "info",
            "Reply Mode is 'AI writes everything', so this template is only a style "
            "guide — the wording actually sent will differ.",
            "AI Settings -> Reply Mode", "/conversation-settings"))

    # --- Follow-ups -----------------------------------------------------
    if changed.get("followup_enabled", "").lower() == "false":
        notes.append(_warn(
            "info",
            "Automatic follow-ups are off. Leads who never reply will not be chased again."))

    return notes
