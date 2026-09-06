import re
from datetime import datetime, timedelta, timezone, date as date_cls
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from database import (
    init_db,
    init_chat_table,

    create_user,
    get_user_by_email,
    get_user_by_id,

    add_task,
    get_tasks,
    get_task,
    update_task_status,
    postpone_task,
    update_task_schedule,
    update_task_duration,

    save_chat,
    get_chat_history,

    add_goal,
    get_goals,
    update_goal_progress,

    add_routine,
    get_routines,
    delete_routine,

    get_notifications,
    mark_notification_read,

    calculate_productivity,
    get_task_statistics,
    get_productivity_history,
    get_activity_history,
    get_productivity_dna
)

from scheduler import (
    generate_schedule,
    reschedule_task
)

from motivation import get_motivation
from ai_engine import understand_task
from groq_engine import understand_message, parse_hhmm


# =========================================================
# APP
# =========================================================
# =========================================================
# TIMETRACE DATE + TIME UNDERSTANDING
# =========================================================

def save_pending_task(task):
    session["pending_task"] = task
    session.modified = True


def get_pending_task():
    return session.get("pending_task")


def clear_pending_task():
    session.pop("pending_task", None)
    session.modified = True


# Same pattern as pending_task, but for "reschedule X" when no time was
# given yet - keeps track of which existing task is being rescheduled
# while we wait for the person to say what time.
def save_pending_reschedule(task_id, title, requested_date=None):
    session["pending_reschedule"] = {
        "task_id": task_id,
        "title": title,
        "requested_date": requested_date,
    }
    session.modified = True


def get_pending_reschedule():
    return session.get("pending_reschedule")


def clear_pending_reschedule():
    session.pop("pending_reschedule", None)
    session.modified = True


def find_task_by_message(user_id, message):
    """Best-effort match of an existing task mentioned in a message, by
    title substring. Prefers the longest matching title so a short title
    like "Gym" doesn't false-match a sentence about something else that
    merely contains the word "gym" in passing."""
    text = str(message).lower()
    tasks = get_tasks(user_id) or []
    best = None
    best_len = 0
    for t in tasks:
        if t["status"] == "completed":
            continue
        title = str(t["title"]).strip().lower()
        if title and title in text and len(title) > best_len:
            best = t
            best_len = len(title)
    return best


# India local time. The application is being used in IST.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


def extract_requested_date(message):
    text = str(message).lower()

    if re.search(r"\btomorrow\b", text):
        return "tomorrow"

    if re.search(r"\btoday\b", text):
        return "today"

    if re.search(r"\btonight\b", text):
        return "today"

    return None


