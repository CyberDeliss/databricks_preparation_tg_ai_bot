"""Telegram quiz bot for the Databricks Certified Data Engineer Professional exam.

Runs as a long-lived worker inside GitHub Actions (no server needed):

  python bot.py run --minutes 340

Behaviour
  /start, /question   -> subscribe the chat and send the next unseen question
  button "answer"     -> correct option + explanation, then a "next question" button
  button "next"       -> next unseen question
  every day at DAILY_HOUR (Europe/Belgrade) every subscriber who has not received
  a question that day gets one automatically, so everyone gets at least 1 per day.

Questions live in questions/*.json (see questions/README.md for the format).
Per-user progress is stored encrypted (key derived from the bot token) in the file
state.enc on the `bot-state` branch, so nothing personal is readable in a public repo.

Only the standard library plus `cryptography` is used. Environment:
  TG_BOT_SECRET  bot token from @BotFather (required)
  STATE_FILE     path of the encrypted state file (default: state.enc)
"""

import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).parent
QUESTIONS_DIR = ROOT / "questions"
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.enc"))
TZ = ZoneInfo("Europe/Belgrade")
DAILY_HOUR = 10
TG_LIMIT = 4096

TOKEN = os.environ.get("TG_BOT_SECRET", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

BTN_ANSWER = "✅ Правильный ответ и почему"
BTN_NEXT = "➡️ Ещё вопрос"


# ---------------------------------------------------------------- Telegram API
class TelegramError(Exception):
    def __init__(self, code, description):
        super().__init__(f"{code}: {description}")
        self.code = code


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
        raise TelegramError(body.get("error_code"), body.get("description"))
    return body["result"]


def keyboard(*buttons):
    return {"inline_keyboard": [[{"text": text, "callback_data": data}] for text, data in buttons]}


# ---------------------------------------------------------------- questions
def load_questions():
    questions = []
    for path in sorted(QUESTIONS_DIR.glob("*.json")):
        q = json.loads(path.read_text(encoding="utf-8"))
        q.setdefault("id", path.stem)
        questions.append(q)
    return questions


def render_question(q, number, total):
    lines = [f"<b>Вопрос {number}/{total}</b> · {html.escape(q.get('topic', ''), quote=False)}", ""]
    lines.append(html.escape(q["question"], quote=False))
    lines.append("")
    for letter, option in zip("ABCDEFG", q["options"]):
        lines.append(f"<b>{letter})</b> {html.escape(option, quote=False)}")
    return "\n".join(lines)[:TG_LIMIT]


def render_answer(q):
    letter = q["correct"]
    option = q["options"]["ABCDEFG".index(letter)]
    lines = [f"<b>Правильный ответ: {letter})</b> {html.escape(option, quote=False)}", ""]
    lines.append(html.escape(q["explanation"], quote=False))
    if q.get("sources"):
        lines.append("")
        lines.append("<b>Источники:</b>")
        lines.extend(html.escape(s, quote=False) for s in q["sources"])
    return "\n".join(lines)[:TG_LIMIT]


# ---------------------------------------------------------------- state
def _fernet():
    key = hashlib.sha256(("state-v1:" + TOKEN).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(_fernet().decrypt(STATE_FILE.read_bytes()))
        except (InvalidToken, ValueError) as exc:
            print(f"WARNING: could not read state ({exc}); starting fresh")
    return {"offset": 0, "users": {}}


def save_state(state):
    STATE_FILE.write_bytes(_fernet().encrypt(json.dumps(state).encode()))


# ---------------------------------------------------------------- bot logic
class Bot:
    def __init__(self):
        self.state = load_state()
        self.dirty = False

    def user(self, chat_id):
        users = self.state["users"]
        key = str(chat_id)
        if key not in users:
            users[key] = {"seen": [], "last_sent": None, "active": True}
            self.dirty = True
        return users[key]

    def safe_send(self, chat_id, **params):
        try:
            return tg("sendMessage", chat_id=chat_id, parse_mode="HTML", **params)
        except TelegramError as exc:
            if exc.code == 403:  # user blocked the bot
                self.user(chat_id)["active"] = False
                self.dirty = True
                print(f"chat {chat_id} blocked the bot; unsubscribed")
            else:
                print(f"send to {chat_id} failed: {exc}")

    def send_next_question(self, chat_id):
        u = self.user(chat_id)
        u["active"] = True
        questions = load_questions()
        unseen = [q for q in questions if q["id"] not in u["seen"]]
        if not unseen:
            self.safe_send(chat_id, text=(
                "Все вопросы, что есть сейчас, пройдены 🎉\n"
                "Новые добавляются регулярно — завтра в 10:00 пришлю следующий."))
            return
        q = unseen[0]
        number = questions.index(q) + 1
        self.safe_send(
            chat_id,
            text=render_question(q, number, len(questions)),
            reply_markup=keyboard((BTN_ANSWER, f"ans:{q['id']}")),
        )
        u["seen"].append(q["id"])
        u["last_sent"] = dt.datetime.now(TZ).date().isoformat()
        self.dirty = True

    def handle_message(self, msg):
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip().lower()
        if text.startswith("/start"):
            self.safe_send(chat_id, text=(
                "Привет! Я присылаю вопросы для подготовки к экзамену "
                "<b>Databricks Certified Data Engineer Professional</b>.\n\n"
                "Каждый день в 10:00 (Белград) — новый вопрос. Под вопросом есть кнопка "
                "с правильным ответом и объяснением, а после — кнопка «Ещё вопрос».\n\n"
                "Начнём 👇"))
            self.send_next_question(chat_id)
        elif text.startswith("/question") or text.startswith("/next"):
            self.send_next_question(chat_id)
        elif text.startswith("/stop"):
            self.user(chat_id)["active"] = False
            self.dirty = True
            self.safe_send(chat_id, text="Ок, больше не буду присылать ежедневные вопросы. /start — вернуться.")
        else:
            self.safe_send(chat_id, text="Нажми /question, чтобы получить вопрос.",
                           reply_markup=keyboard((BTN_NEXT, "next")))

    def handle_callback(self, cb):
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        try:
            tg("answerCallbackQuery", callback_query_id=cb["id"])
        except TelegramError as exc:
            print(f"answerCallbackQuery failed: {exc}")
        if chat_id is None:
            return
        if data.startswith("ans:"):
            qid = data[4:]
            q = next((q for q in load_questions() if q["id"] == qid), None)
            # remove the button under the question so it is not pressed twice
            try:
                tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"],
                   reply_markup={"inline_keyboard": []})
            except TelegramError:
                pass
            if q is None:
                self.safe_send(chat_id, text="Этот вопрос больше не найден.",
                               reply_markup=keyboard((BTN_NEXT, "next")))
                return
            self.safe_send(chat_id, text=render_answer(q),
                           reply_markup=keyboard((BTN_NEXT, "next")),
                           disable_web_page_preview=True)
        elif data == "next":
            try:
                tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"],
                   reply_markup={"inline_keyboard": []})
            except TelegramError:
                pass
            self.send_next_question(chat_id)

    def daily(self):
        now = dt.datetime.now(TZ)
        if now.hour < DAILY_HOUR:
            return
        today = now.date().isoformat()
        for chat_id, u in list(self.state["users"].items()):
            if u.get("active") and u.get("last_sent") != today:
                print(f"daily question -> {chat_id}")
                self.send_next_question(int(chat_id))
                if u.get("last_sent") != today:  # nothing left to send; don't retry all day
                    u["last_sent"] = today
                    self.dirty = True

    def poll_once(self, wait):
        params = {"offset": self.state["offset"], "timeout": wait,
                  "allowed_updates": ["message", "callback_query"]}
        updates = tg("getUpdates", http_timeout=wait + 15, **params)
        for upd in updates:
            self.state["offset"] = upd["update_id"] + 1
            self.dirty = True
            try:
                if "message" in upd and "chat" in upd["message"]:
                    self.handle_message(upd["message"])
                elif "callback_query" in upd:
                    self.handle_callback(upd["callback_query"])
            except Exception as exc:  # never let one bad update kill the worker
                print(f"update {upd['update_id']} failed: {exc!r}")

    def run(self, minutes, on_save):
        tg("deleteWebhook")
        deadline = time.monotonic() + minutes * 60
        last_save = time.monotonic()
        while time.monotonic() < deadline:
            try:
                self.daily()
                self.poll_once(wait=min(50, max(1, int(deadline - time.monotonic()))))
            except Exception as exc:
                print(f"loop error: {exc!r}; retrying in 10s")
                time.sleep(10)
            if self.dirty and time.monotonic() - last_save > 60:
                self.flush(on_save)
                last_save = time.monotonic()
        self.flush(on_save)

    def run_once(self, on_save):
        """Short run for a private repo: handle everything queued since last time, then exit."""
        tg("deleteWebhook")
        self.daily()
        while True:
            before = self.state["offset"]
            self.poll_once(wait=0)
            if self.state["offset"] == before:
                break
        self.flush(on_save)

    def flush(self, on_save):
        if self.dirty:
            save_state(self.state)
            self.dirty = False
            on_save()


