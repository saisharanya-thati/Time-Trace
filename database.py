import sqlite3
from datetime import datetime


DATABASE = "timetrace.db"


# =========================================================
# CONNECTION
# =========================================================

def get_connection():

    connection = sqlite3.connect(
        DATABASE
    )

    connection.row_factory = sqlite3.Row

    return connection


# =========================================================
# INITIAL DATABASE
# =========================================================

def init_db():

    connection = get_connection()
    cursor = connection.cursor()

    # -----------------------------------------------------
    # USERS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL,

            email TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP

        )
    """)

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            category TEXT DEFAULT 'General',

            duration INTEGER DEFAULT 30,

            priority TEXT DEFAULT 'Medium',

            deadline TEXT,

            scheduled_start TEXT,

            scheduled_end TEXT,

            status TEXT DEFAULT 'pending',

            postponed_count INTEGER DEFAULT 0,

            user_id INTEGER NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

        )
    """)

    # -----------------------------------------------------
    # GOALS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS goals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            description TEXT,

            category TEXT DEFAULT 'General',

            priority TEXT DEFAULT 'Medium',

            deadline TEXT,

            progress INTEGER DEFAULT 0,

            user_id INTEGER NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

        )
    """)

    # -----------------------------------------------------
    # ROUTINES
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS routines (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            start_time TEXT NOT NULL,

            end_time TEXT NOT NULL,

            days TEXT DEFAULT 'Everyday',

            user_id INTEGER NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

        )
    """)

    # -----------------------------------------------------
    # NOTIFICATIONS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            message TEXT NOT NULL,

            is_read INTEGER DEFAULT 0,

            user_id INTEGER NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

        )
    """)

    connection.commit()

    connection.close()


# =========================================================
# CHAT TABLE
# =========================================================

def init_chat_table():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            sender TEXT NOT NULL,

            message TEXT NOT NULL,

            user_id INTEGER NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

        )
    """)

    connection.commit()

    connection.close()


# =========================================================
# USERS
# =========================================================

def create_user(
    name,
    email,
    password_hash
):

    connection = get_connection()
    cursor = connection.cursor()

    try:

        cursor.execute("""
            INSERT INTO users
            (
                name,
                email,
                password_hash
            )

            VALUES (?, ?, ?)
        """, (
            name,
            email,
            password_hash
        ))

        connection.commit()

        user_id = cursor.lastrowid

    except sqlite3.IntegrityError:

        user_id = None

    connection.close()

    return user_id


def get_user_by_email(email):

    connection = get_connection()

    user = connection.execute("""
        SELECT *
        FROM users
        WHERE email = ?
    """, (
        email,
    )).fetchone()

    connection.close()

    return user


def get_user_by_id(user_id):

    connection = get_connection()

    user = connection.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (
        user_id,
    )).fetchone()

    connection.close()

    return user


# =========================================================
# TASKS
# =========================================================

def add_task(
    title,
    category,
    duration,
    priority,
    deadline,
    user_id
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO tasks
        (
            title,
            category,
            duration,
            priority,
            deadline,
            user_id
        )

        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        title,
        category,
        duration,
        priority,
        deadline,
        user_id
    ))

    connection.commit()

    task_id = cursor.lastrowid

    connection.close()

    return task_id


