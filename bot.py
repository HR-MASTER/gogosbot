#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import requests
import threading
import asyncio
import csv
from datetime import datetime, timedelta

from flask import Flask, render_template_string, request
from telegram import (
    Update,
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
for var, name in [
    (TELEGRAM_TOKEN, "TELEGRAM_TOKEN"),
    (OWNER_PASSWORD, "OWNER_PASSWORD"),
    (GOOGLE_API_KEY, "GOOGLE_API_KEY"),
]:
    if not var:
        raise RuntimeError(f"{name} is not set")

TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

# ── Internationalized texts ────────────────────────────────────────────────────
texts = {
    "en": {
        "choose":             "Please select your language:",
        "help":               "Available commands:\n"
                              "/register       – Activate translation (7 days)\n"
                              "/stop           – Deactivate translation\n"
                              "/code <CODE>    – Use owner-provided code\n"
                              "/contact <msg>  – Contact owner\n"
                              "/period         – Show your remaining period\n",
        "registered":         "Registered until {date}",
        "already_registered": "Already activated once; cannot register again.",
        "stopped":            "Translation stopped.",
        "auth_fail":          "Authentication failed.",
        "auth_ok":            "Authenticated as owner. Use /help to view owner commands.",
        "invalid_sc":         "Invalid command or arguments.",
        "no_codes":           "No such code.",
        "limit_reached":      "Usage limit reached for this code while active.",
        "used_before":        "This code has already been used in this chat.",
        "code_set":           "Code {code} set for {days} days.",
        "used_code":          "Subscription extended by {days} days, until {date}.",
        "period":             "Subscription valid until {date} ({days} days remaining).",
    },
    "ko": {
        "choose":             "언어를 선택하세요:",
        "help":               "사용 가능한 명령어:\n"
                              "/register       – 번역 활성화 (7일)\n"
                              "/stop           – 번역 중단\n"
                              "/code <CODE>    – 소유자 코드 사용\n"
                              "/contact <msg>  – 소유자에게 문의\n"
                              "/period         – 남은 구독 기간 보기\n",
        "registered":         "등록 완료: {date}까지",
        "already_registered": "이미 한 번 활성화되어 다시 등록할 수 없습니다.",
        "stopped":            "번역 기능 중단됨",
        "auth_fail":          "인증 실패.",
        "auth_ok":            "소유자로 인증되었습니다. /help 로 소유자 명령어 확인 가능합니다.",
        "invalid_sc":         "잘못된 명령어 또는 인수입니다.",
        "no_codes":           "존재하지 않는 코드입니다.",
        "limit_reached":      "활성 구독 중에는 이 코드의 사용 한도를 초과했습니다.",
        "used_before":        "이 채팅에서 이미 이 코드를 사용했습니다.",
        "code_set":           "코드 {code} 가 {days}일 연장용으로 설정되었습니다.",
        "used_code":          "{days}일 연장 완료, {date}까지 활성화되었습니다.",
        "period":             "구독 기간: {date}까지 ({days}일 남음)",
    },
    "zh": {
        "choose":             "请选择您的语言：",
        "help":               "可用命令：\n"
                              "/register       – 启用翻译功能（7天）\n"
                              "/stop           – 停用翻译功能\n"
                              "/code <CODE>    – 使用所有者代码\n"
                              "/contact <msg>  – 联系所有者\n"
                              "/period         – 查看剩余订阅期限\n",
        "registered":         "已注册，直到 {date}",
        "already_registered": "已激活过，无法再次注册。",
        "stopped":            "翻译功能已停用",
        "auth_fail":          "认证失败。",
        "auth_ok":            "已认证为所有者。使用 /help 查看所有者命令。",
        "invalid_sc":         "无效的命令或参数。",
        "no_codes":           "不存在此代码。",
        "limit_reached":      "在活动订阅中已达到此代码的使用限制。",
        "used_before":        "此频道已使用过此代码。",
        "code_set":           "代码 {code} 已设置为延长 {days} 天。",
        "used_code":          "已延长 {days} 天，有效期至 {date}。",
        "period":             "订阅有效期至 {date}（剩余 {days} 天）。",
    },
    "vi": {
        "choose":             "Vui lòng chọn ngôn ngữ:",
        "help":               "Các lệnh khả dụng:\n"
                              "/register       – Kích hoạt dịch thuật (7 ngày)\n"
                              "/stop           – Hủy kích hoạt dịch thuật\n"
                              "/code <CODE>    – Sử dụng mã của chủ sở hữu\n"
                              "/contact <msg>  – Liên hệ chủ sở hữu\n"
                              "/period         – Xem thời gian còn lại\n",
        "registered":         "Đã đăng ký đến {date}",
        "already_registered": "Đã kích hoạt trước đó; không thể đăng ký lại.",
        "stopped":            "Đã tắt dịch thuật.",
        "auth_fail":          "Xác thực thất bại.",
        "auth_ok":            "Đã xác thực với tư cách chủ sở hữu. Sử dụng /help để xem lệnh chủ sở hữu.",
        "invalid_sc":         "Lệnh hoặc đối số không hợp lệ.",
        "no_codes":           "Mã không tồn tại.",
        "limit_reached":      "Đã đạt giới hạn sử dụng cho mã này khi đang hoạt động.",
        "used_before":        "Mã này đã được sử dụng trong nhóm này.",
        "code_set":           "Mã {code} đã được đặt cho {days} ngày.",
        "used_code":          "Đã gia hạn {days} ngày, đến {date}.",
        "period":             "Đăng ký hợp lệ đến {date} ({days} ngày còn lại).",
    },
    "km": {
        "choose":             "សូមជ្រើសរើសភាសារបស់អ្នក៖",
        "help":               "កម្មង់ដែលមាន៖\n"
                              "/register       – ដំណើរការបកប្រែ (7 ថ្ងៃ)\n"
                              "/stop           – បដិសេធការបកប្រែ\n"
                              "/code <CODE>    – ប្រើកូដម្ចាស់\n"
                              "/contact <msg>  – ទំនាក់ទំនងម្ចាស់\n"
                              "/period         – មើលប្រាក់កំណត់នៅសល់\n",
        "registered":         "បានចុះឈ្មោះរហូតដល់ {date}",
        "already_registered": "បានដំណើរការម្ដងរួចហើយ; មិនអាចចុះឈ្មោះម្តងទៀតបានទេ។",
        "stopped":            "បញ្ឈប់ការបកប្រែ។",
        "auth_fail":          "បញ្ហាក្នុងការផ្ទៀងផ្ទាត់។",
        "auth_ok":            "បានផ្ទៀងផ្ទុលជាម្ចាស់។ ប្រើ /help ដើម្បីមើលពាក្យបញ្ជាម្ចាស់។",
        "invalid_sc":         "ពាក្យបញ្ជាឬអាគុយម៉ង់មិនត្រឹមត្រូវ។",
        "no_codes":           "មិនមានកូដនេះទេ។",
        "limit_reached":      "បានឈប់ប្រើកូដនេះពេលមានសកម្មភាព។",
        "used_before":        "កូដនេះបានប្រើនៅក្នុងក្រុមនេះរួចហើយ។",
        "code_set":           "កូដ {code} បានកំណត់សម្រាប់ {days} ថ្ងៃ។",
        "used_code":          "បានពង្រីក {days} ថ្ងៃ រហូតដល់ {date}។",
        "period":             "សម្បទានគ្រប់គ្រាន់រហូតដល់ {date} ({days} ថ្ងៃនៅសល់)។",
    },
}

# ── Database setup ─────────────────────────────────────────────────────────────
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
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
  days        INTEGER,
  created_at  TEXT
);
CREATE TABLE IF NOT EXISTS codes_usage (
  chat_id     INTEGER,
  code        TEXT,
  used_at     TEXT
);
""")
conn.commit()

# ── In-memory preferences ──────────────────────────────────────────────────────
user_lang = {}

# ── Translation helpers ─────────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    r = requests.post(f"{TRANSLATE_URL}/detect",
                      params={"key": GOOGLE_API_KEY},
                      data={"q": text})
    r.raise_for_status()
    return r.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    r = requests.post(TRANSLATE_URL,
                      params={"key": GOOGLE_API_KEY},
                      json={"q": text, "target": target, "format": "text"})
    r.raise_for_status()
    return r.json()["data"]["translations"][0]["translatedText"]

async def detect_language_async(text: str) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, detect_language, text)

async def translate_text_async(text: str, target: str) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, translate_text, text, target)

# ── Telegram handlers ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = user_lang.get(uid)
    if not lang:
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
    else:
        help_text = texts[lang]["help"]
        kb = [["/register", "/stop"], ["/code", "/contact"], ["/period"]]
        await update.message.reply_text(
            help_text,
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

async def choose_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qry = update.callback_query
    await qry.answer()
    lang = qry.data.split("_",1)[1]
    user_lang[qry.from_user.id] = lang
    help_text = texts[lang]["help"]
    kb = [["/register", "/stop"], ["/code", "/contact"], ["/period"]]
    await qry.edit_message_text(help_text)
    await qry.message.reply_text("", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = user_lang.get(uid, "en")
    if cur.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
        return await update.message.reply_text(texts[lang]["already_registered"])
    exp = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)",
                (uid, update.effective_user.username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(texts[lang]["registered"].format(date=exp.date()))

async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = user_lang.get(uid, "en")
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
    conn.commit()
    await update.message.reply_text(texts[lang]["stopped"])

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(ctx.args)
    for (owner_id,) in cur.execute("SELECT user_id FROM owner_sessions").fetchall():
        await ctx.application.bot.send_message(owner_id, f"[Contact]\nFrom {update.effective_user.id}:\n{msg}")
    lang = user_lang.get(update.effective_user.id, "en")
    await update.message.reply_text({
        "en": "Your message has been sent to the owner.",
        "ko": "메시지가 소유자에게 전달되었습니다."
    }[lang])

async def period(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = user_lang.get(uid, "en")
    row = cur.execute("SELECT expires_at,is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[1] == 0:
        return await update.message.reply_text("No active subscription.")
    expires = datetime.fromisoformat(row[0])
    now = datetime.utcnow()
    days = max((expires - now).days, 0)
    await update.message.reply_text(texts[lang]["period"].format(date=expires.date(), days=days))

async def auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    uid = update.effective_user.id
    if ctx.args[0] == OWNER_PASSWORD:
        cur.execute("INSERT OR IGNORE INTO owner_sessions VALUES(?)",(uid,))
        conn.commit()
        await update.message.reply_text(texts["en"]["auth_ok"])
    else:
        await update.message.reply_text(texts["en"]["auth_fail"])

async def help_owner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    await update.message.reply_text(texts["en"]["help"])

async def code_use(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    chat_id = update.effective_chat.id
    code = ctx.args[0]
    lang = user_lang.get(update.effective_user.id, "en")
    row = cur.execute("SELECT days,created_at FROM codes WHERE code=?", (code,)).fetchone()
    if not row:
        return await update.message.reply_text(texts[lang]["no_codes"])
    days, _ = row
    cnt = cur.execute(
        "SELECT COUNT(*) FROM codes_usage WHERE chat_id=? AND code=?", (chat_id, code)
    ).fetchone()[0]
    if cnt >= 1:
        return await update.message.reply_text(texts[lang]["used_before"])
    now = datetime.utcnow()
    user_row = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (update.effective_user.id,)).fetchone()
    expires = datetime.fromisoformat(user_row[0]) if user_row else now
    new_exp = (expires + timedelta(days=days)) if expires > now else now + timedelta(days=days)
    cur.execute("REPLACE INTO users VALUES (?,?,?,1)",
                (update.effective_user.id, update.effective_user.username, new_exp.isoformat()))
    cur.execute("INSERT INTO codes_usage VALUES (?,?,?)", (chat_id, code, now.isoformat()))
    conn.commit()
    await update.message.reply_text(texts[lang]["used_code"].format(days=days, date=new_exp.date()))

async def scode_define(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    if len(ctx.args) != 2 or not re.fullmatch(r"\d{6}", ctx.args[0]) or not ctx.args[1].isdigit():
        return await update.message.reply_text(texts["en"]["invalid_sc"])
    code, days = ctx.args[0], int(ctx.args[1])
    now = datetime.utcnow().isoformat()
    cur.execute("REPLACE INTO codes VALUES (?,?,?)", (code, days, now))
    conn.commit()
    await update.message.reply_text(texts["en"]["code_set"].format(code=code, days=days))

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    rows = cur.execute("SELECT user_id, username, expires_at, is_active FROM users").fetchall()
    msg = f"Total users: {total}\nActive users: {active}\n\n"
    for u, un, exp, act in rows:
        msg += f"{un or ''} (ID {u}) – Expires {exp[:10]} Active:{bool(act)}\n"
    await update.message.reply_text(msg)

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ctx.args or not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    text = " ".join(ctx.args)
    for (u,) in cur.execute("SELECT user_id FROM users WHERE is_active=1").fetchall():
        try:
            await ctx.application.bot.send_message(u, text)
        except:
            pass
    await update.message.reply_text("Broadcast sent.")

async def records(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not cur.execute("SELECT 1 FROM owner_sessions WHERE user_id=?", (uid,)).fetchone():
        return
    rows = cur.execute(
        "SELECT user_id, username, message, timestamp FROM message_logs ORDER BY timestamp DESC"
    ).fetchall()
    path = "records.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "message", "timestamp"])
        w.writerows(rows)
    await ctx.application.bot.send_document(uid, open(path, "rb"))

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text
    cur.execute(
        "INSERT INTO message_logs (user_id,username,message,timestamp) VALUES (?,?,?,?)",
        (uid, update.effective_user.username or "", txt, datetime.utcnow().isoformat())
    )
    conn.commit()
    row = cur.execute("SELECT expires_at,is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[1] == 0:
        return
    expires = datetime.fromisoformat(row[0])
    if expires < datetime.utcnow():
        cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
        conn.commit()
        return
    src = await detect_language_async(txt)
    outs = []
    for t in {"en", "ko", "zh", "vi", "km"} - {src}:
        tr = await translate_text_async(txt, t)
        outs.append(f"{t}: {tr}")
    await update.message.reply_text("\n".join(outs))

# ── Flask app & callback ───────────────────────────────────────────────────────
app_flask = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)

@app_flask.route("/")
def dashboard():
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = cur.execute(
        "SELECT COUNT(*) FROM users WHERE is_active=1 AND expires_at>?",
        (datetime.utcnow().isoformat(),)
    ).fetchone()[0]
    return render_template_string(
        "<h1>Bot Dashboard</h1><ul><li>Total users: {{total}}</li>"
        "<li>Active: {{active}}</li></ul>", total=total, active=active
    )

@app_flask.route("/healthz")
def healthz():
    return "OK"

@app_flask.route("/callback", methods=["POST"])
def payment_callback():
    return "", 200

# ── Dispatcher & launch ────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("contact", contact))
app.add_handler(CommandHandler("period", period))
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
