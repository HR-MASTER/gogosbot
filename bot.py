#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ── 환경 변수 ─────────────────────────────────────
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
OXAPAY_KEY          = os.getenv("OXAPAY_API_KEY")
OWNER_PASSWORD      = os.getenv("OWNER_PASSWORD")
GOOGLE_API_KEY      = os.getenv("GOOGLE_API_KEY")
OXAPAY_CALLBACK_URL = os.getenv("OXAPAY_CALLBACK_URL")

TRANSLATE_URL       = "https://translation.googleapis.com/language/translate/v2"

# ── DB 설정 ───────────────────────────────────────
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
  user_id    INTEGER PRIMARY KEY,
  username   TEXT,
  expires_at TEXT,
  is_active  INTEGER
);
CREATE TABLE IF NOT EXISTS invoices (
  track_id   TEXT PRIMARY KEY,
  user_id    INTEGER,
  days       INTEGER,
  is_paid    INTEGER DEFAULT 0,
  created_at TEXT
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

user_lang      = {}
owner_sessions = set()

# ── 신규 입금 주소 발급 함수 ───────────────────────
def get_deposit_address(currency: str = "USDT") -> str:
    r = requests.post(
        "https://api.oxapay.com/v1/deposit/address",
        headers={
            "payout_api_key": OXAPAY_KEY,
            "Content-Type":   "application/json"
        },
        json={"currency": currency}
    )
    r.raise_for_status()
    return r.json()["data"]["address"]

# ── 인보이스(payout) 생성 함수 ──────────────────
def create_invoice(amount: float, days: int, user_id: int) -> str:
    # 1) 매번 새로운 입금 주소 발급
    address = get_deposit_address("USDT")

    payload = {
        "address":      address,
        "amount":       amount,
        "currency":     "USDT",
        "network":      "TRC20",
        "callback_url": OXAPAY_CALLBACK_URL,
        "memo":         f"Subscribe+{days}d",
        "description":  f"Subscription extension {days} days"
    }
    r = requests.post(
        "https://api.oxapay.com/v1/payout",
        headers={
            "payout_api_key": OXAPAY_KEY,
            "Content-Type":   "application/json"
        },
        json=payload
    )
    r.raise_for_status()
    data  = r.json()["data"]
    track = data["track_id"]

    # DB에 저장
    cur.execute("""
        INSERT OR IGNORE INTO invoices(track_id,user_id,days,is_paid,created_at)
        VALUES(?,?,?,?,?)
    """, (track, user_id, days, 0, datetime.utcnow().isoformat()))
    conn.commit()

    # 사용자에게 인보이스 링크 제공
    return f"https://oxapay.com/pay/{track}"

# ── 결제 완료 자동 감시 & 구독 연장 ───────────────
def payment_watcher(app):
    while True:
        rows = cur.execute("""
            SELECT track_id, user_id, days
              FROM invoices
             WHERE is_paid=0
        """).fetchall()

        for track, uid, days in rows:
            r = requests.get(
                f"https://api.oxapay.com/v1/payout/{track}",
                headers={"payout_api_key": OXAPAY_KEY}
            )
            if r.status_code == 200 and r.json().get("data",{}).get("status") == "completed":
                # 구독 연장
                old = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (uid,)).fetchone()
                if old:
                    exp_new = datetime.fromisoformat(old[0]) + timedelta(days=days)
                else:
                    exp_new = datetime.utcnow() + timedelta(days=days)
                cur.execute("""
                    UPDATE users
                       SET expires_at=?, is_active=1
                     WHERE user_id=?
                """, (exp_new.isoformat(), uid))
                cur.execute("UPDATE invoices SET is_paid=1 WHERE track_id=?", (track,))
                conn.commit()
                app.bot.send_message(
                    chat_id=uid,
                    text=f"✅ Your subscription has been extended until {exp_new.date()}."
                )
        time.sleep(60)

