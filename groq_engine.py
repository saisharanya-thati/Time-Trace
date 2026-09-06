import os
import json
from datetime import datetime
from groq import Groq

_client = None


def get_client():
    global _client
    if _client is None:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY environment variable is not set.")
        _client = Groq(api_key=key)
    return _client


SYSTEM_TEMPLATE = """You are the natural language understanding layer for a scheduling app called TimeTrace.
Return ONLY a single valid JSON object, no markdown fences, no explanation text.

Fields to return:
intent: one of "create_task", "time_reply", "reschedule", "show_schedule", "free_time", "motivation", "greeting", "chat"
title: short task title in title case, or null
duration_minutes: integer number of minutes, or null. If the user gave an explicit start and end time (a range), leave duration_minutes null - do not guess or fill in a default.
date: "today", "tomorrow", a specific date as "YYYY-MM-DD" if the user names one (e.g. "August 20 2026" -> "2026-08-20", "20th August" -> "2026-08-20" using the current year unless stated otherwise), or null if no date at all is mentioned
time: 24-hour "HH:MM" string for the start time, or null
end_time: 24-hour "HH:MM" string, ONLY if the user explicitly gave a time
    range (e.g. "8 to 10 PM", "between 3 and 4"). Never guess or infer an
    end_time from a duration or from context - if only a start time and/or
    a duration was given, end_time MUST be null so the app can compute the
    end time itself from the duration.
target_task: short string naming an existing task the user is referring to, or null (only for reschedule)
reply: a short natural-language reply, or null (only for intent "chat", "greeting", "motivation")

Rules:
- "time_reply" means the message is only answering a previously asked "what time?" question (e.g. "7 PM", "tomorrow evening", "8 to 10").
- "reschedule" means the user wants to move an existing task ("reschedule X to 5 PM", "move gym", "push back the meeting").
- "create_task" means a new task is being described, even without an explicit verb from any fixed list, and even if title/date/time are only partially given.
- "free_time" means the user is asking when they are free or available, not creating anything.
- "show_schedule" means the user wants to see their existing tasks/schedule.
- Interpret vague time-of-day words directly: morning=09:00, afternoon=14:00, evening=18:00, night=21:00, noon=12:00.
- Interpret spelled-out numbers ("two hours") the same as numerals ("2 hours").
- Current date/time: {now}
{context}
"""


def understand_message(message, pending_task=None, pending_reschedule=None):
    now = datetime.now().strftime("%A, %Y-%m-%d %H:%M")

    context = ""
    if pending_task:
        context = (
            f'The assistant just asked what time the user wants for the task '
            f'"{pending_task.get("title")}". If this message is only a time/date '
            f'answer, use intent "time_reply". If it clearly describes a different '
            f'new task instead, use "create_task".'
        )
    elif pending_reschedule:
        context = (
            f'The assistant just asked what time to reschedule '
            f'"{pending_reschedule.get("title")}" to. If this message is only a '
            f'time/date answer, use intent "time_reply".'
        )

    system = SYSTEM_TEMPLATE.format(now=now, context=context)

    client = get_client()
    model_name = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

    try:
        completion = client.chat.completions.create(
            model=model_name,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
        )
    except Exception:
        completion = client.chat.completions.create(
            model=model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
        )

    raw = str(completion.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    if not raw:
        raise RuntimeError("Groq returned an empty response instead of JSON.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Groq returned malformed JSON.") from error

    if not isinstance(data, dict):
        raise RuntimeError("Groq returned JSON in an unexpected format.")

    return {
        "intent": data.get("intent") or "chat",
        "title": data.get("title"),
        "duration_minutes": data.get("duration_minutes"),
        "date": data.get("date"),
        "time": data.get("time"),
        "end_time": data.get("end_time"),
        "target_task": data.get("target_task"),
        "reply": data.get("reply"),
    }


def parse_hhmm(value):
    if not value:
        return None
    try:
        hour_str, minute_str = str(value).split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, TypeError):
        pass
    return None