def requested_date_value(label):
    today = now_ist().date()

    if isinstance(label, date_cls):
        return label

    if label == "tomorrow":
        return today + timedelta(days=1)

    if label == "today" or not label:
        return today

    # A specific date given as "YYYY-MM-DD" - e.g. from Groq when the
    # user names an exact date like "August 20 2026". Previously any
    # value other than the literal strings "tomorrow"/"today" silently
    # fell through to today, which is why a named date was ignored.
    try:
        return datetime.strptime(str(label), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return today


def parse_single_time(hour_text, minute_text=None, ampm=None):
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
        ap = (ampm or "").lower()

        if minute < 0 or minute > 59:
            return None

        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        elif not ap:
            # 24-hour notation is accepted. For ordinary values,
            # leave them as-is and let range parsing infer AM/PM.
            if hour > 23:
                return None

        if 0 <= hour <= 23:
            return hour, minute
    except (TypeError, ValueError):
        pass

    return None



def normalize_spoken_time_text(message):
    """Normalize common speech-recognition forms before parsing times."""
    text = str(message or "").lower()

    replacements = {
        r"\ba\.m\.\b": "am",
        r"\bp\.m\.\b": "pm",
        r"\ba\.m\b": "am",
        r"\bp\.m\b": "pm",
        r"\ba m\b": "am",
        r"\bp m\b": "pm",
        r"\bo'clock\b": "oclock",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.I)

    # Speech recognition may return the hour as a word.
    number_words = {
        "zero": "0", "one": "1", "two": "2", "three": "3",
        "four": "4", "five": "5", "six": "6", "seven": "7",
        "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
        "twelve": "12", "thirteen": "13", "fourteen": "14",
        "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19", "twenty": "20",
        "twenty one": "21", "twenty two": "22", "twenty three": "23",
    }
    for word, number in sorted(number_words.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"\b{re.escape(word)}\b", number, text)

    # "seven thirty pm" -> "7:30 pm"
    text = re.sub(
        r"\b(\d{1,2})\s+(\d{2})\s*(am|pm)\b",
        r"\1:\2 \3",
        text,
        flags=re.I,
    )
    return text


def extract_time_range(message):
    """
    Understand explicit ranges:
      8 to 12
      8 AM to 12 PM
      from 8 to 12
      8:30 PM - 10:30 PM
      from seven pm to nine pm
    """
    text = normalize_spoken_time_text(message)
    text = text.replace("–", "-").replace("—", "-")

    pattern = re.compile(
        r"\b(?:from\s+)?"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*"
        r"(?:to|-|until|till)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        re.I,
    )

    match = pattern.search(text)
    if not match:
        return None

    h1, m1, ap1, h2, m2, ap2 = match.groups()
    ap1 = ap1.lower() if ap1 else None
    ap2 = ap2.lower() if ap2 else None

    # If AM/PM is given only once, apply it to both ends.
    if ap1 and not ap2:
        ap2 = ap1
    elif ap2 and not ap1:
        ap1 = ap2

    # Natural interpretation for "8 to 12", "8 to 10", etc.
    if not ap1 and not ap2:
        sh = int(h1)
        eh = int(h2)

        if sh < 7 and eh <= 12:
            ap1 = "pm"
        elif 7 <= sh < 12:
            ap1 = "am"
        elif sh == 12:
            ap1 = "pm"
        else:
            ap1 = None

        if eh == 12:
            ap2 = "pm"
        elif sh < 12 and eh < 12:
            ap2 = "am"
        elif sh >= 12 and eh < 12:
            ap2 = "pm"
        else:
            ap2 = None

    start = parse_single_time(h1, m1, ap1)
    end = parse_single_time(h2, m2, ap2)
    if not start or not end:
        return None

    # If both were 24-hour values, keep them as-is.
    if not ap1 and not ap2:
        return start, end

    # If an explicit/inferred daytime range wraps past midnight, normalize it.
    sh, sm = start
    eh, em = end
    if end <= start:
        end = (eh + 24, em) if eh < sh else end
        if end[0] >= 24:
            # datetime builder handles overnight only; convert to next day
            # by returning the same clock time and letting build_task_datetime
            # detect end <= start.
            end = (eh, em)

    return start, end


# Vague time-of-day words ("evening", "morning") were previously ignored
# entirely by extract_requested_time - it only recognized exact clock
# times like "7 PM", so saying "tomorrow evening" returned None, which
# either forced an unnecessary "what exact time?" question or (in the
# reschedule/follow-up path) silently defaulted to a fixed early hour.
# These are reasonable representative hours, not a demand for precision
# the person didn't give.
TIME_OF_DAY_HOURS = {
    "early morning": (6, 0),
    "morning": (9, 0),
    "noon": (12, 0),
    "midday": (12, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "tonight": (21, 0),
    "night": (21, 0),
    "late night": (23, 0),
}


def extract_time_of_day_word(message):
    """Return an (hour, minute) guess for a vague time-of-day word, or None."""
    text = str(message).lower()
    for phrase in sorted(TIME_OF_DAY_HOURS, key=len, reverse=True):
        if re.search(r"\b" + phrase + r"\b", text):
            return TIME_OF_DAY_HOURS[phrase]
    return None


def extract_requested_time(message):
    """Return the explicitly requested start time, including voice forms."""
    text = normalize_spoken_time_text(message)

    time_range = extract_time_range(text)
    if time_range:
        return time_range[0]

    patterns = [
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        r"\b(\d{1,2}):([0-5]\d)\b",
        r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        r"\b(\d{1,2})\s*oclock\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue

        groups = match.groups()
        parsed = parse_single_time(
            groups[0],
            groups[1] if len(groups) > 1 else None,
            groups[2] if len(groups) > 2 else None,
        )
        if parsed:
            return parsed

    # No exact clock time given - fall back to a vague time-of-day word
    # if one was said, instead of returning None.
    return extract_time_of_day_word(text)


# Spelled-out numbers ("two hours") never matched the digit-only regex
# below, so anything not written as a numeral silently returned None and
# fell back to a 30-minute default elsewhere in this file. This is the
# actual bug behind durations being wrong - ai_engine.py has its own
# extract_duration() too, but this one (not that one) is what real
# requests go through.
_DURATION_WORD_NUMBERS = {
    "half": "0.5",
    "a": "1", "an": "1", "one": "1",
    "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}


def _normalize_duration_words(text):
    for word, digit in _DURATION_WORD_NUMBERS.items():
        text = re.sub(r"\b" + word + r"\b", digit, text)
    return text


def extract_duration(message):
    """Return duration in minutes when the user explicitly gives it."""
    text = str(message).lower()

    # "half an hour" / "half hour" as a direct special case, before word
    # normalization - otherwise "an" becomes "1" and overrides "half",
    # turning it into 60 minutes instead of 30.
    if re.search(r"half\s+(an\s+)?hour", text):
        return 30

    text = _normalize_duration_words(text)

    hours_match = re.search(
        r"\b(?:for\s*)?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b",
        text
    )
    if hours_match:
        minutes = int(float(hours_match.group(1)) * 60)
        return max(1, min(minutes, 720))

    minutes_match = re.search(
        r"\b(?:for\s*)?(\d+)\s*(?:minutes?|mins?)\b",
        text
    )
    if minutes_match:
        minutes = int(minutes_match.group(1))
        return max(1, min(minutes, 720))

    return None


def get_requested_datetime(message):
    requested_date = extract_requested_date(message)
    requested_time = extract_requested_time(message)
    return requested_date, requested_time


def format_schedule_datetime(start, end):
    try:
        start_dt = datetime.fromisoformat(str(start))
        date_text = start_dt.strftime("%b %d, %Y")
        start_text = start_dt.strftime("%I:%M %p").lstrip("0")

        if end:
            end_dt = datetime.fromisoformat(str(end))
            end_text = end_dt.strftime("%I:%M %p").lstrip("0")
        else:
            end_text = ""

        return date_text, start_text, end_text
    except Exception:
        return "", "", ""


def clean_task_title(message):
    """Create a deterministic title so the AI cannot turn Shopping into DBMS."""
    text = normalize_spoken_time_text(str(message).strip())
    lower = text.lower()

    # Remove conversational prefixes.
    prefixes = [
        r"^please\s+",
        r"^can you\s+",
        r"^could you\s+",
        r"^i want to\s+",
        r"^i have to\s+",
        r"^i need to\s+",
        r"^i'm going to\s+",
        r"^im going to\s+",
        r"^i am going to\s+",
        r"^remind me to\s+",
        r"^add\s+",
        r"^schedule\s+",
        r"^put\s+",
        r"^set\s+"
    ]

    for prefix in prefixes:
        text = re.sub(prefix, "", text, flags=re.I).strip()

    # Remove scheduling/time information.
    text = re.sub(r"\b(?:today|tomorrow|tonight)\b", "", text, flags=re.I)

    # Vague time-of-day words ("evening", "morning", etc.) were being left
    # in the title (e.g. "Study dbms evening") since only exact clock
    # times were stripped before. Strip these too.
    text = re.sub(
        r"\b(?:early\s+morning|late\s+night|morning|noon|midday|"
        r"afternoon|evening|night)\b",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-|until|till)\s*"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
        "",
        text,
        flags=re.I
    )
    text = re.sub(
        r"\b(?:at|around|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
        "",
        text,
        flags=re.I
    )
    text = re.sub(
        r"\b(?:for\s*)?\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)\b",
        "",
        text,
        flags=re.I
    )

    text = re.sub(r"\s+", " ", text).strip(" .,!?-")

    # The bug that mattered most: after removing a date/time phrase, the
    # connector word that introduced it was left dangling at the end -
    # "Remove DBMS from tomorrow" became "Remove DBMS from" once
    # "tomorrow" was stripped, because "from" itself was never touched.
    # Strip any of these trailing connector words, repeatedly (in case
    # more than one is stacked, e.g. "... due by" -> "..." ).
    while True:
        stripped = re.sub(
            r"\s*\b(?:from|for|by|on|at|in|before|until|till|to)\s*$",
            "",
            text,
            flags=re.I
        )
        if stripped == text:
            break
        text = stripped.strip(" .,!?-")

    # Strong deterministic cases.
    if re.search(r"\bshopping\b", lower):
        return "Shopping"

    if re.search(r"\b(?:workout|exercise|gym)\b", lower):
        return "Workout"

    if re.search(r"\b(?:groceries|grocery shopping)\b", lower):
        return "Grocery Shopping"

    if not text:
        return "Task"

    return text[0].upper() + text[1:]


def task_date_text(label):
    date_value = requested_date_value(label)
    return date_value.strftime("%B %d, %Y")


def build_task_datetime(date_label, start_tuple, end_tuple=None, duration=None):
    date_value = requested_date_value(date_label)
    start_hour, start_minute = start_tuple
    start_dt = datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        start_hour,
        start_minute
    )

    if end_tuple:
        end_hour, end_minute = end_tuple
        end_dt = datetime(
            date_value.year,
            date_value.month,
            date_value.day,
            end_hour,
            end_minute
        )
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
    else:
        end_dt = start_dt + timedelta(minutes=int(duration or 30))

    return start_dt, end_dt


def compute_free_periods(user_id, date_label="today", day_start_hour=6, day_end_hour=23, step_minutes=15):
    """Scan a day in fixed steps and report contiguous free stretches,
    reusing the same slot_is_free() check used when actually booking a
    task - so "when am I free" always agrees with what the app will
    actually let you schedule."""
    date_value = requested_date_value(date_label)
    day_start = datetime(date_value.year, date_value.month, date_value.day, day_start_hour, 0)
    day_end = datetime(date_value.year, date_value.month, date_value.day, day_end_hour, 0)

    step = timedelta(minutes=step_minutes)
    free_periods = []
    period_start = None
    cursor = day_start

    while cursor < day_end:
        next_cursor = min(cursor + step, day_end)
        if slot_is_free(user_id, cursor, next_cursor):
            if period_start is None:
                period_start = cursor
        else:
            if period_start is not None:
                free_periods.append((period_start, cursor))
                period_start = None
        cursor = next_cursor

    if period_start is not None:
        free_periods.append((period_start, day_end))

    # Drop slivers shorter than the step itself - not practically useful.
    free_periods = [(s, e) for s, e in free_periods if (e - s) >= step]
    return free_periods


def parse_db_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def slot_is_free(user_id, start_dt, end_dt, ignore_task_id=None):
    """Check existing scheduled tasks and routines before placing a task."""
    tasks = get_tasks(user_id) or []

    for existing in tasks:
        if ignore_task_id is not None and existing["id"] == ignore_task_id:
            continue
        if existing["status"] == "completed":
            continue

        existing_start = parse_db_datetime(existing["scheduled_start"])
        existing_end = parse_db_datetime(existing["scheduled_end"])

        if existing_start and existing_end:
            if start_dt < existing_end and end_dt > existing_start:
                return False

    # Routines are fixed busy periods. Their dates are interpreted for the
    # requested day, including Everyday and simple day-name routines.
    try:
        routines = get_routines(user_id) or []
    except Exception:
        routines = []

    weekday = start_dt.strftime("%A").lower()

    for routine in routines:
        try:
            days = str(routine["days"]).lower()
            applies = (
                "everyday" in days
                or "daily" in days
                or weekday in days
            )
            if not applies:
                continue

            r_start = str(routine["start_time"])
            r_end = str(routine["end_time"])

            sh, sm = [int(x) for x in r_start[:5].split(":")]
            eh, em = [int(x) for x in r_end[:5].split(":")]

            routine_start = datetime(
                start_dt.year, start_dt.month, start_dt.day, sh, sm
            )
            routine_end = datetime(
                start_dt.year, start_dt.month, start_dt.day, eh, em
            )
            if routine_end <= routine_start:
                routine_end += timedelta(days=1)

            if start_dt < routine_end and end_dt > routine_start:
                return False
        except Exception:
            continue

    return True


def find_free_slot(
    user_id,
    date_value,
    duration,
    preferred_start_hour=6,
    ignore_task_id=None,
):
    """Find a free slot in the user's day instead of rejecting the task."""
    duration = max(1, int(duration))
    day_start = datetime(
        date_value.year, date_value.month, date_value.day,
        max(0, preferred_start_hour), 0
    )
    # Midnight of the following day is the exclusive end boundary.  This
    # permits a task ending exactly at midnight and avoids losing the final
    # minute of every day.
    day_end = day_start + timedelta(days=1)

    cursor = day_start
    while cursor + timedelta(minutes=duration) <= day_end:
        candidate_end = cursor + timedelta(minutes=duration)
        if slot_is_free(user_id, cursor, candidate_end, ignore_task_id):
            return cursor, candidate_end
        cursor += timedelta(minutes=15)

    return None, None


def reschedule_missed_task_using_free_time(task_id, user_id):
    """Move a missed task into the next real free period."""
    task = get_task(task_id, user_id)
    if not task:
        return None

    duration = int(task["duration"] or 30)
    current = now_ist().replace(tzinfo=None, second=0, microsecond=0)

    # Search the rest of today first, then the following 29 days.  The old
    # seven-day window made a valid future slot look like a scheduling error.
    for day_offset in range(0, 30):
        date_value = (current + timedelta(days=day_offset)).date()

        if day_offset == 0:
            start_hour = current.hour
            start_minute = current.minute
            cursor = datetime(
                date_value.year, date_value.month, date_value.day,
                start_hour, start_minute
            )
            # Round up to the next 15-minute boundary.
            remainder = cursor.minute % 15
            if remainder:
                cursor += timedelta(minutes=(15 - remainder))

            end_of_day = datetime(
                date_value.year, date_value.month, date_value.day, 23, 59
            )

            while cursor + timedelta(minutes=duration) <= end_of_day:
                candidate_end = cursor + timedelta(minutes=duration)
                if slot_is_free(user_id, cursor, candidate_end, ignore_task_id=task_id):
                    update_task_status(task_id, "pending", user_id)
                    update_task_schedule(
                        task_id,
                        cursor.strftime("%Y-%m-%d %H:%M:%S"),
                        candidate_end.strftime("%Y-%m-%d %H:%M:%S")
                    )
                    return cursor, candidate_end
                cursor += timedelta(minutes=15)
        else:
            free_start, free_end = find_free_slot(
                user_id,
                date_value,
                duration,
                preferred_start_hour=6,
                # The task being moved must not reserve its old slot while
                # we look for its new one.
                ignore_task_id=task_id,
            )
            if free_start:
                update_task_status(task_id, "pending", user_id)
                update_task_schedule(
                    task_id,
                    free_start.strftime("%Y-%m-%d %H:%M:%S"),
                    free_end.strftime("%Y-%m-%d %H:%M:%S")
                )
                return free_start, free_end

    return None


def schedule_task_exact(task_id, user_id, start_dt, end_dt):
    """Write the exact chosen slot directly to the existing tasks table.

    end_dt may be None - the user gave only a start time, with no
    duration or end time at all. The task is still checked against the
    schedule using a small 1-minute window so it can't be silently
    placed exactly on top of another task's start, but only the start
    time itself is stored - no end time is invented."""
    check_end = end_dt if end_dt is not None else start_dt + timedelta(minutes=1)

    if not slot_is_free(user_id, start_dt, check_end, ignore_task_id=task_id):
        return False

    update_task_schedule(
        task_id,
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S") if end_dt is not None else None
    )
    return True


def create_task_from_details(user_id, details):
    """Create the DB task and schedule it in the exact requested/free slot.

    duration/end_time are only applied if actually given - if the user
    said only a start time, the task is stored with just that start
    time and no end time at all, instead of a silently-invented 30
    minutes."""
    title = details["title"]
    category = details.get("category", "General") or "General"
    raw_duration = details.get("duration")
    duration = int(raw_duration) if raw_duration else None
    priority = details.get("priority", "Medium") or "Medium"
    deadline = details.get("deadline", "") or ""
    date_label = details.get("date_label") or "today"
    start_tuple = details.get("start_time")
    end_tuple = details.get("end_time")

    task_id = add_task(
        title,
        category,
        duration,
        priority,
        deadline,
        user_id
    )

    if not task_id:
        raise ValueError("Task could not be inserted into database.")

    if start_tuple:
        start_dt, end_dt = build_task_datetime(
            date_label,
            start_tuple,
            end_tuple,
            duration
        )
        if end_tuple is None and not duration:
            end_dt = None

        # If the user gave an explicit end time (a range), the real
        # duration is now known from that range - even though it wasn't
        # stated as a number of minutes. Write it back to the duration
        # column so anywhere that displays task['duration'] directly
        # (task lists, planner) shows the correct number instead of the
        # "None minutes" that showed up before this fix.
        if end_dt is not None:
            actual_minutes = int((end_dt - start_dt).total_seconds() / 60)
            if actual_minutes != duration:
                try:
                    update_task_duration(task_id, actual_minutes)
                except Exception:
                    pass

        # If the user explicitly gave a time, respect it exactly.
        # Do NOT silently move it to another time. The user should know
        # when the requested slot is unavailable.
        if not schedule_task_exact(task_id, user_id, start_dt, end_dt):
            # Remove the just-created unscheduled task so a failed exact
            # request does not leave a ghost task in the planner.
            try:
                update_task_status(task_id, "cancelled", user_id)
            except Exception:
                pass
            raise ValueError(
                "That exact time overlaps your existing schedule. "
                "Please choose another free time."
            )
    else:
        # This path is normally not reached because AI chat asks for time
        # before creating the task. It is kept as a safe fallback.
        free_start, free_end = find_free_slot(
            user_id,
            requested_date_value(date_label),
            duration
        )
        if not free_start:
            raise ValueError("No free time is available on the requested date.")
        schedule_task_exact(task_id, user_id, free_start, free_end)

    return task_id


app = Flask(__name__)

app.secret_key = "timetrace_secret_key_2026"


# =========================================================
# DATABASE
# =========================================================

init_db()
init_chat_table()


# =========================================================
# LOGIN HELPERS
# =========================================================

def login_required():
    return "user_id" in session


def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    return get_user_by_id(user_id)


@app.context_processor
def inject_user():

    user = get_current_user()

    if user:

        return {
            "current_user": user,
            "current_user_name": user["name"],
            "current_user_email": user["email"]
        }

    return {
        "current_user": None,
        "current_user_name": "",
        "current_user_email": ""
    }


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET":

        if login_required():
            return redirect(url_for("dashboard"))

        return render_template("login.html")

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if not email or not password:

        return render_template(
            "login.html",
            error="Please enter your email and password."
        )

    user = get_user_by_email(email)

    if not user:

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    if not check_password_hash(
        user["password_hash"],
        password
    ):

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    session.clear()

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]

    return redirect(url_for("dashboard"))


# =========================================================
# SIGNUP
# =========================================================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "GET":

        if login_required():
            return redirect(url_for("dashboard"))

        return render_template("signup.html")

    name = request.form.get(
        "name",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    confirm_password = request.form.get(
        "confirm_password",
        ""
    )

    if not name or not email or not password:

        return render_template(
            "signup.html",
            error="Please fill all required fields."
        )

    if password != confirm_password:

        return render_template(
            "signup.html",
            error="Passwords do not match."
        )

    if len(password) < 6:

        return render_template(
            "signup.html",
            error="Password must contain at least 6 characters."
        )

    password_hash = generate_password_hash(password)

    user_id = create_user(
        name,
        email,
        password_hash
    )

    if user_id is None:

        return render_template(
            "signup.html",
            error="An account with this email already exists."
        )

    session.clear()

    session["user_id"] = user_id
    session["user_name"] = name
    session["user_email"] = email

    return redirect(url_for("dashboard"))


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def dashboard():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    user = get_current_user()

    all_tasks = get_tasks(user_id) or []
    today = now_ist().date().isoformat()
    tasks = [
        task for task in all_tasks
        if task["scheduled_start"]
        and str(task["scheduled_start"])[:10] == today
    ]

    chats = get_chat_history(user_id) or []

    goals = get_goals(user_id) or []

    try:
        statistics = get_task_statistics(user_id) or {}
    except Exception:
        statistics = {}

    try:
        productivity = calculate_productivity(user_id)
    except Exception:
        productivity = 0

    try:
        dna = get_productivity_dna(user_id) or {}
    except Exception:
        dna = {}

    try:
        notifications = get_notifications(user_id) or []
    except Exception:
        notifications = []

    # sqlite3.Row objects aren't JSON-serializable, so a plain-dict copy
    # is built here for the popup-reminder JavaScript on the dashboard
    # page, which needs each task's id/title/time/status as real JSON.
    reminder_tasks = [
        {
            "id": t["id"],
            "title": t["title"],
            "scheduled_start": t["scheduled_start"],
            "status": t["status"],
        }
        for t in all_tasks[:20]
    ]

    return render_template(
        "dashboard.html",

        user=user,

        user_name=session.get(
            "user_name",
            "User"
        ),

        user_email=session.get(
            "user_email",
            ""
        ),

        tasks=tasks,

        reminder_tasks=reminder_tasks,

        chats=chats,

        goals=goals,

        statistics=statistics,

        productivity=productivity,

        dna=dna,

        notifications=notifications
    )


# =========================================================
# PLANNER
# =========================================================

@app.route("/planner")
def planner():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    selected_date = request.args.get("date", "").strip()
    if selected_date:
        try:
            selected_date_value = datetime.strptime(selected_date, "%Y-%m-%d").date()
        except ValueError:
            selected_date_value = now_ist().date()
            selected_date = selected_date_value.isoformat()
    else:
        selected_date_value = now_ist().date()
        selected_date = selected_date_value.isoformat()

    all_tasks = get_tasks(user_id) or []

    # Only tasks scheduled on the selected day - previously every task
    # regardless of date was shown together with no way to look at a
    # different day, which is why the planner looked like it only ever
    # showed "today" (whatever happened to be in the list).
    tasks = [
        t for t in all_tasks
        if t["scheduled_start"] and str(t["scheduled_start"])[:10] == selected_date
    ]

    prev_date = (selected_date_value - timedelta(days=1)).isoformat()
    next_date = (selected_date_value + timedelta(days=1)).isoformat()
    is_today = selected_date_value == now_ist().date()

    routines = get_routines(user_id) or []

    return render_template(
        "planner.html",

        tasks=tasks,

        routines=routines,

        selected_date=selected_date,
        selected_date_display=selected_date_value.strftime("%A, %B %d, %Y"),
        prev_date=prev_date,
        next_date=next_date,
        is_today=is_today,

        user_name=session.get(
            "user_name",
            "User"
        )
    )


# =========================================================
# TASKS PAGE
# =========================================================

@app.route("/tasks")
def tasks_page():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    tasks = get_tasks(user_id) or []

    return render_template(
        "tasks.html",

        tasks=tasks,

        user_name=session.get(
            "user_name",
            "User"
        )
    )


# =========================================================
# GOALS
# =========================================================

@app.route("/goals")
def goals_page():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    goals = get_goals(user_id) or []

    return render_template(
        "goals.html",

        goals=goals,

        user_name=session.get(
            "user_name",
            "User"
        )
    )


# =========================================================
# ANALYTICS
# =========================================================

@app.route("/analytics")
def analytics():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    try:
        statistics = get_task_statistics(user_id) or {}
    except Exception:
        statistics = {}

    try:
        productivity = calculate_productivity(user_id)
    except Exception:
        productivity = 0

    try:
        history = get_productivity_history(user_id) or []
    except Exception:
        history = []

    try:
        activity = get_activity_history(user_id) or []
    except Exception:
        activity = []

    try:
        dna = get_productivity_dna(user_id) or {}
    except Exception:
        dna = {}

    return render_template(
        "analytics.html",

        statistics=statistics,

        productivity=productivity,

        history=history,

        activity=activity,

        dna=dna,

        user_name=session.get(
            "user_name",
            "User"
        )
    )


# =========================================================
# PRODUCTIVITY DNA
# =========================================================

@app.route("/productivity-dna")
def productivity_dna():

    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    try:
        dna = get_productivity_dna(user_id) or {}
    except Exception as error:
        print("DNA ERROR:", error)
        dna = {}

    try:
        statistics = get_task_statistics(user_id) or {}
    except Exception:
        statistics = {}

    try:
        productivity = calculate_productivity(user_id)
    except Exception:
        productivity = 0

    try:
        history = get_productivity_history(user_id) or []
    except Exception:
        history = []

    return render_template(
        "productivity_dna.html",

        dna=dna,

        statistics=statistics,

        productivity=productivity,

        history=history,

        user_name=session.get(
            "user_name",
            "User"
        )
    )


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile")
def profile():

    if not login_required():
        return redirect(url_for("login"))

    user = get_current_user()

    return render_template(
        "profile.html",
        user=user
    )


# =========================================================
# ADD TASK
# =========================================================

@app.route("/add-task", methods=["POST"])
def create_task():

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    data = request.get_json(silent=True) or {}

    title = data.get(
        "title",
        ""
    ).strip()

    category = data.get(
        "category",
        "General"
    )

    try:
        duration = int(
            data.get(
                "duration",
                30
            )
        )
    except (TypeError, ValueError):
        duration = 30

    priority = data.get(
        "priority",
        "Medium"
    )

    deadline = data.get(
        "deadline",
        ""
    )

    if not title:

        return jsonify({
            "success": False,
            "message": "Task title required."
        })

    task_id = add_task(
        title,
        category,
        duration,
        priority,
        deadline,
        user_id
    )

    try:
        generate_schedule(
            user_id=user_id
        )
    except TypeError:
        try:
            generate_schedule()
        except Exception:
            pass
    except Exception:
        pass

    task = get_task(
        task_id,
        user_id
    )

    if task and task["scheduled_start"]:

        start = str(
            task["scheduled_start"]
        )[11:16]

        end = str(
            task["scheduled_end"]
        )[11:16]

        message = (
            f"✅ <b>{title}</b> has been added."
            f"<br><br>"
            f"📅 Scheduled for "
            f"<b>{start} - {end}</b>."
        )

    else:

        message = (
            f"✅ <b>{title}</b> has been added "
            f"to your Smart Planner."
        )

    save_chat(
        "bot",
        message,
        user_id
    )

    return jsonify({
        "success": True,
        "task_id": task_id,
        "message": message
    })


# =========================================================
# COMPLETE TASK
# =========================================================

@app.route(
    "/complete/<int:task_id>",
    methods=["POST"]
)
def complete(task_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    task = get_task(
        task_id,
        user_id
    )

    if not task:

        return jsonify({
            "success": False,
            "message": "Task not found."
        }), 404

    update_task_status(
        task_id,
        "completed",
        user_id
    )

    message = (
        "🎉 Great job! Task completed! "
        "Keep the momentum going."
    )

    spoken = f"Great job! {task['title']} is completed. Keep the momentum going."

    save_chat(
        "bot",
        message,
        user_id
    )

    return jsonify({
        "success": True,
        "message": message,
        "speak": spoken
    })


# =========================================================
# MISS TASK
# =========================================================

@app.route(
    "/miss/<int:task_id>",
    methods=["POST"]
)
def miss(task_id):

    if not login_required():
        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]
    task = get_task(task_id, user_id)

    if not task:
        return jsonify({
            "success": False,
            "message": "Task not found."
        }), 404

    # Mark it missed first, then search the user's actual free periods.
    update_task_status(task_id, "missed", user_id)

    try:
        result = reschedule_missed_task_using_free_time(task_id, user_id)
    except Exception as error:
        print("MISSED TASK RESCHEDULE ERROR:", repr(error))
        result = None

    task = get_task(task_id, user_id)

    try:
        motivation = get_motivation(task)
    except TypeError:
        motivation = get_motivation()
    except Exception:
        motivation = (
            "You can get back on track. Start with the next small step."
        )

    if result:
        start_dt, end_dt = result
        date_text = start_dt.strftime("%B %d, %Y")
        start_text = start_dt.strftime("%I:%M %p").lstrip("0")
        end_text = end_dt.strftime("%I:%M %p").lstrip("0")

        message = (
            f"🔄 I noticed you missed <b>{task['title']}</b>."
            f"<br><br>"
            f"I found your next free period and moved it to "
            f"<b>{date_text}</b>, <b>{start_text} - {end_text}</b>."
            f"<br><br>{motivation}"
        )
        spoken = (
            f"I noticed you missed {task['title']}. "
            f"I found your next free period and moved it to "
            f"{date_text}, from {start_text} to {end_text}. "
            f"{motivation}"
        )

        success = True
    else:
        message = (
            f"⚠️ I couldn't find a free period for <b>{task['title']}</b> "
            "in the next thirty days without overlapping your existing schedule."
            f"<br><br>{motivation}"
        )
        spoken = (
            f"I couldn't find a free period for {task['title']} in the next thirty days. "
            f"{motivation}"
        )
        success = False

    save_chat("bot", message, user_id)

    return jsonify({
        "success": success,
        "type": "reschedule",
        "message": message,
        "speak": spoken,
        "task_id": task_id,
        "rescheduled": bool(result)
    })


# =========================================================
# TASK STATUS (used by the dashboard's Done / Missed / Later buttons)
# =========================================================
# The dashboard template calls POST /task/<id>/status with a JSON body
# like {"status": "completed"} - this route did not exist before, which
# is why those buttons failed silently. It dispatches to the same logic
# already used by /miss and /postpone, so behavior stays consistent.

@app.route(
    "/task/<int:task_id>/status",
    methods=["POST"]
)
def task_status(task_id):

    if not login_required():
        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]
    task = get_task(task_id, user_id)

    if not task:
        return jsonify({
            "success": False,
            "message": "Task not found."
        }), 404

    data = request.get_json(silent=True) or {}
    status = data.get("status", "")

    if status == "completed":
        update_task_status(task_id, "completed", user_id)
        message = f"✅ Marked \"{task['title']}\" as completed. Nice work!"
        spoken = f"Marked {task['title']} as completed. Nice work!"
        save_chat("bot", message, user_id)
        return jsonify({
            "success": True,
            "message": message,
            "speak": spoken
        })

    if status == "missed":
        update_task_status(task_id, "missed", user_id)
        save_pending_reschedule(task_id, task["title"])
        message = f"Marked \"{task['title']}\" as missed. What future date and time should I move it to?"
        save_chat("bot", message, user_id)
        return jsonify({"success": True, "message": message, "speak": message, "needs_time": True})

        try:
            result = reschedule_missed_task_using_free_time(task_id, user_id)
        except Exception as error:
            print("STATUS->MISSED RESCHEDULE ERROR:", repr(error))
            result = None

        task = get_task(task_id, user_id)

        try:
            motivation = get_motivation(task)
        except TypeError:
            motivation = get_motivation()
        except Exception:
            motivation = "You can get back on track. Start with the next small step."

        if result:
            start_dt, end_dt = result
            date_text = start_dt.strftime("%B %d, %Y")
            start_text = start_dt.strftime("%I:%M %p").lstrip("0")
            end_text = end_dt.strftime("%I:%M %p").lstrip("0")

            message = (
                f"🔄 I noticed you missed <b>{task['title']}</b>. "
                f"I found your next free period and moved it to "
                f"<b>{date_text}</b>, <b>{start_text} - {end_text}</b>. "
                f"{motivation}"
            )
            spoken = (
                f"I noticed you missed {task['title']}. "
                f"I found your next free period and moved it to "
                f"{date_text}, from {start_text} to {end_text}. "
                f"{motivation}"
            )
            success = True
            scheduled_start = start_dt.isoformat()
            scheduled_end = end_dt.isoformat()
        else:
            message = (
                f"⚠️ I couldn't find a free period for <b>{task['title']}</b> "
                "in the next thirty days without overlapping your existing schedule. "
                f"{motivation}"
            )
            spoken = (
                f"I couldn't find a free period for {task['title']} in the next thirty days. "
                f"{motivation}"
            )
            success = False
            scheduled_start = None
            scheduled_end = None

        save_chat("bot", message, user_id)

        return jsonify({
            "success": success,
            "message": message,
            "speak": spoken,
            "scheduled_start": scheduled_start,
            "scheduled_end": scheduled_end
        })

    if status == "later":
        save_pending_reschedule(task_id, task["title"])
        message = f"What future date and time should I move \"{task['title']}\" to?"
        save_chat("bot", message, user_id)
        return jsonify({"success": True, "message": message, "speak": message, "needs_time": True})

        postpone_task(task_id, user_id)

        try:
            generate_schedule(user_id=user_id)
        except TypeError:
            try:
                generate_schedule()
            except Exception:
                pass
        except Exception:
            pass

        task = get_task(task_id, user_id)
        message = f"⏰ I'll move \"{task['title']}\" to the next suitable free time."
        spoken = f"I'll move {task['title']} to the next suitable free time."
        save_chat("bot", message, user_id)

        scheduled_start = task["scheduled_start"] if task and "scheduled_start" in task.keys() else None
        scheduled_end = task["scheduled_end"] if task and "scheduled_end" in task.keys() else None

        return jsonify({
            "success": True,
            "message": message,
            "speak": spoken,
            "scheduled_start": scheduled_start,
            "scheduled_end": scheduled_end
        })

    return jsonify({
        "success": False,
        "message": f"Unknown status \"{status}\"."
    }), 400


# =========================================================
# RESCHEDULE (used by the "↻ Reschedule" button on the Tasks page)
# =========================================================
# tasks.html calls POST /reschedule/<id> - this route also did not exist
# before. It reuses the exact same free-time search the /miss route uses,
# so a task rescheduled from either screen lands in a real open slot,
# not just an arbitrary time.

@app.route(
    "/reschedule/<int:task_id>",
    methods=["POST"]
)
def reschedule(task_id):

    if not login_required():
        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]
    task = get_task(task_id, user_id)

    if not task:
        return jsonify({
            "success": False,
            "message": "Task not found."
        }), 404

    data = request.get_json(silent=True) or {}
    date_text = str(data.get("date", "")).strip()
    time_text = str(data.get("time", "")).strip()

    if not date_text or not time_text:
        return jsonify({
            "success": False,
            "message": "Choose a future date and time to reschedule this task."
        }), 400

    try:
        start_dt = datetime.strptime(
            f"{date_text} {time_text}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Use a date like 2026-08-20 and a time like 14:30."
        }), 400

    now = now_ist().replace(tzinfo=None, second=0, microsecond=0)
    if start_dt <= now:
        return jsonify({
            "success": False,
            "message": "Rescheduling is only available for a future date and time."
        }), 400

    end_dt = start_dt + timedelta(minutes=max(1, int(task["duration"] or 30)))
    if not slot_is_free(user_id, start_dt, end_dt, ignore_task_id=task_id):
        return jsonify({
            "success": False,
            "message": "That time overlaps another task or routine. Choose another future time."
        }), 409

    update_task_schedule(task_id, start_dt.isoformat(), end_dt.isoformat())
    update_task_status(task_id, "pending", user_id)
    message = (
        f"↻ \"{task['title']}\" rescheduled to "
        f"{start_dt.strftime('%B %d, %Y')}, "
        f"{start_dt.strftime('%I:%M %p').lstrip('0')} - "
        f"{end_dt.strftime('%I:%M %p').lstrip('0')}."
    )
    save_chat("bot", message, user_id)
    return jsonify({
        "success": True,
        "message": message,
        "speak": message,
        "scheduled_start": start_dt.isoformat(),
        "scheduled_end": end_dt.isoformat(),
    })


