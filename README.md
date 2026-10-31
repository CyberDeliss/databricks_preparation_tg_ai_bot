# Databricks prep — Telegram quiz bot

A Telegram bot with practice questions for the Databricks Certified Data Engineer Professional exam (version effective 9 Oct 2026). It runs on GitHub Actions; there is no server and **no data about users is stored anywhere**.

## What users get

- **/start**: a greeting and question 1.
- Under every question there is a **«✅ Правильный ответ и почему»** button: the correct option, the explanation and links to docs.databricks.com.
- After the answer there is a **«➡️ Ещё вопрос»** button for the next question. The question id travels inside the button, so no progress is stored.
- **/question**: today's question (the same for everyone on a given day).
- Every day at **10:00 (Europe/Belgrade)** today's question goes to every chat id in the `TG_CHAT_ID` secret.

## Secrets (Settings → Secrets and variables → Actions)

| Name | What |
|---|---|
| `TG_BOT_SECRET` | Bot token from @BotFather |
| `TG_CHAT_ID` | Chat ids for the daily question, comma-separated: `123456789, 987654321` |

To find a chat id, have the person message the bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` **before the bot's next run** and look for `"chat":{"id":...}`. Alternatively, they can ask @userinfobot.

## Timing (private repo)

The bot checks for messages every 30 minutes to stay within the free 2000 Actions minutes a month, so replies take up to ~30–40 minutes. GitHub cron is UTC-only, so two daily crons (08:00 and 09:00 UTC) run and the bot sends only on the one that is 10:00 in Belgrade.

## Adding questions

See `questions/README.md`.
