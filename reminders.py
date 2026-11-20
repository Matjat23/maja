"""Due dates for tasks and Windows notifications for reminders."""

import calendar
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

DUE_FORMAT = "%Y-%m-%d %H:%M"


def parse_due(text, now=None):
    """Turn 'YYYY-MM-DD HH:MM' into a datetime, or None if it's empty, invalid, or in the past."""
    try:
        due = datetime.strptime(str(text).strip(), DUE_FORMAT)
    except ValueError:
        return None
    return due if due > (now or datetime.now()) else None


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "half an": 0.5}
_IN = re.compile(r"\bin (\d+|a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|half an) "
                 r"(minute|min|hour|day)s?\b")
_AT = re.compile(r"\b(?:at|by) (\d{1,2})(?:[:.](\d{2}))? ?(am|pm|a\.m\.|p\.m\.)?(?!\d)")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"(?:,?\s+(\d{4}))?")
_MONTH_DAY = re.compile(r"\b" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!:)(?:,?\s+(\d{4}))?")
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# 15/12, 12/25 or 15/12/2026, and with dots only when there's a year (15.12.2026), because
# "18.30" is a time and "3.5 hours" isn't a date. Never right after "at" or "by" (a time).
_NUMERIC = re.compile(r"(?<!\bat )(?<!\bby )(?<![\d:.])(?:(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?"
                      r"|(\d{1,2})\.(\d{1,2})\.(\d{4}))(?![\d:./])")


def explicit_date(text, now):
    """A written date like 'December 15', '15 Dec', '15/12' or '2026-12-15'.

    Returns (date or None, found). found is True when the message contains something that
    looks like a date, even if it's not a real one ('31/02'), so the caller doesn't fall
    back to guessing 'today'. Numbers are read day first (15/12), unless only month first
    makes sense (12/25). A date without a year that has passed means next year.
    """
    candidates = []
    if match := _ISO.search(text):
        candidates.append((int(match[1]), int(match[2]), int(match[3])))
    elif match := _DAY_MONTH.search(text):
        candidates.append((match[3] and int(match[3]), MONTHS.index(match[2]) + 1, int(match[1])))
    elif match := _MONTH_DAY.search(text):
        candidates.append((match[3] and int(match[3]), MONTHS.index(match[1]) + 1, int(match[2])))
    elif match := _NUMERIC.search(text):
        parts = match.groups()[:3] if match[1] else match.groups()[3:]
        first, second = int(parts[0]), int(parts[1])
        year = parts[2] and int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
        day_first, month_first = (first, second), (second, first)
        day, month = day_first if second <= 12 else month_first
        candidates.append((year, month, day))
    if not candidates:
        return None, False
    year, month, day = candidates[0]
    try:
        found = date(year or now.year, month, day)
        if not year and found < now.date():
            found = date(now.year + 1, month, day)
    except ValueError:
        return None, True
    return found, True


