# Databricks prep — Telegram quiz bot

A Telegram bot that quizzes anyone who presses **/start** on the Databricks Certified Data Engineer Professional exam (version effective 9 Oct 2026). It runs on GitHub Actions; there is no server.

## What users get

- **/start**: a greeting and the first question.
- Under every question there is a **«✅ Правильный ответ и почему»** button: the correct option, the explanation and links to docs.databricks.com.
- After the answer there is a **«➡️ Ещё вопрос»** button for the next unseen question.
- Every day from 10:00 (Europe/Belgrade) each subscriber who has not received a question that day gets one automatically.
- **/question** sends the next question, and **/stop** turns off the daily messages.

## Timing (private repo)

The workflow wakes up every 30 minutes. That keeps it within the 2000 free Actions minutes a month for private repos, so replies arrive with a delay of up to ~30–40 minutes. Making the repo public would give unlimited minutes: then switch the workflow to `python bot.py run --minutes 340` with an hourly cron, and replies become near-instant.

## Setup

Settings → Secrets and variables → Actions → `TG_BOT_SECRET` = bot token from @BotFather.

Progress (who has seen which question) is saved **encrypted** (key derived from the bot token) as `state.enc` on the `bot-state` branch.

## Adding questions

See `questions/README.md`.
