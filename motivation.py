import random


def get_motivation(task=None):

    messages = [
        "🔥 You've got this! Start with just 10 minutes.",
        "💪 Small progress is still progress. Let's get started.",
        "🚀 One task at a time. You're building momentum.",
        "🎯 Future you will thank you for completing this.",
        "⚡ Don't wait for motivation. Start, and motivation will follow.",
        "🏆 You're closer to your goal than you were yesterday."
    ]

    if task:

        title = task["title"]

        task_messages = [
            f"🔥 Ready for {title}? Let's finish it!",
            f"🎯 {title} is waiting. A focused session now can make your evening easier.",
            f"💪 You've got this! Let's complete {title}.",
            f"🚀 Start {title} now and keep your productivity streak alive."
        ]

        return random.choice(task_messages)

    return random.choice(messages)