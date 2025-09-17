from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import random
import re
import time
import os

# ---------------------------
# Question bank (no repeats per round)
# ---------------------------
questions = [
    {"question": "What is the formula of a hydrogen ion?", "answer": "H +"},
    {"question": "What is the formula of a lithium ion?", "answer": "Li +"},
    {"question": "What is the formula of a sodium ion?", "answer": "Na +"},
    {"question": "What is the formula of a potassium ion?", "answer": "K +"},
    {"question": "What is the formula of a rubidium ion?", "answer": "Rb +"},
    {"question": "What is the formula of a magnesium ion?", "answer": "Mg 2+"},
    {"question": "What is the formula of a calcium ion?", "answer": "Ca 2+"},
    {"question": "What is the formula of a strontium ion?", "answer": "Sr 2+"},
    {"question": "What is the formula of a barium ion?", "answer": "Ba 2+"},
    {"question": "What is the formula of an aluminium ion?", "answer": "Al 3+"},
    {"question": "What is the formula of an iron(II) ion?", "answer": "Fe 2+"},
    {"question": "What is the formula of an iron(III) ion?", "answer": "Fe 3+"},
    {"question": "What is the formula of a copper(I) ion?", "answer": "Cu +"},
    {"question": "What is the formula of a copper(II) ion?", "answer": "Cu 2+"},
    {"question": "What is the formula of a zinc ion?", "answer": "Zn 2+"},
    {"question": "What is the formula of a silver ion?", "answer": "Ag +"},
    {"question": "What is the formula of a fluoride ion?", "answer": "F -"},
    {"question": "What is the formula of a chloride ion?", "answer": "Cl -"},
    {"question": "What is the formula of a bromide ion?", "answer": "Br -"},
    {"question": "What is the formula of a iodide ion?", "answer": "I -"},
    {"question": "What is the formula of a oxide ion?", "answer": "O 2-"},
    {"question": "What is the formula of a sulfide ion?", "answer": "S 2-"},
    {"question": "What is the formula of a nitride ion?", "answer": "N 3-"},
    {"question": "What is the formula of a phosphide ion?", "answer": "P 3-"},
    {"question": "What is the formula of an ammonium ion?", "answer": "NH4 +"},
    {"question": "What is the formula of a nitrate ion?", "answer": "NO3 -"},
    {"question": "What is the formula of a hydroxide ion?", "answer": "OH -"},
    {"question": "What is the formula of a carbonate ion?", "answer": "CO3 2-"},
    {"question": "What is the formula of a sulfate ion?", "answer": "SO4 2-"},
]


ROUND_LENGTH = 15           # questions per round
PER_QUESTION_SECONDS = 15   # seconds per question

# ---------------------------
# Helpers
# ---------------------------

def cancel_jobs(context: ContextTypes.DEFAULT_TYPE):
    """Cancel tick + timeout jobs for the current chat if they exist."""
    for key in ("tick_job", "timeout_job"):
        job = context.chat_data.get(key)
        if job:
            try:
                job.schedule_removal()
            except Exception:
                pass
            context.chat_data[key] = None

# Wrapper for JobQueue to call async times_up
def times_up_sync(context: ContextTypes.DEFAULT_TYPE):
    context.application.create_task(times_up(context))

# ---------------------------
# Commands
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Ion Formula Quiz!\n\n"
        f"Each answer should have the formula of the ion as well as its charge, separated by a space.\n\n"
        f"For example, the carbonate ion will be written as 'CO3 2-'\n\n"
        f"You are strongly encouraged to have a Periodic Table with you!\n\n"
        f"Type /quiz to start a {ROUND_LENGTH}-question round.\n\n"
        f"You will be given {PER_QUESTION_SECONDS}s per question. First to answer correctly scores!"
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a new round (group-friendly)."""
    chat_id = update.effective_chat.id

    # Prevent starting a new quiz if one is already active
    if context.chat_data.get("question_active"):
        await update.message.reply_text("⚠️ A quiz is already in progress! Please wait for it to finish.")
        return

    # Round state (per-chat for multiplayer)
    context.chat_data.clear()
    context.chat_data.update({
        "scores": {},                # user_id -> points
        "names": {},                 # user_id -> latest display name
        "q_count": 0,
        "remaining_qs": random.sample(questions, len(questions)),  # no repeats
        "current_q": None,
        "question_active": False,
        "solved_by": None,
        "time_left": PER_QUESTION_SECONDS,
        "countdown_msg_id": None,
        "tick_job": None,
        "timeout_job": None,
    })
    await send_question(chat_id, context)

# ---------------------------
# Core flow
# ---------------------------

# Synchronous wrapper for JobQueue to call async tick
def tick_sync(context: ContextTypes.DEFAULT_TYPE):
    context.application.create_task(tick(context))

async def send_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Send next question or finish the round."""
    # End condition
    if context.chat_data.get("q_count", 0) >= ROUND_LENGTH or not context.chat_data.get("remaining_qs"):
        await send_final_score(chat_id, context)
        return

    # Cancel any previous tick/timeout jobs
    cancel_jobs(context)

    # Prepare next question
    question = context.chat_data["remaining_qs"].pop(0)
    context.chat_data["current_q"] = question
    context.chat_data["q_count"] += 1
    context.chat_data["question_active"] = True
    context.chat_data["solved_by"] = None
    context.chat_data["time_left"] = PER_QUESTION_SECONDS

    # Send the question with initial countdown
    qnum = context.chat_data["q_count"]
    msg = await context.bot.send_message(
        chat_id,
        f"Q{qnum}: {question['question']}\n\n⏳ {PER_QUESTION_SECONDS}s left… First correct answer scores!"
    )
    context.chat_data["countdown_msg_id"] = msg.message_id

    # Schedule tick (every 1 second) and timeout
    context.chat_data["tick_job"] = context.application.job_queue.run_repeating(
    tick, interval=1, first=1, chat_id=chat_id
    )
    context.chat_data["timeout_job"] = context.application.job_queue.run_once(
    times_up_sync, when=PER_QUESTION_SECONDS, chat_id=chat_id
    )