# ── 핸들러 & UI ─────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
      [InlineKeyboardButton("English",    callback_data="lang_en"),
       InlineKeyboardButton("한국어",       callback_data="lang_ko")],
      [InlineKeyboardButton("中文",         callback_data="lang_zh"),
       InlineKeyboardButton("Tiếng Việt",  callback_data="lang_vi")],
      [InlineKeyboardButton("ភាសាខ្មែរ",   callback_data="lang_km")]
    ]
    await update.message.reply_text(
        "Please select your language:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def choose_language(update, ctx):
    q    = update.callback_query
    lang = q.data.split("_",1)[1]
    user_lang[q.from_user.id] = lang
    await q.answer()
    await q.edit_message_text(texts[lang]["help"])

async def help_cmd(update, ctx):
    lang = user_lang.get(update.effective_user.id, "en")
    await update.message.reply_text(texts[lang]["help"])

async def register(update, ctx):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    exp  = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES(?,?,?,1)",
                (uid, update.effective_user.username, exp.isoformat()))
    conn.commit()
    await update.message.reply_text(texts[lang]["registered"].format(date=exp.date()))

async def stop(update, ctx):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (uid,))
    conn.commit()
    await update.message.reply_text(texts[lang]["stopped"])

async def extend(update, ctx):
    lang = user_lang.get(update.effective_user.id, "en")
    uid  = update.effective_user.id
    kb = [
      [InlineKeyboardButton(texts[lang]["m1"], url=create_invoice(30,30,uid))],
      [InlineKeyboardButton(texts[lang]["y1"], url=create_invoice(365,365,uid))]
    ]
    await update.message.reply_text(
        texts[lang]["extend"],
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def translate_message(update, ctx):
    uid = update.effective_user.id
    row = cur.execute("SELECT is_active FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or row[0] == 0:
        return
    txt     = update.message.text
    src     = detect_language(txt)
    targets = {"en","ko","zh","vi","km"} - {src}
    outs    = [f"{t}: {translate_text(txt,t)}" for t in targets]
    cur.execute("INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
                (uid, txt, datetime.utcnow().isoformat()))
    conn.commit()
    await update.message.reply_text("\n".join(outs))

# ── Owner Commands (slash) ────────────────────────
def is_owner(uid): return uid in owner_sessions

async def owner_auth(update, ctx):
    if not ctx.args or ctx.args[0] != OWNER_PASSWORD:
        return await update.message.reply_text("Usage: /owner <password>")
    owner_sessions.add(update.effective_user.id)
    await update.message.reply_text("✅ Owner authentication successful")

async def owner_add_code(update, ctx):
    if not is_owner(update.effective_user.id): return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /addcode <code> <days>")
    code, days = ctx.args[0], int(ctx.args[1])
    cur.execute("INSERT INTO codes VALUES(?,?,?)",
                (code, days, update.effective_user.username))
    conn.commit()
    await update.message.reply_text(f"✅ Code {code} added for {days} days")

async def owner_list_codes(update, ctx):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("SELECT code,days,created_by FROM codes").fetchall()
    text = "\n".join(f"{c} / {d} days by {b}" for c,d,b in rows) or "None"
    await update.message.reply_text(text)

async def owner_delete(update, ctx):
    if not is_owner(update.effective_user.id): return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /delete <user|code> <value>")
    typ, val = ctx.args[0], ctx.args[1]
    if typ == "user":
        cur.execute("DELETE FROM users WHERE user_id=?", (int(val),))
    else:
        cur.execute("DELETE FROM codes WHERE code=?", (val,))
    conn.commit()
    await update.message.reply_text("✅ Deletion completed")

async def owner_list_users(update, ctx):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("SELECT user_id,username,expires_at FROM users").fetchall()
    text = "\n".join(f"{u}/{n}/{e[:10]}" for u,n,e in rows) or "None"
    await update.message.reply_text(text)

async def owner_chats(update, ctx):
    if not is_owner(update.effective_user.id): return
    rows = cur.execute("""
        SELECT u.username, c.message, c.timestamp
          FROM chats c JOIN users u ON c.user_id=u.user_id
         ORDER BY c.id DESC LIMIT 1000
    """).fetchall()
    text = "\n".join(f"{usr}: {msg} @ {ts[:19]}" for usr,msg,ts in rows) or "None"
    await update.message.reply_text(text)

async def owner_payout(update, ctx):
    if not is_owner(update.effective_user.id): return
    if len(ctx.args) < 4:
        return await update.message.reply_text(
            "Usage: /payout <addr> <amt> <cur> <net> [cb] [memo] [desc]"
        )
    addr, amt, cur_, net = ctx.args[0], float(ctx.args[1]), ctx.args[2], ctx.args[3]
    callback = ctx.args[4] if len(ctx.args) > 4 else OXAPAY_CALLBACK_URL
    memo     = ctx.args[5] if len(ctx.args) > 5 else ""
    desc     = ctx.args[6] if len(ctx.args) > 6 else ""
    payload = {
        "address":      addr,
        "amount":       amt,
        "currency":     cur_,
        "network":      net,
        "callback_url": callback,
        "memo":         memo,
        "description":  desc
    }
    r = requests.post("https://api.oxapay.com/v1/payout",
                      headers={"payout_api_key": OXAPAY_KEY},
                      json=payload)
    try:
        r.raise_for_status()
        d = r.json().get("data",{})
        await update.message.reply_text(
            f"Payout: track {d.get('track_id')}, status {d.get('status')}"
        )
    except Exception as e:
        await update.message.reply_text(f"Payout error: {e}\n{r.text}")

async def owner_commands(update, ctx):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text(
        "/owner <pwd>            — Owner auth\n"
        "/addcode <code> <days>  — Add a code\n"
        "/listcodes              — List codes\n"
        "/delete <u|code> <v>    — Delete user/code\n"
        "/listusers              — List users\n"
        "/chats                  — Show last 1000 chats\n"
        "/payout <addr> <amt> <cur> <net> [cb] [memo] [desc]\n"
        "                        — Generate payout\n"
        "/commands               — Show this list"
    )

# ── Dispatcher 등록 & 실행 ─────────────────────────
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

# user
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_language, pattern=r"^lang_"))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("register", register))
app.add_handler(CommandHandler("stop", stop))
app.add_handler(CommandHandler("extend", extend))
app.add_handler(CommandHandler("code", code_user))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_message))