def get_tasks(user_id):

    connection = get_connection()

    tasks = connection.execute("""
        SELECT *
        FROM tasks
        WHERE user_id = ?

        ORDER BY

            CASE
                WHEN status = 'completed'
                THEN 1
                ELSE 0
            END,

            CASE priority
                WHEN 'High' THEN 1
                WHEN 'Medium' THEN 2
                ELSE 3
            END,

            id DESC

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return tasks


def get_task(
    task_id,
    user_id
):

    connection = get_connection()

    task = connection.execute("""
        SELECT *
        FROM tasks

        WHERE id = ?
        AND user_id = ?

    """, (
        task_id,
        user_id
    )).fetchone()

    connection.close()

    return task


def update_task_status(
    task_id,
    status,
    user_id
):

    connection = get_connection()

    connection.execute("""
        UPDATE tasks

        SET status = ?

        WHERE id = ?
        AND user_id = ?

    """, (
        status,
        task_id,
        user_id
    ))

    connection.commit()

    connection.close()


def postpone_task(
    task_id,
    user_id
):

    connection = get_connection()

    connection.execute("""
        UPDATE tasks

        SET

            postponed_count =
                postponed_count + 1,

            status = 'pending',

            scheduled_start = NULL,

            scheduled_end = NULL

        WHERE id = ?
        AND user_id = ?

    """, (
        task_id,
        user_id
    ))

    connection.commit()

    connection.close()


# =========================================================
# UPDATE TASK SCHEDULE
# =========================================================

def update_task_duration(
    task_id,
    duration
):

    connection = get_connection()

    connection.execute("""
        UPDATE tasks

        SET
            duration = ?

        WHERE id = ?

    """, (
        duration,
        task_id
    ))

    connection.commit()

    connection.close()


def update_task_schedule(
    task_id,
    start,
    end
):

    connection = get_connection()

    connection.execute("""
        UPDATE tasks

        SET
            scheduled_start = ?,
            scheduled_end = ?

        WHERE id = ?

    """, (
        start,
        end,
        task_id
    ))

    connection.commit()

    connection.close()


# =========================================================
# CHAT
# =========================================================

def save_chat(
    sender,
    message,
    user_id
):

    connection = get_connection()

    connection.execute("""
        INSERT INTO chat_history
        (
            sender,
            message,
            user_id
        )

        VALUES (?, ?, ?)

    """, (
        sender,
        message,
        user_id
    ))

    connection.commit()

    connection.close()


def get_chat_history(user_id):

    connection = get_connection()

    chats = connection.execute("""
        SELECT *

        FROM chat_history

        WHERE user_id = ?

        ORDER BY id ASC

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return chats


# =========================================================
# GOALS
# =========================================================

def add_goal(
    title,
    description,
    category,
    priority,
    deadline,
    user_id
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO goals
        (
            title,
            description,
            category,
            priority,
            deadline,
            user_id
        )

        VALUES (?, ?, ?, ?, ?, ?)

    """, (
        title,
        description,
        category,
        priority,
        deadline,
        user_id
    ))

    connection.commit()

    goal_id = cursor.lastrowid

    connection.close()

    return goal_id


def get_goals(user_id):

    connection = get_connection()

    goals = connection.execute("""
        SELECT *

        FROM goals

        WHERE user_id = ?

        ORDER BY id DESC

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return goals


def update_goal_progress(
    goal_id,
    progress,
    user_id
):

    connection = get_connection()

    connection.execute("""
        UPDATE goals

        SET progress = ?

        WHERE id = ?
        AND user_id = ?

    """, (
        progress,
        goal_id,
        user_id
    ))

    connection.commit()

    connection.close()


# =========================================================
# ROUTINES
# =========================================================

def add_routine(
    title,
    start_time,
    end_time,
    days,
    user_id
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO routines
        (
            title,
            start_time,
            end_time,
            days,
            user_id
        )

        VALUES (?, ?, ?, ?, ?)

    """, (
        title,
        start_time,
        end_time,
        days,
        user_id
    ))

    connection.commit()

    routine_id = cursor.lastrowid

    connection.close()

    return routine_id


def get_routines(user_id):

    connection = get_connection()

    routines = connection.execute("""
        SELECT *

        FROM routines

        WHERE user_id = ?

        ORDER BY start_time ASC

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return routines


def delete_routine(
    routine_id,
    user_id=None
):

    connection = get_connection()

    if user_id is not None:

        connection.execute("""
            DELETE FROM routines

            WHERE id = ?
            AND user_id = ?

        """, (
            routine_id,
            user_id
        ))

    else:

        connection.execute("""
            DELETE FROM routines
            WHERE id = ?

        """, (
            routine_id,
        ))

    connection.commit()

    connection.close()


# =========================================================
# NOTIFICATIONS
# =========================================================

def get_notifications(user_id):

    connection = get_connection()

    notifications = connection.execute("""
        SELECT *

        FROM notifications

        WHERE user_id = ?

        ORDER BY id DESC

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return notifications


def mark_notification_read(
    notification_id,
    user_id
):

    connection = get_connection()

    connection.execute("""
        UPDATE notifications

        SET is_read = 1

        WHERE id = ?
        AND user_id = ?

    """, (
        notification_id,
        user_id
    ))

    connection.commit()

    connection.close()


# =========================================================
# PRODUCTIVITY
# =========================================================

def get_task_statistics(user_id):

    connection = get_connection()

    total = connection.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE user_id = ?
    """, (
        user_id,
    )).fetchone()[0]

    completed = connection.execute("""
        SELECT COUNT(*)
        FROM tasks

        WHERE user_id = ?
        AND status = 'completed'

    """, (
        user_id,
    )).fetchone()[0]

    pending = connection.execute("""
        SELECT COUNT(*)
        FROM tasks

        WHERE user_id = ?
        AND status != 'completed'

    """, (
        user_id,
    )).fetchone()[0]

    missed = connection.execute("""
        SELECT COUNT(*)
        FROM tasks

        WHERE user_id = ?
        AND status = 'missed'

    """, (
        user_id,
    )).fetchone()[0]

    connection.close()

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "missed": missed
    }


def calculate_productivity(user_id):

    statistics = get_task_statistics(
        user_id
    )

    total = statistics["total"]
    completed = statistics["completed"]

    if total == 0:
        return 0

    score = (
        completed / total
    ) * 100

    return round(score)


def get_productivity_history(user_id):

    return []


def get_activity_history(user_id):

    connection = get_connection()

    activity = connection.execute("""
        SELECT
            created_at,
            status

        FROM tasks

        WHERE user_id = ?

        ORDER BY id DESC

        LIMIT 30

    """, (
        user_id,
    )).fetchall()

    connection.close()

    return activity


def get_productivity_dna(user_id):

    statistics = get_task_statistics(
        user_id
    )

    productivity = calculate_productivity(
        user_id
    )

    total = statistics["total"]

    if total == 0:

        return {
            "type": "Getting Started",
            "description": "Start adding tasks to discover your productivity pattern.",
            "score": 0
        }

    if productivity >= 80:

        dna_type = "High Performer"

        description = (
            "You consistently complete your planned tasks "
            "and maintain strong productivity."
        )

    elif productivity >= 60:

        dna_type = "Steady Achiever"

        description = (
            "You maintain a good balance between planning "
            "and completing your tasks."
        )

    elif productivity >= 40:

        dna_type = "Flexible Worker"

        description = (
            "Your productivity varies, but you have "
            "a strong opportunity to build consistency."
        )

    else:

        dna_type = "Momentum Builder"

        description = (
            "Small consistent actions can significantly "
            "improve your productivity."
        )

    return {
        "type": dna_type,
        "description": description,
        "score": productivity
    }