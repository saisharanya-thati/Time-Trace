# TimeTrace — Groq-powered version

The regex/keyword NLP layer is replaced with a Groq API call. Every
Flask route, the database, and the scheduling/overlap-checking engine
are unchanged and still fully tested — only the "understand what the
user meant" step changed.

## Setup

```
pip install -r requirements.txt
```

Set your key as an environment variable — never hardcode it in app.py
or commit it to GitHub:

Windows (Command Prompt):
```
set GROQ_API_KEY=your_key_here
python app.py
```

Windows (PowerShell):
```
$env:GROQ_API_KEY="your_key_here"
python app.py
```

Mac/Linux:
```
export GROQ_API_KEY=your_key_here
python app.py
```

If you'd rather not type this every time, install python-dotenv,
create a `.env` file containing `GROQ_API_KEY=your_key_here`, add
`.env` to `.gitignore`, and add `from dotenv import load_dotenv;
load_dotenv()` near the top of app.py.

## How statelessness is handled

Groq has no memory between calls. State lives in the Flask session,
exactly like before (`pending_task`, `pending_reschedule`). Each call
to `understand_message()` in groq_engine.py includes that pending
context directly in the prompt, so a reply like "7 PM" is understood
as answering the earlier question, not a new isolated message.

## What changed vs. what didn't

Changed: `groq_engine.py` (new file) and the `/ai-chat` route in
`app.py` — intent detection and field extraction (title, duration,
date, time) now come from a single Groq call instead of ~15 regex
functions.

Unchanged: every other route, `database.py`, `scheduler.py`,
`motivation.py`, the overlap-checking (`slot_is_free`), task creation
(`create_task_from_details`), and all HTML templates. These were
already tested and working — rewriting them wasn't necessary or safe
with limited time before a deadline.

## Demo-safety note

If the venue's wifi is unreliable, this version cannot work at all —
every message requires a network call to Groq. Before you commit to
demoing this instead of the offline regex version, test it on the
actual venue wifi if you can. `/ai-chat` will show "I couldn't reach
the AI service" instead of crashing if Groq is unreachable, but that's
still a broken demo, just a graceful one.

## Testing note from me

I verified the entire request/response pipeline — session state,
task creation, rescheduling, free-time search, overlap checking — by
mocking Groq's response and confirming everything downstream works
correctly against the real database. I could not test an actual live
call to Groq's API in this environment (no network access to
api.groq.com here). Test the real API call yourself first thing,
before relying on it for anything else.
