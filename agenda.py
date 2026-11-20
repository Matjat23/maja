"""Matty's calendars, read from their private ICS links.

Outlook, Google Calendar and TimeEdit can all share a calendar as an ICS link (a secret
address that only allows reading). Several calendars can be connected; their events are
shown together, each with its calendar's name. Maja downloads each one at most every 30
minutes, keeps a copy so it also works offline, and never changes a calendar. Repeating
events, like a weekly lecture, are expanded into the individual dates.
"""

import hashlib
import re
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
LINK = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)


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


def clean_link(url):
    url = url.strip().strip("<>\"'")
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    return url


class Agenda:
    def __init__(self, settings, cache_folder, old_cache=None):
        """settings is Maja: the links are stored with her other settings. old_cache is
        where the single calendar used to keep its offline copy."""
        if not AGENDA_AVAILABLE:
            raise AgendaError("Reading a calendar needs extra packages. In a terminal, run: "
                              "py -m pip install -r requirements.txt, then restart me.")
        self.settings = settings
        self.cache_folder = Path(cache_folder)
        self._loaded = {}  # url -> (calendar, when it was downloaded)
        self.unreadable = []  # Names of the calendars the last events() call couldn't read.
        self._migrate(old_cache)

    def _migrate(self, old_cache):
        """Maja used to keep one link in 'calendar_url'; it becomes the first of the list,
        and its offline copy moves along so it still works without internet."""
        old = self.settings.setting("calendar_url")
        if old and not self.settings.setting("calendars"):
            self.settings.change_setting("calendars", [{"name": "My calendar", "url": old}])
            new_cache = self._cache_file(old)
            if old_cache and Path(old_cache).is_file() and not new_cache.exists():
                self.cache_folder.mkdir(parents=True, exist_ok=True)
                Path(old_cache).replace(new_cache)
        if old:
            self.settings.change_setting("calendar_url", None)

    @property
    def calendars(self):
        return list(self.settings.setting("calendars") or [])

    def is_connected_link(self, text):
        """True if the text contains the link of a connected calendar."""
        return any(c["url"] in text for c in self.calendars if LINK.match(c["url"]))

    @property
    def url(self):
        """The first calendar's link, or None (also: is any calendar connected?)."""
        return self.calendars[0]["url"] if self.calendars else None

    def connect(self, url):
        """Add a calendar (or refresh one that's already connected) and check that it works.
        Returns its name, taken from the calendar itself."""
        url = clean_link(url)
        if not (url.lower().startswith("https://") or Path(url).is_file()):
            raise AgendaError("That doesn't look like a calendar link. It should start with "
                              "https:// or webcal:// and usually ends with .ics.")
        self._loaded.pop(url, None)  # Connecting always reads the calendar again.
        calendar = self._download(url, use_copy=False)
        calendars = self.calendars
        position = next((i for i, c in enumerate(calendars) if c["url"] == url), len(calendars))
        name = " ".join(str(calendar.get("X-WR-CALNAME", "")).split())[:60]
        # The name is shown to the language model, so it must never carry a link.
        if not name or LINK.search(name):
            name = f"Calendar {position + 1}"
        if position < len(calendars):
            calendars[position] = {"name": name, "url": url}  # Keeps its place and number.
        else:
            calendars.append({"name": name, "url": url})
        self.settings.change_setting("calendars", calendars)
        return name

    def disconnect(self, number=None):
        """Remove calendar `number` (1-based), or all of them. Returns what was removed."""
        calendars = self.calendars
        if number is None:
            removed, calendars = calendars, []
        elif 1 <= number <= len(calendars):
            removed = [calendars.pop(number - 1)]
        else:
            return []
        for calendar in removed:
            self._cache_file(calendar["url"]).unlink(missing_ok=True)
            self._loaded.pop(calendar["url"], None)
        self.settings.change_setting("calendars", calendars)
        return removed

    def _cache_file(self, url):
        return self.cache_folder / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.ics"

    def _download(self, url, use_copy=True):
        """The calendar behind a link, downloaded at most every 30 minutes. With use_copy,
        the last downloaded copy is used when the link can't be reached."""
        loaded = self._loaded.get(url)
        if loaded and time.monotonic() - loaded[1] < REFRESH_SECONDS:
            return loaded[0]
        cache = self._cache_file(url)
        try:
            if Path(url).is_file():
                data = Path(url).read_bytes()
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "Maja calendar reader"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read()
            calendar = icalendar.Calendar.from_ical(data)
            self.cache_folder.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        except Exception as error:  # Network errors, a wrong link or a broken file.
            if not use_copy or not cache.exists():
                raise AgendaError("I couldn't read that calendar. Check the link and the internet "
                                  "connection.") from error
            try:
                calendar = icalendar.Calendar.from_ical(cache.read_bytes())  # Offline copy.
            except Exception as broken:
                raise AgendaError("I couldn't read that calendar, and my offline copy is "
                                  "broken.") from broken
        self._loaded[url] = (calendar, time.monotonic())
        return calendar

    def events(self, start, end):
        """Events from all calendars between two local datetimes, sorted by start time.
        A calendar that can't be read is skipped and its name is put in self.unreadable;
        if none can be read, AgendaError is raised."""
        self.unreadable = []
        if not self.calendars:
            raise AgendaError("No calendar is connected yet. Paste your calendar's private ICS "
                              "link here (see the README for how to get it).")
        found = []
        for entry in self.calendars:
            try:
                found += self._events_of(entry, start, end)
            except Exception:  # A broken calendar must not hide the others.
                self.unreadable.append(entry["name"])
        if len(self.unreadable) == len(self.calendars):
            raise AgendaError("I couldn't read your calendars right now. Check the internet connection.")
        return sorted(found, key=lambda event: event["start"])

    def _events_of(self, entry, start, end):
        found = []
        calendar = self._download(entry["url"])
        for event in recurring_ical_events.of(calendar).between(start, end):
            begins, all_day = local_time(event.get("DTSTART").dt)
            ends = event.get("DTEND")
            finishes = local_time(ends.dt)[0] if ends else begins
            found.append({
                "start": begins,
                "end": finishes,
                "all_day": all_day,
                "title": str(event.get("SUMMARY", "(no title)")).strip(),
                "location": str(event.get("LOCATION", "")).strip(),
                "calendar": entry["name"],
            })
        return found

    def unreadable_note(self):
        """'(I couldn't read Work right now.)' after an events() call that skipped some."""
        if not self.unreadable:
            return ""
        return f"(I couldn't read {' and '.join(self.unreadable)} right now.)"

    def day(self, day):
        start = datetime.combine(day, datetime.min.time())
        return self.events(start, start + timedelta(days=1))


def describe_event(event, with_day=False, show_calendar=False, today=None):
    """'10:00-12:00 HPC lecture (HA2)', 'Fri 23 Oct, all day: Exam week [Work]', or for an
    event that started earlier and is still going on, 'ongoing until Fri 04 Dec: ...'."""
    today = today or date.today()
    if event["all_day"] and event["start"].date() < today and event["end"].date() > today:
        last_day = event["end"] - timedelta(days=1)  # An all-day end date is the day after.
        when = f"ongoing until {last_day:%a %d %b}:"
    else:
        when = "" if not with_day else f"{event['start']:%a %d %b}, "
        when += "all day:" if event["all_day"] else f"{event['start']:%H:%M}-{event['end']:%H:%M}"
    place = f" ({event['location']})" if event["location"] else ""
    calendar = f" [{event['calendar']}]" if show_calendar and event.get("calendar") else ""
    return f"{when} {event['title']}{place}{calendar}"
