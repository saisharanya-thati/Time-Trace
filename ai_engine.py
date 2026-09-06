import re
from datetime import datetime, timedelta


# Spelled-out numbers like "two hours" never matched the digit-only regex
# before, so anything not written as a numeral (e.g. "2 hours") silently
# fell back to the 30-minute default. This maps common word numbers to
# digits before the regex runs, so "two hours" and "2 hours" both work.
WORD_NUMBERS = {
    "half": "0.5",
    "a": "1", "an": "1", "one": "1",
    "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}


def _normalize_word_numbers(text):
    """Replace spelled-out numbers with digits so duration regexes match."""
    for word, digit in WORD_NUMBERS.items():
        text = re.sub(r"\b" + word + r"\b", digit, text)
    return text


def extract_duration(text):

    text = text.lower()

    # Handle "half an hour" / "half hour" as a direct special case before
    # word-number substitution, since "an" would otherwise be read as "1"
    # and override "half" (turning it into 60 minutes instead of 30).
    if re.search(r"half\s+(an\s+)?hour", text):
        return 30

    text = _normalize_word_numbers(text)

    # Example: 2 hours (now also catches "two hours" -> normalized to "2 hours")
    hour_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs)",
        text
    )

    if hour_match:

        hours = float(hour_match.group(1))

        return int(hours * 60)

    # Example: 30 minutes
    minute_match = re.search(
        r"(\d+)\s*(minute|minutes|min|mins)",
        text
    )

    if minute_match:

        return int(minute_match.group(1))

    return 30


def extract_priority(text):

    text = text.lower()

    if any(word in text for word in [
        "urgent",
        "important",
        "high priority",
        "asap",
        "critical"
    ]):

        return "High"

    if any(word in text for word in [
        "low priority",
        "not urgent"
    ]):

        return "Low"

    return "Medium"


def extract_category(text):

    text = text.lower()

    if any(word in text for word in [
        "study",
        "exam",
        "revision",
        "chapter",
        "dbms",
        "gate"
    ]):

        return "Study"

    if any(word in text for word in [
        "project",
        "coding",
        "programming",
        "development"
    ]):

        return "Work"

    if any(word in text for word in [
        "college",
        "assignment",
        "lab",
        "class"
    ]):

        return "College"

    if any(word in text for word in [
        "workout",
        "gym",
        "exercise"
    ]):

        return "Personal"

    return "General"


def extract_deadline(text):

    text = text.lower()

    now = datetime.now()

    if "today" in text:

        return now.strftime("%Y-%m-%d %H:%M")

    if "tomorrow" in text:

        tomorrow = now + timedelta(days=1)

        return tomorrow.strftime(
            "%Y-%m-%d %H:%M"
        )

    if "day after tomorrow" in text:

        date = now + timedelta(days=2)

        return date.strftime(
            "%Y-%m-%d %H:%M"
        )

    # Weekday detection

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6
    }

    for day, weekday in weekdays.items():

        if day in text:

            days_ahead = (
                weekday - now.weekday()
            ) % 7

            if days_ahead == 0:
                days_ahead = 7

            date = now + timedelta(
                days=days_ahead
            )

            return date.strftime(
                "%Y-%m-%d %H:%M"
            )

    return ""


def clean_task_title(text):

    title = text.strip()

    patterns = [

        r"for \d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)",

        r"tomorrow",

        r"today",

        r"day after tomorrow",

        r"before monday",

        r"before tuesday",

        r"before wednesday",

        r"before thursday",

        r"before friday",

        r"before saturday",

        r"before sunday",

        r"urgent",

        r"high priority",

        r"asap"

    ]

    for pattern in patterns:

        title = re.sub(
            pattern,
            "",
            title,
            flags=re.IGNORECASE
        )

    title = re.sub(
        r"^(i need to|i have to|i want to|remind me to|please)\s+",
        "",
        title,
        flags=re.IGNORECASE
    )

    return title.strip(" .,!?") or "New Task"


def understand_task(text):

    duration = extract_duration(text)

    priority = extract_priority(text)

    category = extract_category(text)

    deadline = extract_deadline(text)

    title = clean_task_title(text)

    return {

        "title": title,

        "duration": duration,

        "priority": priority,

        "category": category,

        "deadline": deadline

    }