def git_push_state():
    """Commit state.enc to the bot-state branch (the workflow checks it out into ./state)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    d = STATE_FILE.parent
    os.system(
        f'cd "{d}" && git add {STATE_FILE.name} && '
        f'git -c user.name="quiz-bot" -c user.email="quiz-bot@users.noreply.github.com" '
        f'commit -q -m "update state" && git push -q origin HEAD:bot-state'
    )


def check_questions():
    """Validate every question file; exit non-zero on problems (used in CI)."""
    ok = True
    ids = set()
    for q in load_questions():
        problems = []
        for field in ("question", "options", "correct", "explanation"):
            if not q.get(field):
                problems.append(f"missing {field}")
        if q.get("correct") and q.get("options") and q["correct"] not in "ABCDEFG"[: len(q["options"])]:
            problems.append("correct letter out of range")
        if q["id"] in ids:
            problems.append("duplicate id")
        ids.add(q["id"])
        if len(render_question(q, 1, 1)) >= TG_LIMIT or len(render_answer(q)) >= TG_LIMIT:
            problems.append("too long for Telegram")
        if problems:
            ok = False
            print(f"{q['id']}: {', '.join(problems)}")
    print(f"{len(ids)} questions checked")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["once", "run", "check"])
    ap.add_argument("--minutes", type=float, default=340)
    args = ap.parse_args()
    if args.mode == "check":
        sys.exit(0 if check_questions() else 1)
    if not TOKEN:
        sys.exit("TG_BOT_SECRET is not set")
    if args.mode == "once":
        Bot().run_once(on_save=git_push_state)
    else:  # long-lived worker; only sensible in a public repo (unlimited Actions minutes)
        Bot().run(args.minutes, on_save=git_push_state)
