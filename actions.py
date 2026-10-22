"""Things Maja can do for Matty outside the chat: add a calendar event and draft an email.

Maja never sends or saves anything on her own. She first shows what she'd do and waits for
"yes". Then she opens it in Matty's own app (the calendar event in Outlook or Windows
Calendar, the email as a draft in the mail app), where Matty saves or sends it.
"""

import os
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reminders import DUE_FORMAT, guess_due, parse_due

CALENDAR_REQUEST = re.compile(
    r"^\s*(?:add (?:this )?to (?:my )?calendar|new (?:calendar )?event|calendar event)\s*[:,-]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
EMAIL_REQUEST = re.compile(
    r"^\s*(?:draft|write) (?:an |a )?(?:e-?mail)\s*[:,-]?\s*(.+)$|^\s*e-?mail draft\s*[:,-]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
YES = {"yes", "y", "yes please", "yep", "ok", "okay", "do it", "go ahead", "sure"}
NO = {"no", "n", "nope", "cancel", "stop", "don't", "no thanks"}
MAILTO_LIMIT = 1800  # Longer mailto links get cut off by some mail apps.

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "start": {"type": "string"},
        "duration_minutes": {"type": "integer"},
        "location": {"type": "string"},
    },
    "required": ["title", "start", "duration_minutes", "location"],
}
EMAIL_SCHEMA = {
    "type": "object",
    "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["to", "subject", "body"],
}


def open_with_windows(target):
    """Open a file or link with the app Windows uses for it."""
    os.startfile(target)


def _ics_text(value):
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_event_file(event, folder):
    """Save the event as an .ics file, which calendar apps open as a new event to save."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(event["start"], DUE_FORMAT)
    end = start + timedelta(minutes=event["duration_minutes"])
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Maja//Event//EN", "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@maja", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{_ics_text(event['title'])}",
    ]
    if event.get("location"):
        lines.append(f"LOCATION:{_ics_text(event['location'])}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    path = folder / f"event-{start:%Y%m%d-%H%M}.ics"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def mailto_link(email):
    """A mailto: link that opens the email as a draft. Long bodies are shortened."""
    body = email["body"]
    query = urllib.parse.urlencode({"subject": email["subject"], "body": body}, quote_via=urllib.parse.quote)
    if len(query) > MAILTO_LIMIT:
        body = body[:MAILTO_LIMIT // 3].rsplit(" ", 1)[0] + " ..."
        query = urllib.parse.urlencode({"subject": email["subject"], "body": body}, quote_via=urllib.parse.quote)
    recipient = email["to"] if "@" in email["to"] else ""
    return f"mailto:{urllib.parse.quote(recipient, safe='@.+')}?{query}"


def plan_event(brain, request, now=None):
    """Turn 'dentist Friday at 3pm' into an event. Python reads the time when it can."""
    now = now or datetime.now()
    days = "\n".join(f"- {(now + timedelta(days=i)):%A %Y-%m-%d}" for i in range(8))
    answer = brain.extract([
        {"role": "system", "content": (
            "Turn Matty's request into one calendar event. title: short, without the date or time. "
            "start: 'YYYY-MM-DD HH:MM' using the calendar below (only a day: 09:00). "
            "duration_minutes: as stated, otherwise 60. location: as stated, otherwise ''.\n\n"
            f"Now: {now:%A %Y-%m-%d %H:%M}\nCalendar:\n{days}")},
        {"role": "user", "content": request},
    ], EVENT_SCHEMA)
    start = guess_due(request, now) or parse_due(answer.get("start", ""), now)
    if start is None:
        return None
    duration = answer.get("duration_minutes")
    return {
        "title": str(answer.get("title") or request).strip()[:120],
        "start": start.strftime(DUE_FORMAT),
        "duration_minutes": duration if isinstance(duration, int) and 5 <= duration <= 24 * 60 else 60,
        "location": str(answer.get("location") or "").strip()[:120],
    }


def plan_email(brain, request):
    answer = brain.extract([
        {"role": "system", "content": (
            "Write the email Matty asks for. to: the email address if Matty gave one, otherwise the "
            "person's name. subject: short. body: friendly and clear, at most 150 words, written as "
            "Matty, ending with 'Best regards,\\nMatty'. Don't invent facts Matty didn't give.")},
        {"role": "user", "content": request},
    ], EMAIL_SCHEMA)
    email = {key: str(answer.get(key) or "").strip() for key in ("to", "subject", "body")}
    return email if email["body"] else None
