#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
import threading
import asyncio
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

# ── Environment variables ──────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
OXAPAY_API_KEY  = os.getenv("OXAPAY_API_KEY")   # Invoice & Payout API Key
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
        "choose":      "Please select your language:",
        "help":        "Available commands:\n"
                       "/register          – Activate translation (7 days)\n"
                       "/stop              – Deactivate translation\n"
                       "/extend            – Extend subscription\n"
                       "/payout            – Request a crypto payout\n"
                       "/help [command]    – Show this help or detailed command info",
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
                       "/payout   – 크립토 페이아웃 요청\n"
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
                       "/payout   – 发起加密货币支付请求\n"
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
                       "/payout   – Yêu cầu thanh toán crypto\n"
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
                       "/payout   – ស្នើរសុំផ្ទេរក្របខ័ណ្ឌ crypto\n"
                       "/help     – បង្ហាញជំនួយ",
        "registered":  "បានចុះឈ្មោះរហូតដល់ {date}",
        "stopped":     "បានបញ្ឈប់ការបកប្រែ។",
        "extend":      "ជ្រើសជម្រើសពង្រីក៖",
        "m1":          "1 ខែ (30 USDT)",
        "y1":          "1 ឆ្នាំ (300 USDT)",
    },
}

# ── Command descriptions (for /help <command>) ─────────────────────────────────
COMMAND_DESCRIPTIONS = {
    "start":        "Start the bot and choose your language.",
    "register":     "Activate auto-translation for 7 days.",
    "stop":         "Deactivate auto-translation immediately.",
    "extend":       "Generate invoice links to extend your subscription.",
    "payout":       "Request a crypto payout:\n"
                    "/payout <address> <amount> <currency> [network] [memo] [description]",
    "help":         "/help or /help <command>: Show general help or detailed info about a specific command.",
    "code":         "*(owner only)* Backdoor: extend subscription days: /code <days>.",
    "owner_auth":       "*(owner only)* Authenticate as bot owner: /owner_auth <password>.",
    "owner_register":   "*(owner only)* Register user: /owner_register <user_id> <days>.",
    "owner_stats":      "*(owner only)* Show user & chat statistics.",
    "owner_broadcast":  "*(owner only)* Broadcast message to all active users: /owner_broadcast <message>.",
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

# ── In-memory user language prefs ──────────────────────────────────────────────
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

# ── Invoice & Payout helpers ──────────────────────────────────────────────────
def create_invoice(amount: float, days: int) -> str:
    url = "https://api.oxapay.io/v1/invoices"
    headers = {"Authorization": f"Bearer {OXAPAY_API_KEY}",
               "Content-Type": "application/json"}
    payload = {"amount": amount, "currency": "USDT", "metadata": {"days": days}}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["data"]["invoice_url"]

def generate_payout(address: str,
                    amount: float,
                    currency: str,
                    network: str = None,
                    callback_url: str = None,
                    memo: str = None,
                    description: str = None) -> dict:
    url = "https://api.oxapay.com/v1/payout"
    headers = {
        "payout_api_key": OXAPAY_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"address": address, "amount": amount, "currency": currency}
    if network:      payload["network"]      = network
    if callback_url: payload["callback_url"] = callback_url
    if memo:         payload["memo"]         = memo
    if description:  payload["description"]  = description
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()

# ── Telegram handlers ──────────────────────────────────────────────────────────
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
    args = ctx.args
    if args:
        cmd = args[0].lstrip("/").lower()
        desc = COMMAND_DESCRIPTIONS.get(cmd)
        await update.message.reply_text(desc or "No description available.")
    else:
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
    loop = asyncio.get_event_loop()
    url1 = await loop.run_in_executor(None, create_invoice, 30, 30)
    url2 = await loop.run_in_executor(None, create_invoice, 300, 365)
    kb = [
        [InlineKeyboardButton(texts[lang]["m1"], url=url1)],
        [InlineKeyboardButton(texts[lang]["y1"], url=url2)],
    ]
    await update.message.reply_text(texts[lang]["extend"],
        reply_markup=InlineKeyboardMarkup(kb))

async def payout_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 3:
        return await update.message.reply_text(
            "Usage: /payout <address> <amount> <currency> [network] [memo] [description]"
        )
    address, amount, currency = args[0], float(args[1]), args[2].upper()
    network     = args[3] if len(args) > 3 else None
    memo        = args[4] if len(args) > 4 else None
    description = args[5] if len(args) > 5 else None
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            generate_payout,
            address, amount, currency,
            network, None, memo, description
        )
        data = result.get("data", {})
        tid  = data.get("track_id", "N/A")
        st   = data.get("status",   "unknown")
        await update.message.reply_text(
            f"✅ Payout requested\n• Track ID: {tid}\n• Status:   {st}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Payout failed: {e}")

