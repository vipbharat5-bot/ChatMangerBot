
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "chat_manager.db")
IST = ZoneInfo("Asia/Kolkata")

RANK_BUTTONS = {
    "Daily Ranking": "daily",
    "Weekly Ranking": "weekly",
    "Overall Ranking": "overall",
}


# ---------- DATABASE ----------

def db():
    return sqlite3.connect(DB_PATH, timeout=30)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS groups_list (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_chat_time
            ON messages(chat_id, created_at)
        """)


def register_group(chat):
    if chat and chat.type in ("group", "supergroup"):
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO groups_list(chat_id, title) VALUES (?, ?)",
                (chat.id, chat.title or "Group"),
            )


# ---------- RANKING PERIODS ----------

def period_start(period):
    now = datetime.now(IST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "daily":
        return midnight.isoformat()
    if period == "weekly":
        return (midnight - timedelta(days=midnight.weekday())).isoformat()
    return None


def get_rankings(chat_id, period):
    start = period_start(period)

    with db() as conn:
        if start:
            rows = conn.execute("""
                SELECT user_id, name, username, COUNT(*) AS total
                FROM messages
                WHERE chat_id = ? AND created_at >= ?
                GROUP BY user_id
                ORDER BY total DESC, user_id
                LIMIT 10
            """, (chat_id, start)).fetchall()
        else:
            rows = conn.execute("""
                SELECT user_id, name, username, COUNT(*) AS total
                FROM messages
                WHERE chat_id = ?
                GROUP BY user_id
                ORDER BY total DESC, user_id
                LIMIT 10
            """, (chat_id,)).fetchall()

    return rows


def get_user_rank(chat_id, user_id, period):
    start = period_start(period)

    with db() as conn:
        if start:
            rows = conn.execute("""
                SELECT user_id, COUNT(*) AS total
                FROM messages
                WHERE chat_id = ? AND created_at >= ?
                GROUP BY user_id
                ORDER BY total DESC
            """, (chat_id, start)).fetchall()
        else:
            rows = conn.execute("""
                SELECT user_id, COUNT(*) AS total
                FROM messages
                WHERE chat_id = ?
                GROUP BY user_id
                ORDER BY total DESC
            """, (chat_id,)).fetchall()

    for place, row in enumerate(rows, 1):
        if row[0] == user_id:
            return place, row[1]

    return None, 0


async def show_ranking(update, period):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text(
            "Ranking ke liye apne group mein /ranking likho.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    register_group(chat)
    rows = get_rankings(chat.id, period)

    headings = {
        "daily": "🏆 DAILY RANKING",
        "weekly": "👑 WEEKLY RANKING",
        "overall": "💎 OVERALL RANKING",
    }

    lines = [headings[period], ""]

    if not rows:
        lines.append("Abhi koi messages record nahi hue.")
    else:
        for place, row in enumerate(rows, 1):
            name = row[2] and f"@{row[2]}" or row[1] or "Member"
            lines.append(f"{place}. {name} — {row[3]} messages")

    if user:
        place, total = get_user_rank(chat.id, user.id, period)
        lines.extend([
            "",
            "━━━━━━━━━━━━━━",
            f"Your rank: #{place}" if place else "Your rank: Not ranked yet",
            f"Your messages: {total}",
        ])

    await message.reply_text("\n".join(lines))
    await message.reply_text(
        "✅ Ranking displayed. Buttons removed.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ---------- COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type == "private":
        await update.effective_message.reply_text(
            "👋 Welcome to Chat Manager!\n\n"
            "Ranking dekhne ke liye apne group mein /ranking likho.\n\n"
            "/rank - Your rank\n"
            "/myid - Your Telegram ID",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        register_group(chat)
        await update.effective_message.reply_text(
            "🤖 Chat Manager is active!\n\n"
            "/ranking - Open ranking menu\n"
            "/rank - Your rank\n"
            "/myid - Your Telegram ID"
        )


async def ranking_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Ranking menu kholne ke liye apne group mein /ranking likho.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    register_group(chat)

    keyboard = [
        ["Daily Ranking", "Weekly Ranking"],
        ["Overall Ranking"],
    ]

    await update.effective_message.reply_text(
        "🏆 Choose a ranking:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
        ),
    )


async def ranking_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text
    period = RANK_BUTTONS.get(text)

    if not period:
        return

    await show_ranking(update, period)


async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Apni ranking dekhne ke liye group mein /rank likho."
        )
        return

    register_group(chat)
    lines = ["📊 YOUR RANK"]

    for period in ("daily", "weekly", "overall"):
        place, total = get_user_rank(chat.id, user.id, period)
        label = period.capitalize()
        rank_text = f"#{place}" if place else "Not ranked yet"
        lines.append(f"{label}: {rank_text} | Messages: {total}")

    await update.effective_message.reply_text("\n".join(lines))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Your Telegram ID: {update.effective_user.id}"
    )


async def groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ Owner only command.")
        return

    with db() as conn:
        rows = conn.execute(
            "SELECT chat_id, title FROM groups_list ORDER BY title"
        ).fetchall()

    lines = [f"📋 Registered groups: {len(rows)}", ""]
    for chat_id, title in rows[:100]:
        lines.append(f"{title} | {chat_id}")

    await update.effective_message.reply_text("\n".join(lines))


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ Owner only command.")
        return

    message = " ".join(context.args).strip()
    if not message:
        await update.effective_message.reply_text(
            "Use:\n/broadcast Your announcement here"
        )
        return

    with db() as conn:
        rows = conn.execute("SELECT chat_id FROM groups_list").fetchall()

    sent = failed = 0

    for (chat_id,) in rows:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            sent += 1
        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"📢 Broadcast finished.\nSent: {sent}\nFailed: {failed}"
    )


# ---------- ACTIVITY TRACKING ----------

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user or user.is_bot:
        return

    if chat.type not in ("group", "supergroup"):
        return

    # Ranking button presses should not count as ordinary chat activity.
    if message.text in RANK_BUTTONS:
        return

    register_group(chat)

    with db() as conn:
        conn.execute("""
            INSERT INTO messages
            (chat_id, user_id, name, username, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            chat.id,
            user.id,
            user.full_name,
            user.username,
            datetime.now(IST).isoformat(),
        ))


# ---------- RENDER HEALTH SERVER ----------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Chat Manager is running")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# ---------- MAIN ----------

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing in Render Environment.")
    if not OWNER_ID:
        raise RuntimeError("OWNER_ID missing in Render Environment.")

    init_db()

    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ranking", ranking_menu))
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groups", groups))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Register button handler before activity tracking.
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(
                r"^(Daily Ranking|Weekly Ranking|Overall Ranking)$"
            ),
            ranking_button,
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            track_message,
        ),
        group=1,
    )

    print("Chat Manager starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
