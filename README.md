# Chat Manger

Telegram group-management/activity bot.

## Features
- Daily Member of the Day
- Sunday Weekly Champion
- Last-day-of-month Monthly Legend
- Automatic award-message pinning (bot must have permission to pin)
- Message-based activity leaderboard
- Owner-only broadcast to registered groups
- `/groups` owner stats
- `/myid` to get your numeric Telegram ID
- SQLite persistence

## Environment
`BOT_TOKEN` = BotFather token (keep secret)
`OWNER_ID` = your numeric Telegram user ID
`DB_PATH` = optional SQLite path

## Setup
1. Add the bot to your group and make it admin.
2. Give it permission to send messages and pin messages.
3. In BotFather, turn Group Privacy OFF so normal group messages can be received.
4. Run `/start` in the group.
5. Run `/myid` from your own Telegram account, then put that number in `OWNER_ID`.
6. Use `/broadcast Your message` from the owner account.

## Commands
`/start`
`/leaderboard`
`/rankings`
`/myid`
`/broadcast Your message` (owner only)
`/groups` (owner only)

## Broadcast behavior
`/broadcast Your message` is owner-only. It sends the message to every active group where Chat Manger is currently registered. A group is registered automatically when the bot is added, and marked inactive when the bot is removed or a send fails.

## Note
The ranking is based on messages tracked by this bot. It does not read or depend on ChatFightBot's private/internal ranking data.
