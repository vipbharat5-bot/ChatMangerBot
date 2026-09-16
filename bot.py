import os
import sqlite3
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, ChatMemberHandler, CommandHandler, MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "chat_manager.db")
TZ = ZoneInfo("Asia/Kolkata")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
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

def register_group(chat_id, title):
    conn = db()
    conn.execute(
        "INSERT INTO groups(chat_id,title,active) VALUES(?,?,1) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, active=1",
        (chat_id, title or "")
    )
    conn.commit()
    conn.close()

def local_now():
    return datetime.now(TZ)

def iso(dt):
    return dt.astimezone(ZoneInfo("UTC")).isoformat()

def period_name(dt):
    return dt.strftime("%d %b %Y")

def leaderboard(chat_id, start, end=None, limit=10):
    conn = db()
    if end is None:
        rows = conn.execute("""
            SELECT user_id,
                   COALESCE(NULLIF(username,''), first_name, 'Member') AS name,
                   COUNT(*) AS total
            FROM messages
            WHERE chat_id=? AND ts>=?
            GROUP BY user_id
            ORDER BY total DESC, user_id ASC
            LIMIT ?
        """, (chat_id, iso(start), limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT user_id,
                   COALESCE(NULLIF(username,''), first_name, 'Member') AS name,
                   COUNT(*) AS total
            FROM messages
            WHERE chat_id=? AND ts>=? AND ts<?
            GROUP BY user_id
            ORDER BY total DESC, user_id ASC
            LIMIT ?
        """, (chat_id, iso(start), iso(end), limit)).fetchall()
    conn.close()
    return rows

def format_name(user_id, name):
    safe = (name or "Member").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup") or not user or user.is_bot:
        return

    register_group(chat.id, chat.title)
    conn = db()
    conn.execute(
        "INSERT INTO messages(chat_id,user_id,username,first_name,ts) VALUES(?,?,?,?,?)",
        (chat.id, user.id, user.username, user.first_name or "", iso(datetime.now(TZ)))
    )
    conn.commit()
    conn.close()

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    change = update.my_chat_member
    if not chat or chat.type not in ("group", "supergroup") or not change:
        return

    # Register immediately when the bot becomes a group member/admin.
    new_status = change.new_chat_member.status
    if new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        register_group(chat.id, chat.title)
    elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        conn = db()
        conn.execute("UPDATE groups SET active=0 WHERE chat_id=?", (chat.id,))
        conn.commit()
        conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        register_group(chat.id, chat.title)
        await update.message.reply_text(
            "🤖 <b>Chat Manger</b> is active!\n\n"
            "🔥 Activity tracking is ON.\n"
            "🏆 /leaderboard — today's ranking\n"
            "🆔 /myid — your Telegram ID",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "🤖 Chat Manger\n\nAdd me to your group as an admin and use /start there."
        )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your Telegram ID: <code>{update.effective_user.id}</code>", parse_mode="HTML")

async def rankings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Ye command group mein use karo.")
        return

    now = local_now()
    start = datetime.combine(now.date(), time.min, tzinfo=TZ)
    rows = leaderboard(chat.id, start)

    if not rows:
        await update.message.reply_text("Aaj abhi ranking ke liye activity nahi mili. 🔥")
        return

    text = ["🏆 <b>DAILY LEADERBOARD</b>\n"]
    for i, (uid, name, total) in enumerate(rows, 1):
        text.append(f"{i}. {format_name(uid, name)} — <b>{total}</b> msgs")
    await update.message.reply_text("\n".join(text), parse_mode="HTML")

async def send_award(chat_id, context, title, subtitle, start, end, emoji):
    rows = leaderboard(chat_id, start, end, 1)
    if not rows:
        return False

    uid, name, total = rows[0]
    mention = format_name(uid, name)
    text = (
        f"╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        f"{emoji} <b>{title}</b> {emoji}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👑 Today's/Period Star: {mention}\n\n"
        f"🏆 <b>#1 Rank</b>\n"
        f"💬 <b>{total}</b> messages\n"
        f"{subtitle}\n\n"
        f"🎉 Congratulations {mention}!\n"
        f"❤️ Keep the vibes alive! 🔥"
    )
    msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
    except Exception as e:
        print("Could not pin award:", e)
    return True

async def daily_awards(context: ContextTypes.DEFAULT_TYPE):
    now = local_now()
    today_start = datetime.combine(now.date(), time.min, tzinfo=TZ)
    tomorrow = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)

    conn = db()
    chats = conn.execute("SELECT chat_id FROM groups WHERE active=1").fetchall()
    conn.close()

    for (chat_id,) in chats:
        try:
            # Award the completed day.
            rows = leaderboard(chat_id, yesterday_start, today_start, 1)
            if rows:
                uid, name, total = rows[0]
                mention = format_name(uid, name)
                text = (
                    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
                    "🌟 <b>MEMBER OF THE DAY</b> 🌟\n"
                    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                    f"👤 Today's Star: {mention}\n\n"
                    f"🏆 <b>#1 Daily Rank</b>\n"
                    f"💬 <b>{total}</b> messages\n"
                    "🔥 Most Active Member\n\n"
                    f"🎉 Congratulations {mention}!\n"
                    "❤️ Keep the vibes alive! 🔥"
                )
                msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                try:
                    await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                except Exception as e:
                    print("Daily pin failed:", e)

            # Sunday night: announce the completed Monday-Sunday week.
            if now.weekday() == 6:
                week_start = today_start - timedelta(days=6)
                await send_award(
                    chat_id, context, "WEEKLY CHAMPION",
                    "🔥 Top Active Member This Week", week_start, tomorrow, "🏆"
                )

            # Last calendar day of the month: announce the completed month.
            next_day = now.date() + timedelta(days=1)
            if next_day.month != now.month:
                month_start = datetime(now.year, now.month, 1, tzinfo=TZ)
                await send_award(
                    chat_id, context, "MONTHLY LEGEND",
                    "💎 Top Active Member This Month", month_start, tomorrow, "👑"
                )
        except Exception as e:
            print(f"Award error for {chat_id}: {e}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ONLY the configured owner can use this command.
    if not OWNER_ID or update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return

    message = " ".join(context.args).strip()
    if not message:
        await update.message.reply_text("Use: /broadcast Your message here")
        return

    conn = db()
    rows = conn.execute("SELECT chat_id FROM groups WHERE active=1").fetchall()
    conn.close()

    sent, failed = 0, 0
    for (chat_id,) in rows:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            sent += 1
        except Exception as e:
            failed += 1
            conn = db()
            conn.execute("UPDATE groups SET active=0 WHERE chat_id=?", (chat_id,))
            conn.commit()
            conn.close()
            print("Broadcast failed:", chat_id, e)

    await update.message.reply_text(
        f"📢 <b>Broadcast Complete</b>\n\n✅ Sent: {sent}\n❌ Failed/removed: {failed}",
        parse_mode="HTML"
    )

async def groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM groups WHERE active=1").fetchone()[0]
    conn.close()
    await update.message.reply_text(f"📊 Registered active groups: <b>{count}</b>", parse_mode="HTML")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("Bot error:", context.error)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", rankings))
    app.add_handler(CommandHandler("rankings", rankings))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groups", groups))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, track_message))
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(error_handler)

    # Runs daily at 23:59 IST. It announces the day that just ended.
    app.job_queue.run_daily(daily_awards, time(hour=23, minute=59, tzinfo=TZ))

    print("Chat Manger is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
