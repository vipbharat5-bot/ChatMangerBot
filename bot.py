import os
import sqlite3
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "chat_manager.db")
TZ = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")

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
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS awards (
            chat_id INTEGER NOT NULL,
            award_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, award_type, period_key)
        )
    """)

    conn.commit()
    return conn


def local_now():
    return datetime.now(TZ)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def register_group(chat_id, title):
    conn = db()
    conn.execute(
        "INSERT INTO groups(chat_id, title, active) VALUES(?,?,1) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "title=excluded.title, active=1",
        (chat_id, title or ""),
    )
    conn.commit()
    conn.close()


def leaderboard(chat_id, start, end=None, limit=10):
    conn = db()

    query = """
        SELECT user_id,
               COALESCE(
                   NULLIF(username,''),
                   NULLIF(first_name,''),
                   'Member'
               ) AS name,
               COUNT(*) AS total
        FROM messages
        WHERE chat_id=? AND ts>=?
    """

    args = [chat_id, iso(start)]

    if end is not None:
        query += " AND ts<?"
        args.append(iso(end))

    query += (
        " GROUP BY user_id "
        "ORDER BY total DESC, user_id ASC LIMIT ?"
    )
    args.append(limit)

    rows = conn.execute(query, args).fetchall()
    conn.close()
    return rows


def escape_html(value):
    return (
        str(value or "Member")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_name(user_id, name):
    return (
        f'<a href="tg://user?id={user_id}">'
        f'{escape_html(name)}</a>'
    )


async def track_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if (
        not chat
        or chat.type not in ("group", "supergroup")
        or not user
        or user.is_bot
        or not message
    ):
        return

    register_group(chat.id, chat.title)

    conn = db()
    conn.execute(
        """
        INSERT INTO messages(
            chat_id, user_id, username, first_name, ts
        ) VALUES(?,?,?,?,?)
        """,
        (
            chat.id,
            user.id,
            user.username or "",
            user.first_name or "",
            iso(local_now()),
        ),
    )
    conn.commit()
    conn.close()


async def my_chat_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat = update.effective_chat
    change = update.my_chat_member

    if (
        not chat
        or chat.type not in ("group", "supergroup")
        or not change
    ):
        return

    status = change.new_chat_member.status

    if status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        register_group(chat.id, chat.title)

    elif status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    ):
        conn = db()
        conn.execute(
            "UPDATE groups SET active=0 WHERE chat_id=?",
            (chat.id,),
        )
        conn.commit()
        conn.close()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat = update.effective_chat
    message = update.effective_message

    if chat and chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)

        await message.reply_text(
            "🤖 <b>CHAT MANAGER RANKING BOT</b>\n\n"
            "✅ Activity tracking is ON!\n\n"
            "📊 /leaderboard — Today's ranking\n"
            "🏆 /rankings — View leaderboard\n"
            "🆔 /myid — Your Telegram ID",
            parse_mode="HTML",
        )
    else:
        await message.reply_text(
            "🤖 Chat Manager Ranking Bot\n\n"
            "Mujhe apne Telegram group mein add karo, "
            "phir group mein /start bhejo."
        )


async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID: "
        f"<code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


async def rankings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat = update.effective_chat

    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Ye command group mein use karo."
        )
        return

    now = local_now()
    start_time = datetime.combine(
        now.date(), time.min, tzinfo=TZ
    )

    rows = leaderboard(
        chat.id,
        start_time,
        now + timedelta(seconds=1),
        10,
    )

    if not rows:
        await update.effective_message.reply_text(
            "Aaj abhi ranking ke liye activity nahi mili. 🔥"
        )
        return

    lines = ["🏆 <b>TODAY'S ACTIVITY LEADERBOARD</b>\n"]

    for index, (uid, name, total) in enumerate(rows, 1):
        lines.append(
            f"{index}. {format_name(uid, name)} "
            f"— <b>{total}</b> messages"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


def award_already_sent(chat_id, award_type, period_key):
    conn = db()

    row = conn.execute(
        """
        SELECT 1 FROM awards
        WHERE chat_id=? AND award_type=? AND period_key=?
        """,
        (chat_id, award_type, period_key),
    ).fetchone()

    conn.close()
    return row is not None


def mark_award_sent(chat_id, award_type, period_key):
    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO awards(
            chat_id, award_type, period_key, sent_at
        ) VALUES(?,?,?,?)
        """,
        (
            chat_id,
            award_type,
            period_key,
            iso(local_now()),
        ),
    )

    conn.commit()
    conn.close()