# =========================================================
# POSTPONE
# =========================================================

@app.route(
    "/postpone/<int:task_id>",
    methods=["POST"]
)
def postpone(task_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    task = get_task(
        task_id,
        user_id
    )

    if not task:

        return jsonify({
            "success": False,
            "message": "Task not found."
        }), 404

    postpone_task(
        task_id,
        user_id
    )

    try:
        generate_schedule(
            user_id=user_id
        )
    except TypeError:
        try:
            generate_schedule()
        except Exception:
            pass
    except Exception:
        pass

    message = (
        "⏰ Task postponed. "
        "Your schedule has been updated."
    )

    save_chat(
        "bot",
        message,
        user_id
    )

    return jsonify({
        "success": True,
        "message": message
    })


# =========================================================
# MOTIVATION
# =========================================================

@app.route("/motivation")
def motivation():

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    try:
        message = get_motivation()
    except Exception:
        message = (
            "🚀 You don't have to finish everything "
            "at once. Start with the next small step."
        )

    save_chat(
        "bot",
        message,
        user_id
    )

    return jsonify({
        "success": True,
        "message": message
    })


# =========================================================
# TASK API
# =========================================================

@app.route("/api/tasks")
def api_tasks():

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    tasks = get_tasks(user_id) or []

    result = []

    for task in tasks:

        result.append({
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "duration": task["duration"],
            "priority": task["priority"],
            "deadline": task["deadline"],
            "scheduled_start": task["scheduled_start"],
            "scheduled_end": task["scheduled_end"],
            "status": task["status"],
            "postponed_count": task["postponed_count"]
        })

    return jsonify(result)


# =========================================================
# AI CHAT
# =========================================================

@app.route("/ai-chat", methods=["POST"])
def ai_chat():
    if not login_required():
        return jsonify({"success": False, "message": "Please login first."}), 401

    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        bot_message = "Please tell me what you'd like me to add or manage."
        save_chat("bot", bot_message, user_id)
        return jsonify({"success": False, "type": "chat", "message": bot_message, "speak": bot_message})

    save_chat("user", message, user_id)

    pending_task = get_pending_task()
    pending_reschedule = get_pending_reschedule()

    try:
        understood = understand_message(message, pending_task, pending_reschedule)
    except Exception as error:
        print("GROQ ERROR:", repr(error))
        # A time reply must still work when Groq returns an empty or malformed
        # JSON response.  This local parser covers the follow-up required by
        # manual/dashboard rescheduling without guessing a time on the user's
        # behalf.
        local_time = extract_requested_time(message)
        if (pending_task or pending_reschedule) and local_time:
            time_value = f"{local_time[0]:02d}:{local_time[1]:02d}"
            time_range = extract_time_range(message)
            end_value = (
                f"{time_range[1][0]:02d}:{time_range[1][1]:02d}"
                if time_range else None
            )
            understood = {
                "intent": "time_reply",
                "title": None,
                "duration_minutes": extract_duration(message),
                "date": extract_requested_date(message),
                "time": time_value,
                "end_time": end_value,
                "target_task": None,
                "reply": None,
            }
        else:
            bot_message = "The AI response was invalid. Please try again in a moment."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "chat", "message": bot_message, "speak": bot_message})

    intent = understood["intent"]
    start_tuple = parse_hhmm(understood["time"])
    end_tuple = parse_hhmm(understood["end_time"])
    date_label = understood["date"]

    if intent == "time_reply" and pending_task:
        try:
            effective_date = date_label or pending_task.get("date_label") or "today"
            duration = pending_task.get("duration") or understood["duration_minutes"]

            if start_tuple is None:
                raise ValueError("I still need a specific time.")

            has_end_info = bool(end_tuple) or bool(duration)
            start_dt, computed_end_dt = build_task_datetime(effective_date, start_tuple, end_tuple, duration)
            end_dt = computed_end_dt if has_end_info else None
            check_end_dt = end_dt if end_dt is not None else start_dt + timedelta(minutes=1)

            if not slot_is_free(user_id, start_dt, check_end_dt):
                raise ValueError("That exact time overlaps your existing schedule.")

            details = {
                "title": pending_task["title"],
                "category": pending_task.get("category", "General"),
                "duration": duration,
                "priority": pending_task.get("priority", "Medium"),
                "deadline": pending_task.get("deadline", ""),
                "date_label": effective_date,
                "start_time": start_tuple,
                "end_time": end_tuple,
            }

            task_id = create_task_from_details(user_id, details)
            saved_task = get_task(task_id, user_id)
            clear_pending_task()

            date_text, start_text, end_text = format_schedule_datetime(
                saved_task["scheduled_start"], saved_task["scheduled_end"]
            )

            if end_text:
                bot_message = (
                    f"✅ I've added <b>{pending_task['title']}</b> to your planner."
                    f"<br><br>📅 <b>{date_text}</b><br>⏰ <b>{start_text} - {end_text}</b>"
                    f"<br><br>It is now on your schedule."
                )
                spoken = (
                    f"I've added {pending_task['title']} to your planner. "
                    f"It is scheduled for {date_text}, from {start_text} to {end_text}."
                )
            else:
                bot_message = (
                    f"✅ I've added <b>{pending_task['title']}</b> to your planner."
                    f"<br><br>📅 <b>{date_text}</b><br>⏰ <b>{start_text}</b>"
                    f"<br><br>It is now on your schedule."
                )
                spoken = (
                    f"I've added {pending_task['title']} to your planner. "
                    f"It is scheduled for {date_text} at {start_text}."
                )
            save_chat("bot", bot_message, user_id)
            return jsonify({
                "success": True,
                "type": "task",
                "message": bot_message,
                "speak": spoken,
                "task_id": task_id,
                "task": {
                    "id": task_id,
                    "title": pending_task["title"],
                    "duration": duration,
                    "scheduled_start": saved_task["scheduled_start"],
                    "scheduled_end": saved_task["scheduled_end"],
                },
            })
        except Exception as error:
            reason = str(error).strip() or "that time didn't work"
            bot_message = (
                f"I couldn't schedule <b>{pending_task.get('title', 'that task')}</b> "
                f"at that time: {reason}<br><br>Try a different time, for example "
                "<b>7 PM</b> or <b>8 to 10 PM</b>."
            )
            spoken = f"I couldn't schedule {pending_task.get('title', 'that task')} at that time. {reason}. Try a different time."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "clarification", "message": bot_message, "speak": spoken})

    if intent == "time_reply" and pending_reschedule:
        try:
            task = get_task(pending_reschedule["task_id"], user_id)
            if not task:
                clear_pending_reschedule()
                raise ValueError("That task no longer exists.")

            if date_label:
                effective_date = date_label
            elif pending_reschedule.get("requested_date"):
                effective_date = pending_reschedule["requested_date"]
            else:
                existing_start = parse_db_datetime(task["scheduled_start"])
                effective_date = existing_start.date() if existing_start else "today"

            if start_tuple is None:
                raise ValueError("I still need a specific time.")

            duration = task["duration"] or 30
            start_dt, end_dt = build_task_datetime(effective_date, start_tuple, end_tuple, duration)

            if start_dt <= now_ist().replace(tzinfo=None, second=0, microsecond=0):
                raise ValueError("Rescheduling is only available for a future date and time.")

            if not slot_is_free(user_id, start_dt, end_dt, ignore_task_id=task["id"]):
                raise ValueError("That time overlaps another task on your schedule.")

            update_task_schedule(task["id"], start_dt.isoformat(), end_dt.isoformat())
            update_task_status(task["id"], "pending", user_id)
            clear_pending_reschedule()

            date_text, start_text, end_text = format_schedule_datetime(start_dt.isoformat(), end_dt.isoformat())
            bot_message = f"↻ \"{task['title']}\" rescheduled to <b>{date_text}</b>, <b>{start_text} - {end_text}</b>."
            spoken = f"{task['title']} rescheduled to {date_text}, from {start_text} to {end_text}."
            save_chat("bot", bot_message, user_id)
            return jsonify({
                "success": True,
                "type": "reschedule",
                "message": bot_message,
                "speak": spoken,
                "task_id": task["id"],
                "scheduled_start": start_dt.isoformat(),
                "scheduled_end": end_dt.isoformat(),
            })
        except Exception as error:
            reason = str(error).strip() or "that time didn't work"
            title = pending_reschedule.get("title", "that task")
            bot_message = f"I still need to reschedule <b>{title}</b> — {reason}. Please give me another time."
            spoken = f"I still need to reschedule {title}. {reason}. Please give me another time."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "clarification", "message": bot_message, "speak": spoken})

    if intent == "reschedule":
        matched_task = find_task_by_message(user_id, understood["target_task"] or message)

        if not matched_task:
            bot_message = "I couldn't find a matching task to reschedule. What's it called?"
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "chat", "message": bot_message, "speak": bot_message})

        if start_tuple is None:
            # A date alone is not a schedulable slot.  Keep it for the
            # follow-up instead of attempting to unpack a missing time.
            save_pending_reschedule(
                matched_task["id"],
                matched_task["title"],
                requested_date=date_label,
            )
            date_text = f" on {task_date_text(date_label)}" if date_label else ""
            bot_message = f"Sure — what time would you like to reschedule <b>{matched_task['title']}</b> to{date_text}?"
            spoken = f"What time would you like to reschedule {matched_task['title']} to{date_text}?"
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": True, "type": "clarification", "message": bot_message, "speak": spoken})

        try:
            if date_label:
                effective_date = date_label
            else:
                existing_start = parse_db_datetime(matched_task["scheduled_start"])
                effective_date = existing_start.date() if existing_start else "today"

            duration = matched_task["duration"] or 30
            start_dt, end_dt = build_task_datetime(effective_date, start_tuple, end_tuple, duration)

            if start_dt <= now_ist().replace(tzinfo=None, second=0, microsecond=0):
                raise ValueError("Rescheduling is only available for a future date and time.")

            if not slot_is_free(user_id, start_dt, end_dt, ignore_task_id=matched_task["id"]):
                raise ValueError("That time overlaps another task on your schedule.")

            update_task_schedule(matched_task["id"], start_dt.isoformat(), end_dt.isoformat())
            update_task_status(matched_task["id"], "pending", user_id)

            date_text, start_text, end_text = format_schedule_datetime(start_dt.isoformat(), end_dt.isoformat())
            bot_message = f"↻ \"{matched_task['title']}\" rescheduled to <b>{date_text}</b>, <b>{start_text} - {end_text}</b>."
            spoken = f"{matched_task['title']} rescheduled to {date_text}, from {start_text} to {end_text}."
            save_chat("bot", bot_message, user_id)
            return jsonify({
                "success": True,
                "type": "reschedule",
                "message": bot_message,
                "speak": spoken,
                "task_id": matched_task["id"],
                "scheduled_start": start_dt.isoformat(),
                "scheduled_end": end_dt.isoformat(),
            })
        except Exception as error:
            reason = str(error).strip() or "that time didn't work"
            bot_message = f"I couldn't reschedule <b>{matched_task['title']}</b> — {reason}. Try a different time."
            spoken = f"I couldn't reschedule {matched_task['title']}. {reason}."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "chat", "message": bot_message, "speak": spoken})

    if intent == "show_schedule":
        tasks = get_tasks(user_id) or []
        pending_tasks = [t for t in tasks if t["status"] not in ("completed",)]

        if not pending_tasks:
            bot_message = "You have no pending tasks."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": True, "type": "chat", "message": bot_message, "speak": bot_message})

        message_text = "📅 <b>Here is your TimeTrace schedule:</b><br><br>"
        spoken_text = "Here is your TimeTrace schedule. "

        for task in pending_tasks[:12]:
            if task["scheduled_start"]:
                date_text, start_text, end_text = format_schedule_datetime(task["scheduled_start"], task["scheduled_end"])
                message_text += f"📌 <b>{task['title']}</b><br>📅 {date_text}<br>⏰ {start_text} - {end_text}<br>⏱️ {task['duration']} minutes<br><br>"
                spoken_text += f"{task['title']} on {date_text}, from {start_text} to {end_text}. "
            else:
                message_text += f"📌 <b>{task['title']}</b> — time not set<br><br>"
                spoken_text += f"{task['title']}, time not set. "

        save_chat("bot", message_text, user_id)
        return jsonify({"success": True, "type": "chat", "message": message_text, "speak": spoken_text})

    if intent == "free_time":
        target_date = date_label or "today"
        date_word = "tomorrow" if target_date == "tomorrow" else "today"

        try:
            free_periods = compute_free_periods(user_id, target_date)
        except Exception as error:
            print("FREE TIME ERROR:", repr(error))
            free_periods = []

        if free_periods:
            message_text = f"🗓️ <b>Free time {date_word}:</b><br><br>"
            spoken_text = f"Here's your free time {date_word}. "
            for start_dt, end_dt in free_periods[:8]:
                start_text = start_dt.strftime("%I:%M %p").lstrip("0")
                end_text = end_dt.strftime("%I:%M %p").lstrip("0")
                message_text += f"⏰ <b>{start_text} - {end_text}</b><br>"
                spoken_text += f"{start_text} to {end_text}. "
        else:
            message_text = f"You don't have any free periods {date_word} between 6 AM and 11 PM."
            spoken_text = message_text

        save_chat("bot", message_text, user_id)
        return jsonify({"success": True, "type": "chat", "message": message_text, "speak": spoken_text})

    if intent == "motivation":
        try:
            bot_message = get_motivation()
        except Exception:
            bot_message = "You don't have to finish everything at once. Start with the next small step."
        save_chat("bot", bot_message, user_id)
        return jsonify({
            "success": True,
            "type": "chat",
            "message": bot_message,
            "speak": re.sub(r"<[^>]+>", "", str(bot_message)),
        })

    if intent == "greeting":
        name = session.get("user_name", "there")
        bot_message = (
            f"Hi {name}! I'm your TimeTrace AI assistant."
            "<br><br>I can add tasks and put them directly into your planner."
            "<br><br>Try: <b>Shopping tomorrow</b><br>I'll ask you for the exact time."
        )
        spoken = f"Hi {name}! I'm your TimeTrace AI assistant. Tell me a task such as Shopping tomorrow, and I'll ask you for the exact time."
        save_chat("bot", bot_message, user_id)
        return jsonify({"success": True, "type": "chat", "message": bot_message, "speak": spoken})

    if intent == "create_task":
        title = understood["title"] or "Task"
        duration = understood["duration_minutes"]

        if start_tuple is None:
            save_pending_task({
                "title": title,
                "category": "General",
                "duration": duration,
                "priority": "Medium",
                "deadline": "",
                "date_label": date_label or "today",
            })
            date_text = task_date_text(date_label or "today")
            bot_message = f"I have <b>{title}</b> ready for <b>{date_text}</b>.<br><br>⏰ What exact time would you like to start it?"
            spoken = f"I have {title} ready for {date_text}. What exact time would you like to start it?"
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": True, "type": "clarification", "message": bot_message, "speak": spoken})

        try:
            effective_date = date_label or "today"
            has_end_info = bool(end_tuple) or bool(duration)

            start_dt, computed_end_dt = build_task_datetime(effective_date, start_tuple, end_tuple, duration)
            end_dt = computed_end_dt if has_end_info else None
            check_end_dt = end_dt if end_dt is not None else start_dt + timedelta(minutes=1)

            if not slot_is_free(user_id, start_dt, check_end_dt):
                raise ValueError("That exact time overlaps your existing schedule.")

            details = {
                "title": title,
                "category": "General",
                "duration": duration,
                "priority": "Medium",
                "deadline": "",
                "date_label": effective_date,
                "start_time": start_tuple,
                "end_time": end_tuple,
            }
            task_id = create_task_from_details(user_id, details)
            saved_task = get_task(task_id, user_id)

            if not saved_task or not saved_task["scheduled_start"]:
                raise ValueError("Task was created but could not be scheduled.")

            date_text, start_text, end_text = format_schedule_datetime(saved_task["scheduled_start"], saved_task["scheduled_end"])

            actual_duration = duration
            if saved_task["scheduled_start"] and saved_task["scheduled_end"]:
                try:
                    real_start = datetime.fromisoformat(str(saved_task["scheduled_start"]))
                    real_end = datetime.fromisoformat(str(saved_task["scheduled_end"]))
                    actual_duration = int((real_end - real_start).total_seconds() / 60)
                except Exception:
                    pass

            if end_text:
                bot_message = (
                    f"✅ <b>{title}</b> has been added to your planner."
                    f"<br><br>📅 <b>{date_text}</b><br>⏰ <b>{start_text} - {end_text}</b>"
                    f"<br>⏱️ {actual_duration} minutes<br><br>🎉 Your schedule has been updated."
                )
                spoken = f"Done. I've added {title} to your planner for {date_text}, from {start_text} to {end_text}. Your schedule has been updated."
            else:
                bot_message = (
                    f"✅ <b>{title}</b> has been added to your planner."
                    f"<br><br>📅 <b>{date_text}</b><br>⏰ <b>{start_text}</b>"
                    f"<br><br>🎉 Your schedule has been updated."
                )
                spoken = f"Done. I've added {title} to your planner for {date_text} at {start_text}. Your schedule has been updated."


            save_chat("bot", bot_message, user_id)
            return jsonify({
                "success": True,
                "type": "task",
                "message": bot_message,
                "speak": spoken,
                "task_id": task_id,
                "task": {
                    "id": task_id,
                    "title": title,
                    "category": "General",
                    "duration": duration,
                    "priority": "Medium",
                    "deadline": "",
                    "scheduled_start": saved_task["scheduled_start"],
                    "scheduled_end": saved_task["scheduled_end"],
                    "status": saved_task["status"],
                },
            })
        except Exception as error:
            print("TASK CREATION ERROR:", repr(error))
            bot_message = (
                "I understood the task, but I couldn't put it on the planner."
                "<br><br>Please try another time, for example <b>7 PM</b> or <b>8 to 10 PM</b>."
            )
            spoken = "I understood the task, but I couldn't put it on the planner. Please try another time, such as 7 PM or 8 to 10 PM."
            save_chat("bot", bot_message, user_id)
            return jsonify({"success": False, "type": "chat", "message": bot_message, "speak": spoken})

    bot_message = understood["reply"] or (
        "I can help you manage your time."
        "<br><br>Try saying:<br>📚 <b>Study DBMS for 2 hours tomorrow at 7 PM</b><br>"
        "🛒 <b>Shopping tomorrow</b><br>📅 <b>Show my tasks</b><br>🔥 <b>Motivate me</b>"
    )
    spoken = re.sub(r"<[^>]+>", "", str(bot_message))
    save_chat("bot", bot_message, user_id)
    return jsonify({"success": True, "type": "chat", "message": bot_message, "speak": spoken})


