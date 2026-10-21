"""Matty's calendar, read from a private ICS link.

Outlook, Google Calendar and TimeEdit can all share a calendar as an ICS link (a secret
address that only allows reading). Maja downloads it at most every 30 minutes, keeps a
copy so it also works offline, and never changes the calendar. Repeating events, like a
weekly lecture, are expanded into the individual dates.
"""

import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import icalendar
    import recurring_ical_events
    AGENDA_AVAILABLE = True
except ImportError:
    AGENDA_AVAILABLE = False

REFRESH_SECONDS = 30 * 60


class AgendaError(Exception):
    """A friendly, user-facing explanation of a calendar problem."""


def local_time(value):
    """Calendar times come with or without a time zone, or as whole days; make them all
    plain local times. Returns (datetime, all_day)."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value, False
    return datetime.combine(value, datetime.min.time()), True


class Agenda:
    def __init__(self, settings, cache_file):
        """settings is Maja: the link is stored with her other settings."""
        if not AGENDA_AVAILABLE:
            raise AgendaError("Reading a calendar needs extra packages. In a terminal, run: "
                              "py -m pip install -r requirements.txt, then restart me.")
        self.settings = settings
        self.cache_file = Path(cache_file)
        self._calendar = None
        self._loaded_at = 0.0

    @property
    def url(self):
        return self.settings.setting("calendar_url")

    def connect(self, url):
        """Remember the link and check that it works."""
        url = url.strip().strip("<>\"'")
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if not (url.lower().startswith("https://") or Path(url).is_file()):
            raise AgendaError("That doesn't look like a calendar link. It should start with "
                              "https:// or webcal:// and usually ends with .ics.")
        previous = self.url
        self.settings.change_setting("calendar_url", url)
        self._calendar = None
        try:
            self._load(force=True)
        except AgendaError:
            self.settings.change_setting("calendar_url", previous)
            raise

    def disconnect(self):
        self.settings.change_setting("calendar_url", None)
        self._calendar = None
        self.cache_file.unlink(missing_ok=True)

    def _download(self):
        if Path(self.url).is_file():
            return Path(self.url).read_bytes()
        request = urllib.request.Request(self.url, headers={"User-Agent": "Maja calendar reader"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()

    def _load(self, force=False):
        if not self.url:
            raise AgendaError("No calendar is connected yet. Type 'calendar' and your calendar's "
                              "ICS link (see the README for how to get it).")
        fresh = time.monotonic() - self._loaded_at < REFRESH_SECONDS
        if self._calendar is not None and fresh and not force:
            return self._calendar
        try:
            data = self._download()
            calendar = icalendar.Calendar.from_ical(data)
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_bytes(data)
        except Exception as error:  # Network errors, a wrong link or a broken file.
            if force or not self.cache_file.exists():
                raise AgendaError("I couldn't read that calendar. Check the link and the internet "
                                  "connection.") from error
            calendar = icalendar.Calendar.from_ical(self.cache_file.read_bytes())  # Offline copy.
        self._calendar, self._loaded_at = calendar, time.monotonic()
        return calendar

    def events(self, start, end):
        """Events between two local datetimes, sorted by start time."""
        found = []
        for event in recurring_ical_events.of(self._load()).between(start, end):
            begins, all_day = local_time(event.get("DTSTART").dt)
            ends = event.get("DTEND")
            finishes = local_time(ends.dt)[0] if ends else begins
            found.append({
                "start": begins,
                "end": finishes,
                "all_day": all_day,
                "title": str(event.get("SUMMARY", "(no title)")).strip(),
                "location": str(event.get("LOCATION", "")).strip(),
            })
        return sorted(found, key=lambda event: event["start"])

    def day(self, day):
        start = datetime.combine(day, datetime.min.time())
        return self.events(start, start + timedelta(days=1))


def describe_event(event, with_day=False):
    """'10:00-12:00 HPC lecture (HA2)', or 'Fri 23 Oct, all day: Exam week'."""
    when = "" if not with_day else f"{event['start']:%a %d %b}, "
    when += "all day" if event["all_day"] else f"{event['start']:%H:%M}-{event['end']:%H:%M}"
    place = f" ({event['location']})" if event["location"] else ""
    return f"{when}{':' if event['all_day'] else ''} {event['title']}{place}"