async def tick(context: ContextTypes.DEFAULT_TYPE):
    """Update the countdown message only at 15s, 10s, and 5s left."""
    chat_id = context.job.chat_id

    # Stop ticking if question is already solved
    if not context.chat_data.get("question_active", False):
        try:
            context.job.schedule_removal()
        except Exception:
            pass
        context.chat_data["tick_job"] = None
        return

    # Decrement time left
    left = context.chat_data.get("time_left", PER_QUESTION_SECONDS) - 1
    context.chat_data["time_left"] = left

    # Only update the countdown message at 15, 10, or 5 seconds
    if left in [15, 10, 5]:
        msg_id = context.chat_data.get("countdown_msg_id")
        q = context.chat_data.get("current_q", {})
        qnum = context.chat_data.get("q_count", 0)
        if msg_id and q:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"Q{qnum}: {q['question']}\n\n⏳ {left}s left… Quickly!"
                )
            except Exception:
                pass

    # Stop ticking at 0; the timeout job will handle the transition
    if left <= 0:
        try:
            context.job.schedule_removal()
        except Exception:
            pass
        context.chat_data["tick_job"] = None

async def times_up(context: ContextTypes.DEFAULT_TYPE):
    """Handle when time runs out."""
    chat_id = context.job.chat_id

    # Only trigger if question is still active
    if not context.chat_data.get("question_active", False):
        return

    # Mark question as inactive
    context.chat_data["question_active"] = False

    # Cancel any remaining jobs safely
    cancel_jobs(context)

    # Send the correct answer
    answer = context.chat_data.get("current_q", {}).get("answer", "?")
    await context.bot.send_message(chat_id, f"⏰ Time's up! The correct answer is {answer}")

    # Move to next question
    await send_question(chat_id, context)

# ---------------------------
# Answers
# ---------------------------
async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept the FIRST correct answer only, award point, then move on immediately."""
    if "current_q" not in context.chat_data:
        return  # No active round

    if not context.chat_data.get("question_active", False):
        return  # Waiting for next question

    # If someone already solved this question, ignore
    if context.chat_data.get("solved_by") is not None:
        return

    user_id = update.effective_user.id
    user_name = (update.effective_user.full_name or update.effective_user.first_name).strip()
    context.chat_data["names"][user_id] = user_name

    # Direct comparison without normalisation
    user_text = update.message.text.strip()
    correct_text = context.chat_data["current_q"]["answer"].strip()

    if user_text == correct_text:
        # Mark question as solved
        context.chat_data["solved_by"] = user_id
        context.chat_data["question_active"] = False

        # Award point to this user
        scores = context.chat_data["scores"]
        scores[user_id] = scores.get(user_id, 0) + 1

        # Cancel countdown and timeout jobs immediately
        cancel_jobs(context)

        # Announce winner and their score
        display_score = scores[user_id]
        await update.message.reply_text(
            f"✅ {user_name} got it first! (+1) Current points: {display_score}"
        )

        # Move to next question immediately
        await send_question(update.effective_chat.id, context)

    # No response for wrong answers

# ---------------------------
# Final scoreboard
# ---------------------------
async def send_final_score(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    scores = context.chat_data.get("scores", {})
    names = context.chat_data.get("names", {})
    if not scores:
        await context.bot.send_message(chat_id, "🏁 Round finished! Nobody scored any points :(")
    else:
        # Sort by points desc, then name
        sorted_items = sorted(scores.items(), key=lambda kv: (-kv[1], names.get(kv[0], '')))
        lines = [f"{names.get(uid, str(uid))}: {pts}" for uid, pts in sorted_items]
        board = "\n".join(lines)
        await context.bot.send_message(chat_id, f"🏁 Round Finished ({ROUND_LENGTH} questions)!\n\n🏆 Final Scoreboard:\n{board}")

    # Clean up round state
    cancel_jobs(context)
    context.chat_data.clear()

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    # Put your token in quotes:
    TOKEN = os.environ.get("BOT_TOKEN")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))

    app.run_polling()
