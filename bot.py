#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
import threading
import asyncio
import time
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
CALLBACK_URL    = os.getenv("CALLBACK_URL")
RETURN_URL      = os.getenv("RETURN_URL")

for var in ("TELEGRAM_TOKEN","OXAPAY_API_KEY","OWNER_PASSWORD","GOOGLE_API_KEY","CALLBACK_URL","RETURN_URL"):
    if not globals()[var]:
        raise RuntimeError(f"{var} is not set")

# ── DB setup ────────────────────────────────────────────────────────────────────
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  expires_at  TEXT,
  is_active   INTEGER
);
CREATE TABLE IF NOT EXISTS chats (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  message     TEXT,
  timestamp   TEXT
);
CREATE TABLE IF NOT EXISTS owner_sessions (
  user_id     INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS codes (
  code        TEXT PRIMARY KEY,
  days        INTEGER
);
""")
conn.commit()

# ── In-memory prefs ─────────────────────────────────────────────────────────────
user_lang = {}

# ── Texts ──────────────────────────────────────────────────────────────────────
texts = {
    "en": {
        "choose":      "Please select your language:",
        "registered":  "Registered until {date}",
        "stopped":     "Translation stopped.",
        "contact_ok":  "Your message has been sent to the owner.",
        "contact_txt": "[Contact]\nFrom {uid}/{uname} (DB#{dbid}):\n{msg}",
        "code_success":"Translation activated until {date}",
        "code_fail":   "Invalid code or days.",
        "extend":      "Choose extension option:",
        "m1":          "1 month",
        "y1":          "1 year",
        "invoice_btn": "▶️ Pay now",
        "auth_ok":     "Authenticated as owner.",
        "auth_fail":   "Authentication failed.",
        "help_owner":  "Owner commands:\n"
                       "/auth <password>\n"
                       "/help\n"
                       "/code <code> <days>\n"
                       "/stats\n"
                       "/user <user_id> <message>\n"
                       "/message <message>\n"
                       "/stat\n",
        "stats_hdr":   "Total users: {total}\nActive subs: {active}\n",
        "stats_row":   "UID:{uid} UNAME:{uname} EXPIRES:{exp} ACTIVE:{act}",
        "user_ok":     "Message sent to {uid}.",
        "user_fail":   "Failed to send to {uid}.",
        "bcast_ok":    "Broadcast sent.",
        "stat_hdr":    "Chats for {uid}/{uname} (last {n}):",
        "stat_row":    "{ts} | {msg}"
    },
    "ko": {
        "choose":      "언어를 선택하세요:",
        "registered":  "{date}까지 번역 기능 활성화됨",
        "stopped":     "번역 기능 중단됨",
        "contact_ok":  "메시지가 소유자에게 전달되었습니다.",
        "contact_txt": "[문의]\nFrom {uid}/{uname} (DB#{dbid}):\n{msg}",
        "code_success":"{date}까지 번역 기능이 활성화되었습니다.",
        "code_fail":   "잘못된 코드 또는 일수입니다.",
        "extend":      "연장 옵션을 선택하세요:",
        "m1":          "1개월",
        "y1":          "1년",
        "invoice_btn": "▶️ 결제하기",
        "auth_ok":     "소유자 인증 완료.",
        "auth_fail":   "인증에 실패했습니다.",
        "help_owner":  "소유자 명령어:\n"
                       "/auth <password>\n"
                       "/help\n"
                       "/code <code> <days>\n"
                       "/stats\n"
                       "/user <user_id> <message>\n"
                       "/message <message>\n"
                       "/stat\n",
        "stats_hdr":   "총 사용자: {total}\n활성 구독: {active}\n",
        "stats_row":   "유저ID:{uid} 이름:{uname} 만료:{exp} 활성:{act}",
        "user_ok":     "{uid}에게 메시지를 보냈습니다.",
        "user_fail":   "{uid}에게 메시지 전송 실패.",
        "bcast_ok":    "전체 공지가 발송되었습니다.",
        "stat_hdr":    "{uid}/{uname} 채팅 기록 (최근 {n}개):",
        "stat_row":    "{ts} | {msg}"
    },
    # "zh","vi","km" 동일하게 추가 가능
}

# ── Helpers ─────────────────────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    r = requests.post("https://translation.googleapis.com/language/translate/v2/detect",
                      params={"key": GOOGLE_API_KEY}, data={"q": text})
    r.raise_for_status()
    return r.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    r = requests.post("https://translation.googleapis.com/language/translate/v2",
                      params={"key": GOOGLE_API_KEY},
                      json={"q": text, "target": target, "format": "text"})
    r.raise_for_status()
    return r.json()["data"]["translations"][0]["translatedText"]

async def detect_language_async(text: str) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, detect_language, text)

async def translate_text_async(text: str, target: str) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, translate_text, text, target)

def create_invoice(data: dict) -> dict:
    r = requests.post("https://api.oxapay.com/v1/payment/invoice",
                      json=data,
                      headers={"merchant_api_key": OXAPAY_API_KEY, "Content-Type":"application/json"})
    r.raise_for_status()
    return r.json()

# ── Telegram Handlers ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("English", callback_data="lang_en"),
         InlineKeyboardButton("한국어", callback_data="lang_ko")],
        [InlineKeyboardButton("中文",    callback_data="lang_zh"),
         InlineKeyboardButton("Tiếng Việt", callback_data="lang_vi")],
        [InlineKeyboardButton("ភាសាខ្មែរ", callback_data="lang_km")],
    ]
    await update.message.reply_text(texts["en"]["choose"], reply_markup=InlineKeyboardMarkup(kb))

async def choose_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qry  = update.callback_query
    lang = qry.data.split("_",1)[1]
    user_lang[qry.from_user.id] = lang
    await qry.answer()
    # 언어 선택 후 일반 사용자에게는 등록 안내 바로 띄우도록 texts[lang]["registered"] 사용해도 되고, 
    # 예제대로 owner_help 대신 일반 도움말로 바꾸려면 별도 texts[lang]["help_user"] 추가 필요
    await qry.edit_message_text(texts[lang]["help_owner"])

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    exp  = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?,?,?,1)", (uid, update.effective_user.username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(texts[lang]["registered"].format(date=exp.date()))

async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
    conn.commit()
    await update.message.reply_text(texts[lang]["stopped"])

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang  = user_lang.get(update.effective_user.id, "en")
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    row   = cur.execute("SELECT rowid FROM users WHERE user_id=?", (uid,)).fetchone()
    dbid  = row[0] if row else ""
    msg   = " ".join(ctx.args) if ctx.args else ""
    txt   = texts[lang]["contact_txt"].format(uid=uid, uname=uname, dbid=dbid, msg=msg)
    for (oid,) in cur.execute("SELECT user_id FROM owner_sessions").fetchall():
        await ctx.application.bot.send_message(chat_id=oid, text=txt)
    await update.message.reply_text(texts[lang]["contact_ok"])

async def code_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = user_lang.get(uid, "en")
    args = ctx.args
    # Owner mode: create code
    if cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        if len(args) != 2:
            return await update.message.reply_text("Usage: /code <6-digit code> <days>")
        code, days = args[0], int(args[1])
        cur.execute("REPLACE INTO codes VALUES (?,?)", (code, days))
        conn.commit()
        return await update.message.reply_text(f"Code {code} → {days} days created.")
    # User mode: redeem code
    if len(args) != 2:
        return await update.message.reply_text("Usage: /code <code> <days>")
    code, days = args[0], int(args[1])
    row = cur.execute("SELECT days FROM codes WHERE code=?", (code,)).fetchone()
    if not row or row[0] != days:
        return await update.message.reply_text(texts[lang]["code_fail"])
    now = datetime.utcnow()
    ur  = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (uid,)).fetchone()
    if ur and datetime.fromisoformat(ur[0]) > now:
        new_exp = datetime.fromisoformat(ur[0]) + timedelta(days=days)
    else:
        new_exp = now + timedelta(days=days)
    cur.execute("REPLACE INTO users VALUES (?,?,?,1)",
                (uid, update.effective_user.username, new_exp.isoformat()))
    conn.commit()
    await update.message.reply_text(texts[lang]["code_success"].format(date=new_exp.date()))

async def extend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang   = user_lang.get(update.effective_user.id, "en")
    uid    = update.effective_user.id
    now_ts = int(time.time())
    kb     = []
    for days, label in ((30, texts[lang]["m1"]), (365, texts[lang]["y1"])):
        data = {
            "amount":            100,
            "currency":          "USD",
            "lifetime":          30,
            "fee_paid_by_payer": 1,
            "under_paid_coverage":2.5,
            "to_currency":       "USDT",
            "auto_withdrawal":   False,
            "mixed_payment":     True,
            "callback_url":      CALLBACK_URL,
            "return_url":        RETURN_URL,
            "email":             update.effective_user.username or "",
            "order_id":          f"{uid}-{now_ts}-{days}",
            "metadata":          {"user_id": uid, "days": days},
            "thanks_message":    "Thank you!",
            "description":       f"Subscription {days}-day extension",
            "sandbox":           False
        }
        resp = await asyncio.get_event_loop().run_in_executor(None, create_invoice, data)
        kb.append([InlineKeyboardButton(label, url=resp["data"]["invoice_url"])])
    await update.message.reply_text(texts[lang]["extend"], reply_markup=InlineKeyboardMarkup(kb))

async def auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = ctx.args
    if not args:
        return await update.message.reply_text("Usage: /auth <password>")
    if args[0] == OWNER_PASSWORD:
        cur.execute("INSERT OR IGNORE INTO owner_sessions VALUES(?)", (uid,))
        conn.commit()
        await update.message.reply_text(texts[user_lang.get(uid, "en")]["auth_ok"])
    else:
        await update.message.reply_text(texts[user_lang.get(uid, "en")]["auth_fail"])

async def owner_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    lang = user_lang.get(uid, "en")
    await update.message.reply_text(texts[lang]["help_owner"])

async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    lang  = user_lang.get(uid, "en")
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active= cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    msg = texts[lang]["stats_hdr"].format(total=total, active=active)
    for row in cur.execute("SELECT user_id,username,expires_at,is_active FROM users"):
        msg += "\n" + texts[lang]["stats_row"].format(
            uid=row[0], uname=row[1] or "", exp=row[2].split("T")[0], act=row[3]
        )
    await update.message.reply_text(msg)

async def user_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /user <user_id> <message>")
    target = int(ctx.args[0])
    text   = " ".join(ctx.args[1:])
    try:
        await ctx.application.bot.send_message(chat_id=target, text=text)
        await update.message.reply_text(texts[user_lang.get(uid,"en")]["user_ok"].format(uid=target))
    except:
        await update.message.reply_text(texts[user_lang.get(uid,"en")]["user_fail"].format(uid=target))

async def message_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    text = " ".join(ctx.args)
    for (u,) in cur.execute("SELECT user_id FROM users WHERE is_active=1").fetchall():
        try:
            await ctx.application.bot.send_message(chat_id=u, text=text)
        except:
            pass
    await update.message.reply_text(texts[user_lang.get(uid,"en")]["bcast_ok"])

async def stat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    lang = user_lang.get(uid, "en")
    for (u,) in cur.execute("SELECT DISTINCT user_id FROM chats").fetchall():
        uname = cur.execute("SELECT username FROM users WHERE user_id=?", (u,)).fetchone()[0] or ""
        calls = cur.execute(
            "SELECT message,timestamp FROM chats WHERE user_id=? ORDER BY timestamp DESC LIMIT 200",
            (u,)
        ).fetchall()
        header = texts[lang]["stat_hdr"].format(uid=u, uname=uname, n=len(calls))
        await update.message.reply_text(header)
        for msg, ts in calls:
            line = texts[lang]["stat_row"].format(
                ts=ts.split("T")[0] + " " + ts.split("T")[1].split(".")[0],
                msg=msg
            )
            await update.message.reply_text(line)

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = cur.execute("SELECT expires_at,is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[1] == 0:
        return
    exp = datetime.fromisoformat(row[0])
    if exp < datetime.utcnow():
        cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
        conn.commit()
        return
    txt = update.message.text
    src = await detect_language_async(txt)
    targets = {"en","ko","zh","vi","km"} - {src}
    outs = []
    for t in targets:
        tr = await translate_text_async(txt, t)
        outs.append(f"{t}: {tr}")
    cur.execute("INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
                (uid, txt, datetime.utcnow().isoformat()))
    conn.commit()
    await update.message.reply_text("\n".join(outs))

# ── Flask & callback ──────────────────────────────────────────────────────────
app_flask = Flask(__name__)
bot       = Bot(token=TELEGRAM_TOKEN)

@app_flask.route("/callback", methods=["POST"])
def payment_callback():
    payload = request.get_json() or {}
    data    = payload.get("data", {})
    if data.get("status") == "paid":
        meta    = data.get("metadata", {})
        uid     = meta.get("user_id")
        days    = meta.get("days", 0)
        if uid and days:
            now = datetime.utcnow()
            ur  = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (uid,)).fetchone()
            if ur and datetime.fromisoformat(ur[0]) > now:
                newe = datetime.fromisoformat(ur[0]) + timedelta(days=days)
            else:
                newe = now + timedelta(days=days)
            cur.execute("REPLACE INTO users VALUES (?,?,?,1)",
                        (uid, None, newe.isoformat()))
            conn.commit()
            lang = user_lang.get(uid, "en")
            bot.send_message(chat_id=uid,
                             text=texts[lang]["code_success"].format(date=newe.date()))
    return "", 200

# ── Dispatcher & launch ────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start",   start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("register",register))
app.add_handler(CommandHandler("stop",    stop))
app.add_handler(CommandHandler("contact", contact))
app.add_handler(CommandHandler("code",    code_cmd))
app.add_handler(CommandHandler("extend",  extend))
app.add_handler(CommandHandler("auth",    auth))
app.add_handler(CommandHandler("help",    owner_help))
app.add_handler(CommandHandler("stats",   stats_cmd))
app.add_handler(CommandHandler("user",    user_cmd))
app.add_handler(CommandHandler("message", message_cmd))
app.add_handler(CommandHandler("stat",    stat_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

def main():
    threading.Thread(
        target=lambda: app_flask.run(host="0.0.0.0", port=int(os.getenv("PORT",8080)))
    ).start()
    app.run_polling()

if __name__ == "__main__":
    main()
