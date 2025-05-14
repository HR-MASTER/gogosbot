#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# 환경 변수
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
OXAPAY_KEY       = os.getenv("OXAPAY_API_KEY")
OWNER_PASSWORD   = os.getenv("OWNER_PASSWORD")    # ex: "ss501"
GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY")

TRANSLATE_URL    = "https://translation.googleapis.com/language/translate/v2"

# 다국어 UI 텍스트
texts = {
    "en": {
        "choose":     "Please select your language:",
        "help":       "Commands:\n"
                      "/register – Activate translation (7 days)\n"
                      "/stop     – Deactivate translation\n"
                      "/extend   – Extend subscription\n"
                      "/help     – Show this help",
        "registered": "Registered until {date}",
        "stopped":    "Translation stopped.",
        "extend":     "Choose extension:",
        "m1":         "1 month (30 USDT)",
        "y1":         "1 year (300 USDT)",
    },
    "ko": {
        "choose":     "언어를 선택하세요:",
        "help":       "명령어:\n"
                      "/register – 번역 활성화 (7일)\n"
                      "/stop     – 번역 중단\n"
                      "/extend   – 구독 연장\n"
                      "/help     – 도움말 보기",
        "registered": "등록 완료: {date}까지",
        "stopped":    "번역 기능 중단됨",
        "extend":     "연장 옵션 선택:",
        "m1":         "1개월 (30 USDT)",
        "y1":         "1년 (300 USDT)",
    },
    "zh": {
        "choose":     "请选择语言：",
        "help":       "命令：\n"
                      "/register – 启动翻译（7天）\n"
                      "/stop     – 停止翻译\n"
                      "/extend   – 延长服务\n"
                      "/help     – 查看帮助",
        "registered": "注册成功，截止：{date}",
        "stopped":    "翻译已停止。",
        "extend":     "请选择延长：",
        "m1":         "1个月 (30 USDT)",
        "y1":         "1年 (300 USDT)",
    },
    "vi": {
        "choose":     "Vui lòng chọn ngôn ngữ:",
        "help":       "Lệnh:\n"
                      "/register – Kích hoạt dịch (7 ngày)\n"
                      "/stop     – Dừng dịch\n"
                      "/extend   – Gia hạn\n"
                      "/help     – Trợ giúp",
        "registered": "Đăng ký đến {date}",
        "stopped":    "Đã dừng dịch.",
        "extend":     "Chọn gia hạn:",
        "m1":         "1 tháng (30 USDT)",
        "y1":         "1 năm (300 USDT)",
    },
    "km": {
        "choose":     "សូមជ្រើសភាសា៖",
        "help":       "ពាក្យបញ្ជា៖\n"
                      "/register – បើកបកប្រែ (7 ថ្ងៃ)\n"
                      "/stop     – បញ្ឈប់បកប្រែ\n"
                      "/extend   – ពង្រីកសេវា\n"
                      "/help     – ជំនួយ",
        "registered": "បានចុះឈ្មោះរហូតដល់ {date}",
        "stopped":    "បានបញ្ឈប់បកប្រែ។",
        "extend":     "ជ្រើសពង្រីក៖",
        "m1":         "1 ខែ (30 USDT)",
        "y1":         "1 ឆ្នាំ (300 USDT)",
    },
}

