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
OXAPAY_API_KEY  = os.getenv("OXAPAY_API_KEY")   # Invoice API Key
OWNER_PASSWORD  = os.getenv("OWNER_PASSWORD")
GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")
if not OXAPAY_API_KEY:
    raise RuntimeError("OXAPAY_API_KEY is not set")
if not OWNER_PASSWORD:
    raise RuntimeError("OWNER_PASSWORD is not set")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

# ── Internationalized texts ────────────────────────────────────────────────────
texts = {
    "en": {
        "choose":         "Please select your language:",
        "help":           "Available commands:\n"
                          "/register – Activate translation (7 days)\n"
                          "/stop     – Deactivate translation\n"
                          "/extend   – Extend subscription\n"
                          "/contact  – Contact owner\n",
        "registered":     "Registered until {date}",
        "stopped":        "Translation stopped.",
        "extend":         "Choose extension option:",
        "m1":             "1 month",
        "y1":             "1 year",
        "invoice_button": "▶️ Pay now",
        "ext_success":    "Invoice created:\n{url}",
        "ext_fail":       "Invoice creation failed: {error}",
        "ext_notify":     "Your subscription has been extended until {date}.",
    },
    "ko": {
        "choose":         "언어를 선택하세요:",
        "help":           "사용 가능한 명령어:\n"
                          "/register – 번역 활성화 (7일)\n"
                          "/stop     – 번역 중단\n"
                          "/extend   – 구독 연장\n"
                          "/contact  – 소유자에게 문의\n",
        "registered":     "등록 완료: {date}까지",
        "stopped":        "번역 기능 중단됨",
        "extend":         "연장 옵션을 선택하세요:",
        "m1":             "1개월",
        "y1":             "1년",
        "invoice_button": "▶️ 결제하기",
        "ext_success":    "인보이스 생성됨:\n{url}",
        "ext_fail":       "인보이스 생성 실패: {error}",
        "ext_notify":     "구독이 {date}까지 연장되었습니다.",
    },
    # Add "zh", "vi", "km" similarly if needed
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

# ── Invoice creation helper ────────────────────────────────────────────────────
def create_invoice(data: dict) -> dict:
    url = "https://api.oxapay.com/v1/payment/invoice"
    headers = {
        "merchant_api_key": OXAPAY_API_KEY,
        "Content-Type":     "application/json"
    }
    r = requests.post(url, json=data, headers=headers)
    r.raise_for_status()
    return r.json()

# ── Telegram handlers ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("English", callback_data="lang_en"),
         InlineKeyboardButton("한국어", callback_data="lang_ko")],
        [InlineKeyboardButton("中文",    callback_data="lang_zh"),
         InlineKeyboardButton("Tiếng Việt", callback_data="lang_vi")],
        [InlineKeyboardButton("ភាសាខ្មែរ", callback_data="lang_km")],
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

async def extend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang   = user_lang.get(update.effective_user.id, "en")
    uid    = update.effective_user.id
    now_ts = int(time.time())

    options = [
        (30, texts[lang]["m1"]),
        (365, texts[lang]["y1"])
    ]
    kb = []
    for days, label in options:
        invoice_data = {
            "amount":              100,
            "currency":            "USD",
            "lifetime":            30,
            "fee_paid_by_payer":   1,
            "under_paid_coverage": 2.5,
            "to_currency":         "USDT",
            "auto_withdrawal":     False,
            "mixed_payment":       True,
            "callback_url":        os.getenv("CALLBACK_URL"),
            "return_url":          os.getenv("RETURN_URL"),
            "email":               update.effective_user.username or "",
            "order_id":            f"{uid}-{now_ts}-{days}",
            "metadata": {
                "user_id": uid,
                "days":    days
            },
            "thanks_message":      "Thank you!",
            "description":         f"Subscription {days}-day extension",
            "sandbox":             False
        }
        resp = await asyncio.get_event_loop().run_in_executor(
            None, create_invoice, invoice_data
        )
        url = resp["data"]["invoice_url"]
        kb.append([InlineKeyboardButton(label, url=url)])

    await update.message.reply_text(
        texts[lang]["extend"],
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def code_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return
    uid  = update.effective_user.id
    days = int(ctx.args[0])
    exp  = datetime.utcnow() + timedelta(days=days)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(f"Extended by {days} days, until {exp.date()}.")

async def auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /auth <password>")
    pwd = ctx.args[0]
    uid = update.effective_user.id
    if pwd == OWNER_PASSWORD:
        cur.execute("INSERT OR IGNORE INTO owner_sessions(user_id) VALUES(?)", (uid,))
        conn.commit()
        await update.message.reply_text("Authenticated as owner.")
    else:
        await update.message.reply_text("Authentication failed.")

async def owner_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    total  = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    await update.	message.reply_text(f"Users: {total}\nActive: {active}")

async def owner_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ctx.args:
        return await update.message.reply_text("Usage: /owner_broadcast <message>")
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

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
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
    txt = update.message.text
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
    payload = request.get_json() or {}
    data    = payload.get("data", {})
    status  = data.get("status")
    if status == "paid":
        meta    = data.get("metadata", {})
        user_id = meta.get("user_id")
        days    = meta.get("days", 0)
        if user_id and days:
            now = datetime.utcnow()
            row = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row and datetime.fromisoformat(row[0]) > now:
                new_exp = datetime.fromisoformat(row[0]) + timedelta(days=days)
            else:
                new_exp = now + timedelta(days=days)
            cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                        (user_id, None, new_exp.isoformat()))
            conn.commit()
            lang = user_lang.get(user_id, "en")
            msg  = texts[lang]["ext_notify"].format(date=new_exp.date())
            bot.send_message(chat_id=user_id, text=msg)
    return "", 200

# ── Dispatcher & launch ────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("contact", contact))
app.add_handler(CommandHandler("extend", extend))
app.add_handler(CommandHandler("code", code_user))
app.add_handler(CommandHandler("auth", auth))
app.add_handler(CommandHandler("owner_stats", owner_stats))
app.add_handler(CommandHandler("owner_broadcast", owner_broadcast))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

def main():
    threading.Thread(
        target=lambda: app_flask.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    ).start()
    app.run_polling()

if __name__ == "__main__":
    main()
