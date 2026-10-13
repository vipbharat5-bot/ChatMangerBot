import os
import sqlite3
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "chat_manager.db")
TZ = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            ts TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def now():
    return datetime.now(TZ)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def register_group(chat_id, title):
    conn = db()
    conn.execute("""
        INSERT INTO groups(chat_id, title, active)
        VALUES (?, ?, 1)
        ON CONFLICT(chat_id)
        DO UPDATE SET title=excluded.title, active=1
    """, (chat_id, title or "Telegram Group"))
    conn.commit()
    conn.close()


def active_groups():
    conn = db()
    rows = conn.execute(
        "SELECT chat_id, title FROM groups WHERE active=1 ORDER BY title"
    ).fetchall()
    conn.close()
    return rows


def keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📅 Daily Ranking"),
             KeyboardButton("🗓 Weekly Ranking")],
            [KeyboardButton("👑 Overall Ranking")]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose your ranking..."
    )


def group_keyboard():
    rows = [
        [InlineKeyboardButton(
            (title or "Telegram Group")[:40],
            callback_data=f"pick:{chat_id}"
        )]
        for chat_id, title in active_groups()
    ]
    return InlineKeyboardMarkup(rows) if rows else None


def bounds(kind):
    current = now()
    today = datetime.combine(
        current.date(), time.min, tzinfo=TZ
    )

    if kind == "daily":
        return today, today + timedelta(days=1)

    if kind == "weekly":
        monday = today - timedelta(days=current.weekday())
        return monday, monday + timedelta(days=7)

    return datetime(1970, 1, 1, tzinfo=TZ), None


def ranking(chat_id, kind, user_id):
    start, end = bounds(kind)

    conn = db()
    sql = """
        SELECT user_id,
        COALESCE(NULLIF(username,''), NULLIF(first_name,''), 'Member'),
        COUNT(*) AS total
        FROM messages
        WHERE chat_id=? AND ts>=?
    """
    args = [chat_id, iso(start)]

    if end:
        sql += " AND ts<?"
        args.append(iso(end))

    sql += " GROUP BY user_id ORDER BY total DESC, user_id ASC"
    rows = conn.execute(sql, args).fetchall()
    conn.close()

    titles = {
        "daily": "📅 DAILY RANKING",
        "weekly": "🗓 WEEKLY RANKING",
        "overall": "👑 OVERALL LIFETIME RANKING"
    }

    lines = [f"<b>{titles[kind]}</b>", "━━━━━━━━━━━━━━"]

    if not rows:
        lines.append("Abhi koi messages record nahi hue.")
    else:
        medals = ["🥇", "🥈", "🥉"]

        for position, (uid, name, count) in enumerate(rows[:10], 1):
            medal = medals[position - 1] if position <= 3 else f"{position}."
            safe_name = (
                str(name).replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            lines.append(
                f"{medal} <a href='tg://user?id={uid}'>{safe_name}</a>"
                f" — <b>{count}</b> messages"
            )

    own_rank = next(
        (i for i, row in enumerate(rows, 1) if row[0] == user_id),
        None
    )

    own_count = next(
        (row[2] for row in rows if row[0] == user_id),
        0
    )

    lines.append("━━━━━━━━━━━━━━")
    if own_rank:
        lines.append(
            f"📍 Your Rank: <b>#{own_rank}</b>\n"
            f"💬 Your Messages: <b>{own_count}</b>"
        )
    else:
        lines.append("📍 Your Rank: Not ranked yet. Group mein message bhejo!")

    if kind == "daily":
        lines.append("🔄 Reset: Daily at 12 AM IST")
    elif kind == "weekly":
        lines.append("🔄 Reset: Every Monday at 12 AM IST")
    else:
        lines.append("♾️ Lifetime ranking — never resets")

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message

    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        await msg.reply_text(
            "🤖 <b>CHAT MANAGER</b>\n\n"
            "Ranking system active!\n"
            "Daily, Weekly aur Overall ranking dekh sakte ho.",
            parse_mode="HTML",
            reply_markup=keyboard()
        )
        return

    groups = active_groups()

    await msg.reply_text(
        "👋 <b>Welcome to Chat Manager!</b>\n\n"
        "Niche ke buttons se ranking choose karo.",
        parse_mode="HTML",
        reply_markup=keyboard()
    )

    if groups:
        await msg.reply_text(
            "📍 Apna group select karo:",
            reply_markup=group_keyboard()
        )
    else:
        await msg.reply_text(
            "Pehle bot ko apne GC mein add karo aur wahan /start bhejo."
        )


async def show_private_ranking(update, context, chat_id, kind):
    user = update.effective_user
    allowed = {gid for gid, _ in active_groups()}

    if chat_id not in allowed:
        if update.callback_query:
            await update.callback_query.answer(
                "Group active nahi hai.", show_alert=True
            )
        return

    text = ranking(chat_id, kind, user.id)

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Daily", callback_data=f"rank:{chat_id}:daily"),
            InlineKeyboardButton("🗓 Weekly", callback_data=f"rank:{chat_id}:weekly"),
            InlineKeyboardButton("👑 Overall", callback_data=f"rank:{chat_id}:overall")
        ],
        [InlineKeyboardButton("🔁 Change Group", callback_data="groups")]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=buttons
        )
    else:
        await context.bot.send_message(
            chat_id=user.id, text=text,
            parse_mode="HTML", reply_markup=buttons
        )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "groups":
        await query.answer()
        await query.edit_message_text(
            "📍 Apna group select karo:",
            reply_markup=group_keyboard()
        )
        return

    if data.startswith("pick:"):
        chat_id = int(data.split(":", 1)[1])
        await show_private_ranking(update, context, chat_id, "daily")
        return

    if data.startswith("rank:"):
        _, chat_id, kind = data.split(":", 2)
        if kind in ("daily", "weekly", "overall"):
            await show_private_ranking(update, context, int(chat_id), kind)