async def code_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return
    uid  = update.effective_user.id
    days = int(ctx.args[0])
    cur.execute("UPDATE users SET expires_at=? WHERE user_id=?",
                ((datetime.utcnow()+timedelta(days=days)).isoformat(), uid))
    conn.commit()

# Owner-only handlers
async def owner_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /owner_auth <password>")
    pwd = ctx.args[0]
    uid = update.effective_user.id
    if pwd == OWNER_PASSWORD:
        cur.execute("INSERT OR IGNORE INTO owner_sessions(user_id) VALUES(?)", (uid,))
        conn.commit()
        await update.message.reply_text("Authenticated as owner.")
    else:
        await update.message.reply_text("Authentication failed.")

async def owner_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /owner_register <user_id> <days>")
    target_id = int(ctx.args[0])
    days      = int(ctx.args[1])
    exp = datetime.utcnow() + timedelta(days=days)
    row = cur.execute("SELECT username FROM users WHERE user_id=?", (target_id,)).fetchone()
    username = row[0] if row else None
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (target_id, username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(f"User {target_id} registered until {exp.date()}.")

async def owner_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    total   = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active  = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    chats   = cur.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    await update.message.reply_text(
        f"Stats:\n• Total users:  {total}\n• Active users: {active}\n• Total chats:  {chats}"
    )

async def owner_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ctx.args:
        return await update.message.reply_text("Usage: /owner_broadcast <message>")
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text("Unauthorized.")
    text = " ".join(ctx.args)
    rows = cur.execute("SELECT user_id FROM users WHERE is_active=1").fetchall()
    success = 0
    for (user_id,) in rows:
        try:
            await ctx.application.bot.send_message(chat_id=user_id, text=text)
            success += 1
        except:
            pass
    await update.message.reply_text(f"Broadcast sent to {success} users.")

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
    cur.execute(
        "INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
        (uid, txt, datetime.utcnow().isoformat())
    )
    conn.commit()
    await update.message.reply_text("\n".join(outs))

# ── Flask dashboard ───────────────────────────────────────────────────────────
app_flask = Flask(__name__)

@app_flask.route("/")
def dashboard():
    total  = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    return render_template_string("""
    <h1>Bot Dashboard</h1>
    <ul>
      <li>Total users: {{total}}</li>
      <li>Active users: {{active}}</li>
    </ul>
    """, total=total, active=active)

@app_flask.route("/healthz")
def healthz():
    return "OK"

# ── Dispatcher & launch ────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("extend", extend))
app.add_handler(CommandHandler("payout", payout_cmd))
app.add_handler(CommandHandler("code", code_user))  # backdoor
app.add_handler(CommandHandler("owner_auth", owner_auth))
app.add_handler(CommandHandler("owner_register", owner_register))
app.add_handler(CommandHandler("owner_stats", owner_stats))
app.add_handler(CommandHandler("owner_broadcast", owner_broadcast))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

def main():
    threading.Thread(
        target=lambda: app_flask.run(host="0.0.0.0", port=5000)
    ).start()
    app.run_polling()

if __name__ == "__main__":
    main()
