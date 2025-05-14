# bot.py

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
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# 환경 변수
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
RONGRID_KEY    = os.getenv("RONGRID_API_KEY")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

# DB 연결
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  expires_at  TEXT,
  is_active   INTEGER
);
CREATE TABLE IF NOT EXISTS codes (
  code        TEXT PRIMARY KEY,
  days        INTEGER,
  created_by  TEXT
);
CREATE TABLE IF NOT EXISTS chats (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  message     TEXT,
  timestamp   TEXT
);
""")
conn.commit()

# 번역 함수
def detect_language(text: str) -> str:
    resp = requests.post(
        f"{TRANSLATE_URL}/detect",
        params={"key": GOOGLE_API_KEY},
        data={"q": text}
    )
    return resp.json()["data"]["detections"][0][0]["language"]

def translate_text(text: str, target: str) -> str:
    resp = requests.post(
        TRANSLATE_URL,
        params={"key": GOOGLE_API_KEY},
        json={"q": text, "target": target, "format": "text"}
    )
    return resp.json()["data"]["translations"][0]["translatedText"]

# 결제 함수 (Rongrid)
def create_invoice(amount: float, days: int) -> str:
    url = "https://api.rongrid.io/v1/invoices"
    headers = {
        "Authorization": f"Bearer {RONGRID_KEY}",
        "Content-Type": "application/json"
    }
    data = {"amount": amount, "currency": "USDT", "metadata": {"days": days}}
    r = requests.post(url, headers=headers, json=data)
    return r.json()["data"]["hosted_url"]

# 봇 핸들러
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("사용법", callback_data="help")]]
    await update.message.reply_text("번역 봇 시작", reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "지원 언어: ko, zh, vi, km\n"
        "자동 감지 후 나머지 3개 언어로 번역"
    )

async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid, uname = update.effective_user.id, update.effective_user.username
    expires = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)", (uid, uname, expires.isoformat()))
    conn.commit()
    await update.message.reply_text(f"등록 완료: {expires.date()}까지")

async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cur.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (uid,))
    conn.commit()
    await update.message.reply_text("번역 기능 중단됨")

async def extend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("1개월(30 USDT)", url=create_invoice(30, 30))],
        [InlineKeyboardButton("1년(300 USDT)", url=create_invoice(300, 365))]
    ]
    await update.message.reply_text("연장 옵션 선택", reply_markup=InlineKeyboardMarkup(kb))

async def code_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("코드를 입력하세요.")
    code = ctx.args[0]
    row = cur.execute("SELECT days FROM codes WHERE code = ?", (code,)).fetchone()
    if not row:
        return await update.message.reply_text("유효하지 않은 코드")
    days = row[0]
    uid, uname = update.effective_user.id, update.effective_user.username
    old = cur.execute("SELECT expires_at FROM users WHERE user_id = ?", (uid,)).fetchone()
    new_exp = (datetime.fromisoformat(old[0]) + timedelta(days=days)) if old else (datetime.utcnow() + timedelta(days=days))
    cur.execute("REPLACE INTO users VALUES (?, ?, ?, 1)", (uid, uname, new_exp.isoformat()))
    conn.commit()
    await update.message.reply_text(f"코드 적용: {days}일 연장 (만료: {new_exp.date()})")

async def translate_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    usr = cur.execute("SELECT is_active FROM users WHERE user_id = ?", (uid,)).fetchone()
    if not usr or usr[0] == 0:
        return
    txt = update.message.text
    src = detect_language(txt)
    targets = {"ko", "zh", "vi", "km"} - {src}
    results = [f"{lang}: {translate_text(txt, lang)}" for lang in targets]
    cur.execute(
        "INSERT INTO chats(user_id, message, timestamp) VALUES (?, ?, ?)",
        (uid, txt, datetime.utcnow().isoformat())
    )
    conn.commit()
    await update.message.reply_text("\n".join(results))

# 소유자 인증 및 명령어
owner_sessions = set()

async def owner_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pw = update.message.text.lstrip(".")
    if pw == OWNER_PASSWORD:
        owner_sessions.add(update.effective_user.id)
        await update.message.reply_text("소유자 인증 완료")
    else:
        await update.message.reply_text("인증 실패")

def is_owner(user_id: int) -> bool:
    return user_id in owner_sessions

async def owner_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    _, code, days = update.message.text.split()
    cur.execute("INSERT INTO codes VALUES (?, ?, ?)", (code, int(days), update.effective_user.username))
    conn.commit()
    await update.message.reply_text(f"코드 {code} 저장 ({days}일)")

async def owner_list_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    rows = cur.execute("SELECT code, days, created_by FROM codes").fetchall()
    text = "\n".join(f"{c} / {d}일 by {b}" for c, d, b in rows) or "없음"
    await update.message.reply_text(text)

async def owner_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    _, typ, val = update.message.text.split()
    if typ == "user":
        cur.execute("DELETE FROM users WHERE user_id = ?", (int(val),))
    else:
        cur.execute("DELETE FROM codes WHERE code = ?", (val,))
    conn.commit()
    await update.message.reply_text("삭제 완료")

async def owner_list_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    rows = cur.execute("SELECT user_id, username, expires_at FROM users").fetchall()
    text = "\n".join(f"{u} / {n} / {e[:10]}" for u, n, e in rows) or "없음"
    await update.message.reply_text(text)

async def owner_chats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    rows = cur.execute("SELECT user_id, message, timestamp FROM chats").fetchall()
    text = "\n".join(f"{u}: {m} @ {t[:19]}" for u, m, t in rows[-20:]) or "없음"
    await update.message.reply_text(text)

async def owner_commands(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    cmds = (
        ".ownerss501\n"
        ".등록 <코드> <기간>\n"
        ".코드\n"
        ".삭제 <user|code> <값>\n"
        ".목록\n"
        ".채팅\n"
        ".명령어"
    )
    await update.message.reply_text(cmds)

# Flask 대시보드
app_flask = Flask(__name__)
TEMPLATE = """
<h2>Users</h2>
<ul>{% for u,n,e in users %}<li>{{u}} / {{n}} / {{e[:10]}}</li>{% endfor %}</ul>
<h2>Codes</h2>
<ul>{% for c,d,b in codes %}<li>{{c}} / {{d}}일 by {{b}}</li>{% endfor %}</ul>
"""

@app_flask.route("/dashboard")
def dashboard():
    users = cur.execute("SELECT user_id, username, expires_at FROM users").fetchall()
    codes = cur.execute("SELECT code, days, created_by FROM codes").fetchall()
    return render_template_string(TEMPLATE, users=users, codes=codes)

def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help", help_cmd))
    bot.add_handler(CommandHandler("등록", register))
    bot.add_handler(CommandHandler("종료", stop))
    bot.add_handler(CommandHandler("연장", extend))
    bot.add_handler(CommandHandler("코드", code_user))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.ownerss501$"), owner_auth))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.등록 "), owner_register))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.코드$"), owner_list_codes))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.삭제 "), owner_delete))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.목록$"), owner_list_users))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.채팅$"), owner_chats))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.명령어$"), owner_commands))

    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=5000)).start()
    bot.run_polling()

if __name__ == "__main__":
    main()