async def text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choices = {
        "📅 Daily Ranking": "daily",
        "🗓 Weekly Ranking": "weekly",
        "👑 Overall Ranking": "overall"
    }

    text = (update.effective_message.text or "").strip()
    if text not in choices:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        await update.effective_message.reply_text(
            ranking(chat.id, choices[text], user.id),
            parse_mode="HTML"
        )
        return

    groups = active_groups()

    if len(groups) == 1:
        await show_private_ranking(
            update, context, groups[0][0], choices[text]
        )
    elif groups:
        await update.effective_message.reply_text(
            "Pehle group select karo:",
            reply_markup=group_keyboard()
        )
    else:
        await update.effective_message.reply_text(
            "Bot ko pehle apne group mein add karo."
        )


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user or user.is_bot:
        return

    if chat.type not in ("group", "supergroup"):
        return

    if msg.text and msg.text.startswith("/"):
        return

    register_group(chat.id, chat.title)

    conn = db()
    conn.execute("""
        INSERT INTO messages(chat_id, user_id, username, first_name, ts)
        VALUES (?, ?, ?, ?, ?)
    """, (
        chat.id, user.id, user.username,
        user.first_name, iso(now())
    ))
    conn.commit()
    conn.close()


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Your Telegram ID: <code>{update.effective_user.id}</code>",
        parse_mode="HTML"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not OWNER_ID or user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ Owner only.")
        return

    message = " ".join(context.args).strip()
    if not message:
        await update.effective_message.reply_text(
            "Usage: /broadcast Your message"
        )
        return

    sent = 0
    failed = 0

    for chat_id, _ in active_groups():
        try:
            await context.bot.send_message(chat_id, message)
            sent += 1
        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"📢 Broadcast complete\n✅ Sent: {sent}\n❌ Failed: {failed}"
    )


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ Owner only.")
        return

    await update.effective_message.reply_text(
        f"Registered groups: {len(active_groups())}"
    )


async def member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    change = update.my_chat_member

    if not chat or not change:
        return

    status = change.new_chat_member.status

    if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        register_group(chat.id, chat.title)
    elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        conn = db()
        conn.execute("UPDATE groups SET active=0 WHERE chat_id=?", (chat.id,))
        conn.commit()
        conn.close()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Chat Manager running")

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()


def main():
    conn = db()
    conn.close()

    try:
        start_health_server()
    except Exception:
        logger.exception("Health server failed")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("leaderboard", text_buttons))
    app.add_handler(CommandHandler("rankings", text_buttons))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("groups", groups_command))

    app.add_handler(
        CallbackQueryHandler(callbacks, pattern=r"^(pick:|rank:|groups$)")
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_buttons),
        group=0
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND,
            track_message
        ),
        group=1
    )

    app.add_handler(
        ChatMemberHandler(member_update, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
