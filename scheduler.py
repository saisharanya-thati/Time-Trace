from datetime import datetime, timedelta
from database import get_tasks, update_task_schedule


# =========================================================
# SMART SCHEDULER
# =========================================================

def generate_schedule(user_id=None):

    tasks = get_tasks(user_id)

    if not tasks:
        return

    now = datetime.now()

    # Start scheduling from the next 30-minute boundary
    minute = (now.minute // 30 + 1) * 30

    if minute >= 60:
        current_time = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )
    else:
        current_time = now.replace(
            minute=minute,
            second=0,
            microsecond=0
        )

    # -----------------------------------------------------
    # Schedule pending tasks
    # -----------------------------------------------------

    for task in tasks:

        if task["status"] == "completed":
            continue

        if task["status"] == "missed":
            continue

        if task["scheduled_start"]:
            continue

        duration = task["duration"] or 30

        start = current_time

        end = start + timedelta(
            minutes=duration
        )

        # -------------------------------------------------
        # Check deadline
        # -------------------------------------------------

        deadline = None

        if task["deadline"]:

            try:

                deadline = datetime.fromisoformat(
                    task["deadline"]
                )

            except ValueError:

                deadline = None

        # Don't schedule beyond deadline
        if deadline and end > deadline:

            continue

        update_task_schedule(
            task["id"],
            start.isoformat(),
            end.isoformat(),
            user_id
        )

        # Add a small break between tasks
        current_time = end + timedelta(
            minutes=10
        )


# =========================================================
# RESCHEDULE MISSED TASK
# =========================================================

def reschedule_task(
    task_id,
    user_id=None
):

    tasks = get_tasks(user_id)

    task = None

    for item in tasks:

        if item["id"] == task_id:

            task = item

            break

    if not task:
        return None

    duration = task["duration"] or 30

    now = datetime.now()

    # -----------------------------------------------------
    # Find free time in the next few hours
    # -----------------------------------------------------

    candidate = now + timedelta(
        minutes=15
    )

    # Round to next 15 minutes

    minute = (
        (candidate.minute // 15) + 1
    ) * 15

    if minute >= 60:

        candidate = (
            candidate.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    else:

        candidate = candidate.replace(
            minute=minute,
            second=0,
            microsecond=0
        )

    # -----------------------------------------------------
    # Check existing scheduled tasks
    # -----------------------------------------------------

    for _ in range(40):

        proposed_start = candidate

        proposed_end = (
            proposed_start
            + timedelta(minutes=duration)
        )

        conflict = False

        for other in tasks:

            if other["id"] == task_id:
                continue

            if other["status"] == "completed":
                continue

            if not other["scheduled_start"]:
                continue

            try:

                other_start = datetime.fromisoformat(
                    other["scheduled_start"]
                )

                other_end = datetime.fromisoformat(
                    other["scheduled_end"]
                )

            except (ValueError, TypeError):

                continue

            # Check overlap

            if (
                proposed_start < other_end
                and proposed_end > other_start
            ):

                conflict = True

                break

        if not conflict:

            # Check deadline

            if task["deadline"]:

                try:

                    deadline = datetime.fromisoformat(
                        task["deadline"]
                    )

                    if proposed_end > deadline:

                        return None

                except ValueError:

                    pass

            update_task_schedule(
                task_id,
                proposed_start.isoformat(),
                proposed_end.isoformat(),
                user_id
            )

            return (
                proposed_start.strftime("%I:%M %p"),
                proposed_end.strftime("%I:%M %p")
            )

        candidate += timedelta(
            minutes=30
        )

    return None