"""Due dates for tasks and Windows notifications for reminders."""

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

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


def guess_due(message, now=None):
    """Find a reminder time in plain English, like 'in 2 hours', 'at 7am', 'tomorrow',
    or 'Friday at 18:30'. Returns a datetime in the future, or None.

    This backs up the AI model, which is unreliable at this kind of date math.
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    text = message.lower()

    day = None
    relative = _IN.search(text)
    if relative:
        amount = _NUMBER_WORDS.get(relative.group(1)) or int(relative.group(1))
        if relative.group(2) != "day":
            unit = "minutes" if relative.group(2) in {"minute", "min"} else "hours"
            return now + timedelta(**{unit: amount})
        day = now.date() + timedelta(days=int(amount))  # "In 3 days": the time is read below.
    elif re.search(r"\b(today|tonight)\b", text):
        day = now.date()
    elif re.search(r"\btomorrow\b", text):
        day = now.date() + timedelta(days=1)
    else:
        for number, name in enumerate(WEEKDAYS):
            if re.search(rf"\b{name}\b", text):
                day = now.date() + timedelta(days=(number - now.weekday() - 1) % 7 + 1)
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
        elif day is not None:
            time = (9, 0)

    if time is None:
        return None
    if day is None:
        day = now.date()
        if datetime.combine(day, datetime.min.time()).replace(hour=time[0], minute=time[1]) <= now:
            day += timedelta(days=1)
    due = datetime.combine(day, datetime.min.time()).replace(hour=time[0], minute=time[1])
    return due if due > now else None


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
