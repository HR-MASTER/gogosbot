#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import requests
import threading
import asyncio
import time
import csv
from datetime import datetime, timedelta

from flask import Flask, render_template_string, request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ── Environment variables ──────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
OXAPAY_API_KEY  = os.getenv("OXAPAY_API_KEY")
OWNER_PASSWORD  = os.getenv("OWNER_PASSWORD")
GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")

for var, name in [(TELEGRAM_TOKEN, "TELEGRAM_TOKEN"),
                  (OWNER_PASSWORD,  "OWNER_PASSWORD"),
                  (GOOGLE_API_KEY,  "GOOGLE_API_KEY")]:
    if not var:
        raise RuntimeError(f"{name} is not set")

TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

# ── Internationalized texts ────────────────────────────────────────────────────
texts = {
    "en": {
        "choose":     "Please select your language:",
        "help":       "Available commands:\n"
                      "/register       – Activate translation (7 days)\n"
                      "/stop           – Deactivate translation\n"
                      "/code <CODE>    – Use owner-provided code to extend\n"
                      "/contact <msg>  – Contact owner\n"
                      "/records        – Download recent message logs\n",
        "registered": "Registered until {date}",
        "stopped":    "Translation stopped.",
        "auth_fail":  "Authentication failed.",
        "auth_ok":    "Authenticated as owner. Use /help to view owner commands.",
        "invalid_sc": "Invalid command or arguments.",
        "no_codes":   "No such code.",
        "limit_reached": "Usage limit reached for this code while active.",
        "code_set":   "Code {code} set for {days} days.",
        "used_code":  "Subscription extended by {days} days, until {date}.",
    },
    "ko": {
        "choose":     "언어를 선택하세요:",
        "help":       "사용 가능한 명령어:\n"
                      "/register       – 번역 활성화 (7일)\n"
                      "/stop           – 번역 중단\n"
                      "/code <CODE>    – 소유자 코드 사용\n"
                      "/contact <msg>  – 소유자에게 문의\n"
                      "/records        – 최근 메시지 로그 다운로드\n",
        "registered": "등록 완료: {date}까지",
        "stopped":    "번역 기능 중단됨",
        "auth_fail":  "인증 실패.",
        "auth_ok":    "소유자로 인증되었습니다. /help 로 소유자 명령어 확인 가능합니다.",
        "invalid_sc": "잘못된 명령어 또는 인수입니다.",
        "no_codes":   "존재하지 않는 코드입니다.",
        "limit_reached": "활성 구독 중에는 이 코드의 사용 한도를 초과했습니다.",
        "code_set":   "코드 {code} 가 {days}일 연장용으로 설정되었습니다.",
        "used_code":  "{days}일 연장 완료, {date}까지 활성화되었습니다.",
    },
    # 추가 언어 필요 시 여기에 정의
}