# DB 연결
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
  user_id    INTEGER PRIMARY KEY,
  username   TEXT,
  expires_at TEXT,
  is_active  INTEGER
);
CREATE TABLE IF NOT EXISTS chats (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER,
  message    TEXT,
  timestamp  TEXT
);
CREATE TABLE IF NOT EXISTS codes (
  code       TEXT PRIMARY KEY,
  days       INTEGER,
  created_by TEXT
);
""")
conn.commit()

# 사용자 언어 저장
user_lang = {}

# 번역 함수
def detect_language(text: str) -> str:
    r = requests.post(
        f"{TRANSLATE_URL}/detect",
        params={"key": GOOGLE_API_KEY},
        data={"q": text}
    )
    return r.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    r = requests.post(
        TRANSLATE_URL,
        params={"key": GOOGLE_API_KEY},
        json={"q": text, "target": target, "format": "text"}
    )
    return r.json()["data"]["translations"][0]["translatedText"]

# 인보이스 생성 (OXAPAY)
def create_invoice(amount: float, days: int) -> str:
    url = "https://api.oxapay.io/v1/invoices"
    headers = {
        "Authorization": f"Bearer {OXAPAY_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"amount": amount, "currency": "USDT", "metadata": {"days": days}}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["data"]["invoice_url"]

# ── 핸들러 ─────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("English", callback_data="lang_en"),
         InlineKeyboardButton("한국어", callback_data="lang_ko")],
        [InlineKeyboardButton("中文", callback_data="lang_zh"),
         InlineKeyboardButton("Tiếng Việt", callback_data="lang_vi")],
        [InlineKeyboardButton("ភាសាខ្មែរ", callback_data="lang_km")],
    ]
    await update.message.reply_text(texts["en"]["choose"],
                                    reply_markup=InlineKeyboardMarkup(kb))

async def choose_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    lang = q.data.split("_", 1)[1]
    user_lang[q.from_user.id] = lang
    await q.answer()
    await q.edit_message_text(texts[lang]["help"])

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    await update.message.reply_text(texts[lang]["help"])

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = user_lang.get(update.effective_user.id, "en")
    uid = update.effective_user.id
    exp = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, exp.isoformat()))
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
        [InlineKeyboardButton(texts[lang]["y1"], url=create_invoice(300,365))]
    ]
    await update.message.reply_text(texts[lang]["extend"],
                                    reply_markup=InlineKeyboardMarkup(kb))

# 백도어: 영어-only /code
async def code_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return
    uid = update.effective_user.id
    days = int(ctx.args[0])
    cur.execute("UPDATE users SET expires_at=? WHERE user_id=?",
                ((datetime.utcnow()+timedelta(days=days)).isoformat(), uid))
    conn.commit()

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = cur.execute("SELECT is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[0] == 0:
        return
    txt = update.message.text
    src = detect_language(txt)
    targets = {"en","ko","zh","vi","km"} - {src}
    outs = [f"{t}: {translate_text(txt,t)}" for t in targets]
    cur.execute("INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
                (uid, txt, datetime.utcnow().isoformat()))
    conn.commit()
    await update.message.reply_text("\n".join(outs))

# ── Owner 명령어 (영문) ─────────────────────────

owner_sessions = set()

async def owner_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lstrip(".") == f"owner{OWNER_PASSWORD}":
        owner_sessions.add(update.effective_user.id)
        await update.message.reply_text("Owner authentication successful")
    else:
        await update.message.reply_text("Authentication failed")

def is_owner(uid: int) -> bool:
    return uid in owner_sessions

async def owner_add_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    _, code, days = update.message.text.split()
    cur.execute("INSERT INTO codes VALUES (?, ?, ?)",
                (code, int(days), update.effective_user.username))
    conn.commit()
    await update.message.reply_text(f"Code {code} added for {days} days")

async def owner_list_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("SELECT code, days, created_by FROM codes").fetchall()
    text = "\n".join(f"{c} / {d} days by {b}" for c, d, b in rows) or "None"
    await update.message.reply_text(text)

async def owner_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    _, typ, val = update.message.text.split()
    if typ == "user":
        cur.execute("DELETE FROM users WHERE user_id = ?", (int(val),))
    else:
        cur.execute("DELETE FROM codes WHERE code = ?", (val,))
    conn.commit()
    await update.message.reply_text("Deletion completed")

async def owner_list_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("SELECT user_id, username, expires_at FROM users").fetchall()
    text = "\n".join(f"{u} / {n} / {e[:10]}" for u, n, e in rows) or "None"
    await update.message.reply_text(text)

async def owner_chats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("""
        SELECT u.username, c.message, c.timestamp
        FROM chats AS c
        JOIN users AS u ON c.user_id = u.user_id
        ORDER BY c.id DESC
        LIMIT 1000
    """).fetchall()
    text = "\n".join(f"{usr}: {msg} @ {ts[:19]}" for usr, msg, ts in rows) or "None"
    await update.message.reply_text(text)

# 새로운 Owner 명령어: .payout
async def owner_payout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    args = ctx.args
    if len(args) < 2:
        return await update.message.reply_text("Usage: .payout <address> <amount> [memo] [description]")
    address, amount = args[0], float(args[1])
    memo = args[2] if len(args) > 2 else ""
    description = args[3] if len(args) > 3 else ""
    payload = {
        "address": address,
        "amount": amount,
        "currency": "TRX",
        "network": "TRC20",
        "memo": memo,
        "description": description
    }
    headers = {
        "payout_api_key": OXAPAY_KEY,
        "Content-Type": "application/json"
    }
    r = requests.post("https://api.oxapay.com/v1/payout",
                      json=payload, headers=headers)
    try:
        r.raise_for_status()
        await update.message.reply_text(f"Payout result: {r.json()}")
    except Exception as e:
        await update.message.reply_text(f"Payout failed: {e}\n{r.text}")

async def owner_commands(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text(
        ".owner<password>         — Owner auth\n"
        ".addcode <code> <days>   — Add new code\n"
        ".listcodes               — List all codes\n"
        ".delete <user|code> <v>  — Delete user or code\n"
        ".listusers               — Show users\n"
        ".chats                   — Show last 1000 chats\n"
        ".payout <addr> <amt> [memo] [desc] — Execute payout\n"
        ".commands                — This command list"
    )

# ── 핸들러 등록 & 실행 ─────────────────────────

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("extend", extend))
app.add_handler(CommandHandler("code", code_user))

app.add_handler(MessageHandler(filters.Regex(rf"^\.owner{OWNER_PASSWORD}$"), owner_auth))
app.add_handler(MessageHandler(filters.Regex(r"^\.addcode\s+\S+\s+\d+$"), owner_add_code))
app.add_handler(MessageHandler(filters.Regex(r"^\.listcodes$"), owner_list_codes))
app.add_handler(MessageHandler(filters.Regex(r"^\.delete\s+(user|code)\s+\S+$"), owner_delete))
app.add_handler(MessageHandler(filters.Regex(r"^\.listusers$"), owner_list_users))
app.add_handler(MessageHandler(filters.Regex(r"^\.chats$"), owner_chats))
app.add_handler(MessageHandler(filters.Regex(r"^\.payout\s+\S+\s+\S+"), owner_payout))
app.add_handler(MessageHandler(filters.Regex(r"^\.commands$"), owner_commands))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

# Flask 대시보드는 기존과 동일
app_flask = Flask(__name__)
# … dashboard 구현 …

def main():
    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=5000)).start()
    app.run_polling()

if __name__ == "__main__":
    main()