# owner
app.add_handler(CommandHandler("owner", owner_auth))
app.add_handler(CommandHandler("addcode", owner_add_code))
app.add_handler(CommandHandler("listcodes", owner_list_codes))
app.add_handler(CommandHandler("delete", owner_delete))
app.add_handler(CommandHandler("listusers", owner_list_users))
app.add_handler(CommandHandler("chats", owner_chats))
app.add_handler(CommandHandler("payout", owner_payout))
app.add_handler(CommandHandler("commands", owner_commands))

# Flask 대시보드
app_flask = Flask(__name__)
TEMPLATE = """
<h2>Users</h2>
<ul>{% for u,n,e in users %}<li>{{u}} / {{n}} / {{e[:10]}}</li>{% endfor %}</ul>
<h2>Invoices</h2>
<ul>{% for t,u,d,p,c in inv %}<li>{{t}} / {{u}} / {{d}} days / paid:{{p}}</li>{% endfor %}</ul>
<h2>Codes</h2>
<ul>{% for c,d,b in codes %}<li>{{c}} / {{d}} days by {{b}}</li>{% endfor %}</ul>
"""
@app_flask.route("/dashboard")
def dashboard():
    users  = cur.execute("SELECT user_id,username,expires_at FROM users").fetchall()
    inv    = cur.execute("SELECT track_id,user_id,days,is_paid,created_at FROM invoices").fetchall()
    codes  = cur.execute("SELECT code,days,created_by FROM codes").fetchall()
    return render_template_string(TEMPLATE, users=users, inv=inv, codes=codes)

def main():
    threading.Thread(target=lambda: payment_watcher(app), daemon=True).start()
    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=5000), daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