@app.route(
    "/create-goal",
    methods=["POST"]
)
def create_goal():

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    data = request.get_json(
        silent=True
    ) or {}

    title = data.get(
        "title",
        ""
    ).strip()

    if not title:

        return jsonify({
            "success": False,
            "message": "Goal title required."
        })

    goal_id = add_goal(
        title,
        data.get(
            "description",
            ""
        ),
        data.get(
            "category",
            "General"
        ),
        data.get(
            "priority",
            "Medium"
        ),
        data.get(
            "deadline",
            ""
        ),
        user_id
    )

    return jsonify({
        "success": True,
        "goal_id": goal_id,
        "message": "🎯 Goal created successfully!"
    })


# =========================================================
# GOAL PROGRESS
# =========================================================

@app.route(
    "/goal/<int:goal_id>/progress",
    methods=["POST"]
)
def goal_progress(goal_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    data = request.get_json(
        silent=True
    ) or {}

    try:

        progress = int(
            data.get(
                "progress",
                0
            )
        )

    except (TypeError, ValueError):

        progress = 0

    progress = max(
        0,
        min(
            100,
            progress
        )
    )

    update_goal_progress(
        goal_id,
        progress,
        user_id
    )

    return jsonify({
        "success": True,
        "progress": progress
    })


# =========================================================
# ADD ROUTINE
# =========================================================

@app.route(
    "/add-routine",
    methods=["POST"]
)
def create_routine():

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    data = request.get_json(
        silent=True
    ) or {}

    title = data.get(
        "title",
        ""
    ).strip()

    start_time = data.get(
        "start_time",
        ""
    )

    end_time = data.get(
        "end_time",
        ""
    )

    days = data.get(
        "days",
        "Everyday"
    )

    if (
        not title
        or not start_time
        or not end_time
    ):

        return jsonify({
            "success": False,
            "message": "Routine details required."
        })

    routine_id = add_routine(
        title,
        start_time,
        end_time,
        days,
        user_id
    )

    return jsonify({
        "success": True,
        "routine_id": routine_id
    })


# =========================================================
# DELETE ROUTINE
# =========================================================

@app.route(
    "/delete-routine/<int:routine_id>",
    methods=["POST"]
)
def remove_routine(routine_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    try:

        delete_routine(
            routine_id,
            user_id
        )

    except TypeError:

        delete_routine(routine_id)

    return jsonify({
        "success": True,
        "message": "Routine deleted."
    })


# =========================================================
# NOTIFICATION
# =========================================================

@app.route(
    "/notification/<int:notification_id>/read",
    methods=["POST"]
)
def notification_read(notification_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401

    user_id = session["user_id"]

    mark_notification_read(
        notification_id,
        user_id
    )

    return jsonify({
        "success": True
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )
