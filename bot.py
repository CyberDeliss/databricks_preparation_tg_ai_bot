"""Telegram quiz bot for the Databricks Certified Data Engineer Professional exam.

Runs inside GitHub Actions and stores NOTHING about users:

  python bot.py run 340 - long-lived worker for N minutes (replies within seconds)
  python bot.py poll    - answer everything people sent since the last run, then exit
  python bot.py daily   - send today's question to every chat id listed in TG_CHAT_ID
  python bot.py check   - validate the question files

Behaviour
  /start              -> greeting + question 1
  /question           -> today's question
  "answer" button     -> correct option + explanation, then a "next question" button
  "next" button       -> the question after the one it was pressed under (the id travels
                         inside the button, so no per-user progress is needed)

Telegram itself remembers which updates the bot has already fetched (getUpdates with
offset confirms them), so there is no local state at all.

Environment
  TG_BOT_SECRET  bot token from @BotFather (required)
  TG_CHAT_ID     comma-separated chat ids that get the daily question (daily mode)
  SCHEDULE       the cron string that started the run (daily mode, set by the workflow)
"""

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
QUESTIONS_DIR = ROOT / "questions"
TZ = ZoneInfo("Europe/Belgrade")
DAILY_HOUR = 10
FIRST_DAY = dt.date(2026, 11, 1)  # the day question 1 is the daily question
TG_LIMIT = 4096

TOKEN = os.environ.get("TG_BOT_SECRET", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

BTN_ANSWER = "✅ Правильный ответ и почему"
BTN_NEXT = "➡️ Ещё вопрос"


# ---------------------------------------------------------------- Telegram API
class TelegramError(Exception):
    pass


def tg(method, http_timeout=30, **params):
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
    ).encode()
    try:
        with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=http_timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = json.load(exc)
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('error_code')} {body.get('description')}")
    return body["result"]


def keyboard(text, data):
    return {"inline_keyboard": [[{"text": text, "callback_data": data}]]}


def send(chat_id, text, **params):
    try:
        tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
           disable_web_page_preview=True, **params)
    except TelegramError as exc:
        print(f"a send failed: {exc}")


def drop_buttons(chat_id, message_id):
    try:
        tg("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
           reply_markup={"inline_keyboard": []})
    except TelegramError:
        pass


# ---------------------------------------------------------------- questions
def load_questions():
    questions = []
    for path in sorted(QUESTIONS_DIR.glob("*.json")):
        q = json.loads(path.read_text(encoding="utf-8"))
        q.setdefault("id", path.stem)
        questions.append(q)
    return questions


def esc(text):
    return html.escape(text, quote=False)


def render_question(q, number, total):
    lines = [f"<b>Вопрос {number}/{total}</b> · {esc(q.get('topic', ''))}", "", esc(q["question"]), ""]
    for letter, option in zip("ABCDEFG", q["options"]):
        lines.append(f"<b>{letter})</b> {esc(option)}")
    return "\n".join(lines)[:TG_LIMIT]


def render_answer(q):
    letter = q["correct"]
    option = q["options"]["ABCDEFG".index(letter)]
    lines = [f"<b>Правильный ответ: {letter})</b> {esc(option)}", "", esc(q["explanation"])]
    if q.get("sources"):
        lines += ["", "<b>Источники:</b>", *map(esc, q["sources"])]
    return "\n".join(lines)[:TG_LIMIT]


def send_question(chat_id, index):
    questions = load_questions()
    if not questions:
        send(chat_id, "Вопросов пока нет — загляни позже.")
        return
    index %= len(questions)
    q = questions[index]
    send(chat_id, render_question(q, index + 1, len(questions)),
         reply_markup=keyboard(BTN_ANSWER, f"ans:{q['id']}"))


def index_of(qid):
    ids = [q["id"] for q in load_questions()]
    return ids.index(qid) if qid in ids else None


def todays_index():
    return (dt.datetime.now(TZ).date() - FIRST_DAY).days


# ---------------------------------------------------------------- modes
def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip().lower()
    if text.startswith("/start"):
        send(chat_id,
             "Привет! Я задаю вопросы для подготовки к экзамену "
             "<b>Databricks Certified Data Engineer Professional</b>.\n\n"
             "Под каждым вопросом — кнопка с правильным ответом и объяснением, "
             "а после неё — «Ещё вопрос». /question — вопрос дня.\n\nНачнём 👇")
        send_question(chat_id, 0)
    elif text.startswith("/question"):
        send_question(chat_id, todays_index())
    else:
        send(chat_id, "Нажми /question, чтобы получить вопрос дня.")