async def send_award(
    context,
    chat_id,
    award_type,
    title,
    subtitle,
    start_time,
    end_time,
    emoji,
    period_key,
):
    if award_already_sent(chat_id, award_type, period_key):
        return

    rows = leaderboard(
        chat_id,
        start_time,
        end_time,
        1,
    )

    if not rows:
        return

    uid, name, total = rows[0]
    mention = format_name(uid, name)

    text = (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        f"{emoji} <b>{title}</b> {emoji}\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👑 Winner: {mention}\n"
        "🏆 <b>#1 Rank</b>\n"
        f"💬 <b>{total}</b> messages\n"
        f"{subtitle}\n\n"
        f"🎉 Congratulations {mention}!\n"
        "❤️ Keep the vibes alive! 🔥"
    )

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
    )

    mark_award_sent(chat_id, award_type, period_key)

    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except Exception:
        logger.exception(
            "Could not pin %s award in %s",
            award_type,
            chat_id,
        )


async def daily_awards(
    context: ContextTypes.DEFAULT_TYPE
):
    # Daily award at 9 PM IST uses the previous completed day.
    now = local_now()

    today_start = datetime.combine(
        now.date(), time.min, tzinfo=TZ
    )
    yesterday_start = today_start - timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)

    conn = db()
    chats = conn.execute(
        "SELECT chat_id FROM groups WHERE active=1"
    ).fetchall()
    conn.close()

    for (chat_id,) in chats:
        try:
            # Daily winner
            await send_award(
                context,
                chat_id,
                "daily",
                "MEMBER OF THE DAY",
                "🔥 Most Active Member — previous day",
                yesterday_start,
                today_start,
                "🌟",
                yesterday_start.date().isoformat(),
            )

            # Weekly winner: Sunday at 9 PM IST
            if now.weekday() == 6:
                week_start = today_start - timedelta(days=6)

                await send_award(
                    context,
                    chat_id,
                    "weekly",
                    "WEEKLY CHAMPION",
                    "🔥 Top Active Member This Week",
                    week_start,
                    tomorrow_start,
                    "🏆",
                    f"week-{week_start.date().isoformat()}",
                )

            # Monthly winner: last day of the month
            next_day = now.date() + timedelta(days=1)

            if next_day.month != now.month:
                month_start = datetime(
                    now.year, now.month, 1, tzinfo=TZ
                )

                await send_award(
                    context,
                    chat_id,
                    "monthly",
                    "MONTHLY LEGEND",
                    "💎 Top Active Member This Month",
                    month_start,
                    tomorrow_start,
                    "👑",
                    f"month-{now.year}-{now.month:02d}",
                )

        except Exception:
            logger.exception(
                "Award error for group %s",
                chat_id,
            )


async def broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    message_obj = update.effective_message

    if not OWNER_ID or not user or user.id != OWNER_ID:
        await message_obj.reply_text("⛔ Owner only.")
        return

    message = " ".join(context.args).strip()

    if not message:
        await message_obj.reply_text(
            "Use: /broadcast Your message here"
        )
        return

    conn = db()
    chats = conn.execute(
        "SELECT chat_id FROM groups WHERE active=1"
    ).fetchall()
    conn.close()

    sent = 0
    failed = 0

    for (chat_id,) in chats:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception(
                "Broadcast failed for group %s",
                chat_id,
            )

    await message_obj.reply_text(
        "📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}",
        parse_mode="HTML",
    )


async def groups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not OWNER_ID or not user or user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "⛔ Owner only."
        )
        return

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM groups WHERE active=1"
    ).fetchone()[0]

    conn.close()

    await update.effective_message.reply_text(
        f"📊 Registered active groups: <b>{count}</b>",
        parse_mode="HTML",
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logger.error("Bot error: %s", context.error)


def main():
    conn = db()
    conn.close()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", rankings))
    app.add_handler(CommandHandler("rankings", rankings))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groups", groups))
    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND,
            track_message,
        )
    )

    app.add_handler(
        ChatMemberHandler(
            my_chat_member,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

    app.add_error_handler(error_handler)

    # Automatic awards every day at 9 PM India time.
    app.job_queue.run_daily(
        daily_awards,
        time(hour=21, minute=0, tzinfo=TZ),
        name="automatic_activity_awards",
    )

    logger.info(
        "Chat Manager Ranking Bot starting. "
        "Awards scheduled for 21:00 IST."
    )

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