def guess_due(message, now=None):
    """Find a reminder time in plain English, like 'in 2 hours', 'at 7am', 'tomorrow',
    'December 15 at 3pm' or 'Friday at 18:30'. Returns a datetime in the future, or None.

    This backs up the AI model, which is unreliable at this kind of date math. When the
    message has a date it can't read, it returns None instead of guessing a wrong day.
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    text = message.lower()

    day = None
    weekday = False  # "Friday" on a Friday means today if the time hasn't passed, else next week.
    relative = _IN.search(text)
    written, has_date = explicit_date(text, now)
    if relative:
        amount = _NUMBER_WORDS.get(relative.group(1)) or int(relative.group(1))
        if relative.group(2) != "day":
            unit = "minutes" if relative.group(2) in {"minute", "min"} else "hours"
            return now + timedelta(**{unit: amount})
        day = now.date() + timedelta(days=int(amount))  # "In 3 days": the time is read below.
    elif has_date:
        if written is None:
            return None  # Something like "31/02": better no guess than a wrong one.
        day = written
    elif re.search(r"\b(today|tonight)\b", text):
        day = now.date()
    elif re.search(r"\btomorrow\b", text):
        day = now.date() + timedelta(days=1)
    else:
        for number, name in enumerate(WEEKDAYS):
            if re.search(rf"\b{name}\b", text):
                day = now.date() + timedelta(days=(number - now.weekday()) % 7)
                weekday = True
                break

    time = None
    at = _AT.search(text)
    if at and int(at.group(1)) <= 23:
        hour, minute, suffix = int(at.group(1)), int(at.group(2) or 0), (at.group(3) or "").replace(".", "")
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        elif not suffix and hour < 8 and re.search(r"\b(evening|tonight|afternoon)\b", text):
            hour += 12
        if minute < 60:
            time = (hour, minute)
    if time is None:
        if re.search(r"\b(tonight|evening)\b", text):
            time = (19, 0)
        elif re.search(r"\bafternoon\b", text):
            time = (14, 0)
        elif re.search(r"\bmorning\b", text):
            time = (8, 0)
        elif day is not None:
            time = (9, 0)

    if time is None:
        return None
    if day is None:
        day = now.date()
        if datetime.combine(day, datetime.min.time()).replace(hour=time[0], minute=time[1]) <= now:
            day += timedelta(days=1)
    due = datetime.combine(day, datetime.min.time()).replace(hour=time[0], minute=time[1])
    if weekday and due <= now:
        due += timedelta(days=7)
    return due if due > now else None


REPEATS = ["daily", "weekdays", "weekly", "monthly"]
REPEAT_LABELS = {"daily": "every day", "weekdays": "every weekday", "weekly": "every week",
                 "monthly": "every month"}
_EVERY = r"\b(?:every|each)\s+"


def guess_repeat(message):
    """'every day at 8' -> 'daily', 'every Monday' -> 'weekly', otherwise None."""
    text = message.lower()
    if re.search(_EVERY + r"weekday|\bon weekdays\b|\bmonday (?:to|through|-) friday\b", text):
        return "weekdays"
    if re.search(_EVERY + r"(?:day|morning|evening|night|afternoon)\b|\bdaily\b", text):
        return "daily"
    if re.search(_EVERY + r"(?:week|" + "|".join(WEEKDAYS) + r")\b|\bweekly\b", text):
        return "weekly"
    if re.search(_EVERY + r"month\b|\bmonthly\b", text):
        return "monthly"
    return None


def first_due(message, repeat, now=None):
    """When a repeating reminder starts: the time in the message, or 9:00 next time."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    due = guess_due(message, now)
    if due is None:
        due = now.replace(hour=9, minute=0)
        if due <= now:
            due += timedelta(days=1)
    if repeat == "weekdays":
        while due.weekday() >= 5:
            due += timedelta(days=1)
    return due


def next_due(due, repeat, now=None):
    """The next time a repeating reminder is due, after 'now'."""
    now = now or datetime.now()
    while due <= now:
        if repeat == "daily":
            due += timedelta(days=1)
        elif repeat == "weekdays":
            due += timedelta(days=3 if due.weekday() == 4 else 2 if due.weekday() == 5 else 1)
        elif repeat == "weekly":
            due += timedelta(days=7)
        elif repeat == "monthly":
            month = due.month % 12 + 1
            year = due.year + (due.month == 12)
            day = min(due.day, calendar.monthrange(year, month)[1])
            due = due.replace(year=year, month=month, day=day)
        else:
            raise ValueError(f"Unknown repeat: {repeat}")
    return due


def describe_due(due_text):
    """'2026-10-10 18:00' -> 'Sat 10 Oct, 18:00'"""
    return datetime.strptime(due_text, DUE_FORMAT).strftime("%a %d %b, %H:%M")


# Shows a Windows toast notification. The title and text are passed through
# environment variables, so nothing in them can be run as a command.
_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName("text")
$texts.Item(0).AppendChild($xml.CreateTextNode($env:MAJA_TOAST_TITLE)) > $null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:MAJA_TOAST_TEXT)) > $null
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def notify(title, text):
    """Show a Windows notification. Does nothing on other systems or if it fails."""
    if sys.platform != "win32":
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "MAJA_TOAST_TITLE": title, "MAJA_TOAST_TEXT": text},
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).communicate(_TOAST_SCRIPT.encode("utf-8"), timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
