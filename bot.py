#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
import threading
from datetime import datetime, timedelta

from flask import Flask, render_template_string
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# 환경 변수
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OXAPAY_KEY     = os.getenv("OXAPAY_API_KEY")      # Invoice API
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")      # ex: "ss501"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")      # Translation API key

TRANSLATE_URL  = "https://translation.googleapis.com/language/translate/v2"

# 언어별 메시지
texts = {
    "en": {
        "choose":      "Please select your language:",
        "help":        "Available commands:\n"
                       "/register – Activate translation (7 days)\n"
                       "/stop     – Deactivate translation\n"
                       "/extend   – Extend subscription\n"
                       "/help     – Show this help message",
        "registered":  "Registered until {date}",
        "stopped":     "Translation stopped.",
        "extend":      "Choose an option to extend:",
        "m1":          "1 month (30 USDT)",
        "y1":          "1 year (300 USDT)",
    },
    "ko": {
        "choose":      "언어를 선택하세요:",
        "help":        "사용 가능한 명령어:\n"
                       "/register – 번역 활성화 (7일)\n"
                       "/stop     – 번역 중단\n"
                       "/extend   – 구독 연장\n"
                       "/help     – 도움말 표시",
        "registered":  "등록 완료: {date}까지",
        "stopped":     "번역 기능 중단됨",
        "extend":      "연장 옵션 선택:",
        "m1":          "1개월 (30 USDT)",
        "y1":          "1년 (300 USDT)",
    },
    "zh": {
        "choose":      "请选择语言：",
        "help":        "可用命令：\n"
                       "/register – 启动翻译（7天）\n"
                       "/stop     – 停止翻译\n"
                       "/extend   – 延长服务\n"
                       "/help     – 显示帮助",
        "registered":  "注册成功，截止日期：{date}",
        "stopped":     "翻译已停止。",
        "extend":      "请选择延长选项：",
        "m1":          "1 个月 (30 USDT)",
        "y1":          "1 年 (300 USDT)",
    },
    "vi": {
        "choose":      "Vui lòng chọn ngôn ngữ:",
        "help":        "Các lệnh:\n"
                       "/register – Kích hoạt dịch (7 ngày)\n"
                       "/stop     – Dừng dịch\n"
                       "/extend   – Gia hạn\n"
                       "/help     – Hiển thị trợ giúp",
        "registered":  "Đăng ký đến ngày {date}",
        "stopped":     "Đã dừng dịch.",
        "extend":      "Chọn tùy chọn gia hạn:",
        "m1":          "1 tháng (30 USDT)",
        "y1":          "1 năm (300 USDT)",
    },
    "km": {
        "choose":      "សូមជ្រើសភាសា៖",
        "help":        "ពាក្យបញ្ជា:\n"
                       "/register – បើកការបកប្រែ (7 ថ្ងៃ)\n"
                       "/stop     – បញ្ឈប់ការបកប្រែ\n"
                       "/extend   – ពង្រីកការជាវ\n"
                       "/help     – បង្ហាញជំនួយ",
        "registered":  "បានចុះឈ្មោះរហូតដល់ {date}",
        "stopped":     "បានបញ្ឈប់ការបកប្រែ។",
        "extend":      "ជ្រើសជម្រើសពង្រីក៖",
        "m1":          "1 ខែ (30 USDT)",
        "y1":          "1 ឆ្នាំ (300 USDT)",
    },
}

# DB setup
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
""")
conn.commit()

# In-memory user language prefs
user_lang = {}

# Translation
def detect_language(text: str) -> str:
    r = requests.post(f"{TRANSLATE_URL}/detect",
        params={"key": GOOGLE_API_KEY}, data={"q": text})
    return r.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    r = requests.post(TRANSLATE_URL,
        params={"key": GOOGLE_API_KEY},
        json={"q": text, "target": target, "format": "text"})
    return r.json()["data"]["translations"][0]["translatedText"]

# Invoice
def create_invoice(amount: float, days: int) -> str:
    url = "https://api.oxapay.io/v1/invoices"
    headers = {"Authorization": f"Bearer {OXAPAY_KEY}",
               "Content-Type": "application/json"}
    payload = {"amount": amount, "currency": "USDT", "metadata": {"days": days}}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["data"]["invoice_url"]

# ── Handlers ─────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("English", callback_data="lang_en"),
         InlineKeyboardButton("한국어", callback_data="lang_ko")],
        [InlineKeyboardButton("中文", callback_data="lang_zh"),
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

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    await update.message.reply_text(texts[lang]["help"])

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid = update.effective_user.id
    exp = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, exp.isoformat(),))
    conn.commit()
    await update.message.reply_text(texts[lang]["registered"].format(date=exp.date()))

async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid = update.effective_user.id
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
    conn.commit()
    await update.message.reply_text(texts[lang]["stopped"])

async def extend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    kb = [
        [InlineKeyboardButton(texts[lang]["m1"], url=create_invoice(30,30))],
        [InlineKeyboardButton(texts[lang]["y1"], url=create_invoice(300,365))],
    ]
    await update.message.reply_text(texts[lang]["extend"],
        reply_markup=InlineKeyboardMarkup(kb))

# still available but not documented to user
async def code_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # backdoor
    if not ctx.args: return
    uid = update.effective_user.id
    days = int(ctx.args[0])
    cur.execute("UPDATE users SET expires_at=? WHERE user_id=?",
                ((datetime.utcnow()+timedelta(days=days)).isoformat(), uid))
    conn.commit()

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = cur.execute("SELECT is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[0]==0: return
    txt = update.message.text
    src = detect_language(txt)
    targets = {"en","ko","zh","vi","km"} - {src}
    outs = [f"{t}: {translate_text(txt,t)}" for t in targets]
    cur.execute("INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
        (uid, txt, datetime.utcnow().isoformat()))
    conn.commit()
    await update.message.reply_text("\n".join(outs))

# Owner backdoor remains unchanged...
# (owner_auth, owner_register, etc.)

# ── Dispatcher & launch ──────────────────────────

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("extend", extend))
app.add_handler(CommandHandler("code", code_user))      # backdoor only
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

# + owner handlers...

# Flask dashboard (unchanged)...
app_flask = Flask(__name__)
# ...

def main():
    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=5000)).start()
    app.run_polling()

if __name__ == "__main__":
    main()