def handle_callback(cb):
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"])
    except TelegramError as exc:
        print(f"answerCallbackQuery failed: {exc}")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return
    kind, _, qid = (cb.get("data") or "").partition(":")
    drop_buttons(chat_id, msg["message_id"])
    i = index_of(qid)
    if i is None:
        send(chat_id, "Этот вопрос больше не найден.")
        send_question(chat_id, todays_index())
    elif kind == "ans":
        send(chat_id, render_answer(load_questions()[i]),
             reply_markup=keyboard(BTN_NEXT, f"next:{qid}"))
    elif kind == "next":
        send_question(chat_id, i + 1)


def process(updates):
    for upd in updates:
        try:
            if "callback_query" in upd:
                handle_callback(upd["callback_query"])
            elif "chat" in (upd.get("message") or {}):
                handle_message(upd["message"])
        except Exception as exc:  # one bad update must not block the rest
            print(f"update {upd['update_id']} failed: {exc!r}")


def poll():
    """One-shot: handle everything queued since the last run, then exit."""
    tg("deleteWebhook")  # getUpdates does not work while a webhook is set
    while True:
        updates = tg("getUpdates", timeout=0, allowed_updates=["message", "callback_query"])
        if not updates:
            break
        process(updates)
        # confirming with offset = last + 1 makes Telegram forget these updates
        tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)
    print("poll done")


def run(minutes):
    """Long-lived worker: long-poll Telegram so replies arrive within seconds.

    Used in the public repo (unlimited Actions minutes). The workflow starts a new worker
    every hour; concurrency keeps exactly one running and one queued, so when a worker
    reaches its time limit the queued one takes over almost immediately.
    """
    tg("deleteWebhook")
    deadline = time.monotonic() + minutes * 60
    offset = None
    while time.monotonic() < deadline:
        wait = int(max(1, min(50, deadline - time.monotonic())))
        params = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = tg("getUpdates", http_timeout=wait + 15, **params)
        except Exception as exc:
            print(f"getUpdates failed: {exc!r}; retrying in 10s")
            time.sleep(10)
            continue
        if updates:
            process(updates)
            offset = updates[-1]["update_id"] + 1
    if offset is not None:  # confirm the last batch so the next worker doesn't repeat it
        tg("getUpdates", offset=offset, timeout=0)
    print("worker finished")


def is_daily_slot(schedule):
    """True if the cron that started this run is the one matching 10:00 in Belgrade today.

    GitHub cron is UTC-only, so the workflow has two daily crons (08:00 and 09:00 UTC);
    exactly one of them is 10:00 Belgrade depending on summer/winter time. Checking the
    cron string rather than the clock means a delayed run still sends exactly once.
    """
    m = re.match(r"^\s*(\d+)\s+(\d+)\s", schedule or "")
    if not m:
        return True  # manual run: send
    today = dt.datetime.now(TZ).date()
    target_utc = dt.datetime.combine(today, dt.time(DAILY_HOUR), TZ).astimezone(dt.timezone.utc)
    return int(m.group(2)) == target_utc.hour


def daily():
    if not is_daily_slot(os.environ.get("SCHEDULE")):
        print("not the 10:00 Belgrade slot today; skipping")
        return
    chat_ids = [c for c in re.split(r"[\s,;]+", os.environ.get("TG_CHAT_ID", "")) if c]
    if not chat_ids:
        print("TG_CHAT_ID is empty; nobody to send to")
        return
    for chat_id in chat_ids:
        send_question(chat_id, todays_index())
    print(f"daily question sent to {len(chat_ids)} chat(s)")


def check():
    ok, ids = True, set()
    for q in load_questions():
        problems = [f"missing {f}" for f in ("question", "options", "correct", "explanation") if not q.get(f)]
        if not problems and q["correct"] not in "ABCDEFG"[: len(q["options"])]:
            problems.append("correct letter out of range")
        if q["id"] in ids:
            problems.append("duplicate id")
        if len(f"next:{q['id']}".encode()) > 64:
            problems.append("id too long for a Telegram button (max ~59 chars)")
        ids.add(q["id"])
        if not problems and (len(render_question(q, 1, 1)) >= TG_LIMIT or len(render_answer(q)) >= TG_LIMIT):
            problems.append("too long for Telegram")
        if problems:
            ok = False
            print(f"{q['id']}: {', '.join(problems)}")
    print(f"{len(ids)} questions checked")
    return ok


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "poll"
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 340
    if mode == "check":
        sys.exit(0 if check() else 1)
    if not TOKEN:
        sys.exit("TG_BOT_SECRET is not set")
    if mode == "run":
        run(minutes)
    else:
        {"poll": poll, "daily": daily}[mode]()