# ── Database setup ─────────────────────────────────────────────────────────────
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  expires_at  TEXT,
  is_active   INTEGER
);
CREATE TABLE IF NOT EXISTS owner_sessions (
  user_id     INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS message_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  username    TEXT,
  message     TEXT,
  timestamp   TEXT
);
CREATE TABLE IF NOT EXISTS codes (
  code        TEXT PRIMARY KEY,
  days        INTEGER
);
CREATE TABLE IF NOT EXISTS codes_usage (
  user_id     INTEGER,
  code        TEXT,
  used_at     TEXT
);
""")
conn.commit()

# ── In-memory language prefs ───────────────────────────────────────────────────
user_lang = {}

# ── Translation helpers ─────────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    r = requests.post(f"{TRANSLATE_URL}/detect",
        params={"key": GOOGLE_API_KEY}, data={"q": text})
    r.raise_for_status()
    return r.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    r = requests.post(TRANSLATE_URL,
        params={"key": GOOGLE_API_KEY},
        json={"q": text, "target": target, "format": "text"})
    r.raise_for_status()
    return r.json()["data"]["translations"][0]["translatedText"]

async def detect_language_async(text: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, detect_language, text)

async def translate_text_async(text: str, target: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, translate_text, text, target)

# ── Telegram handlers ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("English", callback_data="lang_en"),
         InlineKeyboardButton("한국어", callback_data="lang_ko")],
    ]
    await update.message.reply_text(
        texts["en"]["choose"],
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def choose_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qry = update.callback_query
    lang = qry.data.split("_",1)[1]
    user_lang[qry.from_user.id] = lang
    await qry.answer()
    await qry.edit_message_text(texts[lang]["help"])

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    exp  = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(
        texts[lang]["registered"].format(date=exp.date())
    )

async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
    conn.commit()
    await update.message.reply_text(texts[lang]["stopped"])

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args) if ctx.args else ""
    rows = cur.execute("SELECT user_id FROM owner_sessions").fetchall()
    for (owner_id,) in rows:
        await ctx.application.bot.send_message(
            chat_id=owner_id,
            text=f"[Contact]\nFrom {update.effective_user.id}:\n{text}"
        )
    lang = user_lang.get(update.effective_user.id, "en")
    await update.message.reply_text({
        "en": "Your message has been sent to the owner.",
        "ko": "메시지가 소유자에게 전달되었습니다."
    }[lang])

async def auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    pwd = ctx.args[0]
    uid = update.effective_user.id
    if pwd == OWNER_PASSWORD:
        cur.execute("INSERT OR IGNORE INTO owner_sessions(user_id) VALUES(?)", (uid,))
        conn.commit()
        await update.message.reply_text(texts["en"]["auth_ok"])
    else:
        await update.message.reply_text(texts["en"]["auth_fail"])

async def help_owner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    help_text = (
        "/start\n"
        "/register\n"
        "/stop\n"
        "/code <CODE>\n"
        "/scode <6-digit-code> <days>\n"
        "/auth <password>\n"
        "/help\n"
        "/stats\n"
        "/broadcast <message>\n"
        "/records\n"
    )
    await update.message.reply_text(help_text)

async def code_use(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    uid = update.effective_user.id
    code = ctx.args[0]
    lang = user_lang.get(uid, "en")
    # lookup code
    row = cur.execute("SELECT days FROM codes WHERE code=?", (code,)).fetchone()
    if not row:
        return await update.message.reply_text(texts[lang]["no_codes"])
    days = row[0]
    # check usage limit
    cnt = cur.execute(
        "SELECT COUNT(*) FROM codes_usage WHERE user_id=? AND code=?", (uid, code)
    ).fetchone()[0]
    user_row = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (uid,)).fetchone()
    now = datetime.utcnow()
    expires = datetime.fromisoformat(user_row[0]) if user_row else now
    if expires > now and cnt >= 2:
        return await update.message.reply_text(texts[lang]["limit_reached"])
    # extend subscription
    if expires > now:
        new_exp = expires + timedelta(days=days)
    else:
        new_exp = now + timedelta(days=days)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, new_exp.isoformat()))
    cur.execute("INSERT INTO codes_usage VALUES (?, ?, ?)",
                (uid, code, now.isoformat()))
    conn.commit()
    await update.message.reply_text(
        texts[lang]["used_code"].format(days=days, date=new_exp.date())
    )

async def scode_define(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    if len(ctx.args) != 2 or not re.fullmatch(r"\d{6}", ctx.args[0]) or not ctx.args[1].isdigit():
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    code = ctx.args[0]
    days = int(ctx.args[1])
    lang = user_lang.get(uid, "en")
    cur.execute("REPLACE INTO codes VALUES (?, ?)", (code, days))
    conn.commit()
    await update.message.reply_text(
        texts[lang]["code_set"].format(code=code, days=days)
    )

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    total  = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    rows   = cur.execute(
        "SELECT user_id, username, expires_at, is_active FROM users"
    ).fetchall()
    msg = f"Total users: {total}\nActive users: {active}\n\nUser details:\n"
    for user_id, username, exp, is_active in rows:
        msg += f"- {username or ''} (ID: {user_id})  Expires: {exp[:10]}  Active: {bool(is_active)}\n"
    await update.message.reply_text(msg)

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ctx.args:
        return await update.message.reply_text("Usage: /broadcast <message>")
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    text = " ".join(ctx.args)
    rows = cur.execute("SELECT user_id FROM users WHERE is_active=1").fetchall()
    for (user_id,) in rows:
        try:
            await ctx.application.bot.send_message(chat_id=user_id, text=text)
        except:
            pass
    await update.message.reply_text("Broadcast sent.")

async def records(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    rows = cur.execute(
        "SELECT user_id, username, message, timestamp FROM message_logs ORDER BY timestamp DESC"
    ).fetchall()
    path = "records.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id","username","message","timestamp"])
        writer.writerows(rows)
    await ctx.application.bot.send_document(chat_id=uid, document=open(path, "rb"))

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text
    # log message
    cur.execute(
        "INSERT INTO message_logs (user_id, username, message, timestamp) VALUES (?,?,?,?)",
        (uid, update.effective_user.username or "", txt, datetime.utcnow().isoformat())
    )
    conn.commit()
    # check subscription
    row = cur.execute(
        "SELECT expires_at,is_active FROM users WHERE user_id=?", (uid,)
    ).fetchone()
    if not row or row[1] == 0:
        return
    expires_at = datetime.fromisoformat(row[0])
    if expires_at < datetime.utcnow():
        cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
        conn.commit()
        return
    # translate
    src = await detect_language_async(txt)
    targets = {"en","ko","zh","vi","km"} - {src}
    outs = []
    for t in targets:
        tr = await translate_text_async(txt, t)
        outs.append(f"{t}: {tr}")
    await update.message.reply_text("\n".join(outs))


# ── Flask app and callback endpoint ────────────────────────────────────────────
app_flask = Flask(__name__)
bot       = Bot(token=TELEGRAM_TOKEN)

@app_flask.route("/")
def dashboard():
    total  = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    return render_template_string("""
    <h1>Bot Dashboard</h1>
    <ul><li>Total users: {{total}}</li><li>Active: {{active}}</li></ul>
    """, total=total, active=active)

@app_flask.route("/healthz")
def healthz():
    return "OK"

@app_flask.route("/callback", methods=["POST"])
def payment_callback():
    # kept for compatibility if needed
    return "", 200


# ── Dispatcher & launch ────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("contact", contact))
app.add_handler(CommandHandler("auth", auth))
app.add_handler(CommandHandler("help", help_owner))
app.add_handler(CommandHandler("code", code_use))
app.add_handler(CommandHandler("scode", scode_define))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CommandHandler("records", records))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

def main():
    threading.Thread(
        target=lambda: app_flask.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    ).start()
    app.run_polling()

if __name__ == "__main__":
    main()
