#!/usr/bin/env python3
import os, sqlite3, requests, threading
from datetime import datetime, timedelta
from flask import Flask, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.cloud import translate_v3 as translate

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")
RONGRID_KEY       = os.getenv("RONGRID_API_KEY")
OWNER_PASSWORD    = os.getenv("OWNER_PASSWORD")

client = translate.TranslationServiceClient()
PARENT = f"projects/{GOOGLE_PROJECT_ID}/locations/global"

conn = sqlite3.connect("bot.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users(user_id PRIMARY KEY, username, expires_at, is_active);
CREATE TABLE IF NOT EXISTS codes(code PRIMARY KEY, days, created_by);
CREATE TABLE IF NOT EXISTS chats(id PRIMARY KEY AUTOINCREMENT, user_id, message, timestamp);
""")
conn.commit()

def detect_language(text):
    resp = client.detect_language(content=text, mime_type="text/plain", parent=PARENT)
    return resp.languages[0].language_code

def translate_text(text, target):
    resp = client.translate_text(request={
        "parent": PARENT,
        "contents": [text],
        "mime_type": "text/plain",
        "target_language_code": target
    })
    return resp.translations[0].translated_text

def create_invoice(amount, days):
    r = requests.post("https://api.rongrid.io/v1/invoices",
        headers={"Authorization":f"Bearer {RONGRID_KEY}","Content-Type":"application/json"},
        json={"amount":amount,"currency":"USDT","metadata":{"days":days}}
    )
    return r.json()["data"]["hosted_url"]

async def start(u, ctx):
    kb = [[InlineKeyboardButton("사용법", callback_data="help")]]
    await u.message.reply_text("번역 봇 시작", reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(u, ctx):
    await u.message.reply_text(
        "지원 언어: ko, zh, vi, km\n"
        "자동 감지 후 나머지 3개국어 번역"
    )

async def register(u, ctx):
    uid, name = u.effective_user.id, u.effective_user.username
    exp = datetime.utcnow() + timedelta(days=7)
    cur.execute("REPLACE INTO users VALUES(?,?,?,1)", (uid,name,exp.isoformat()))
    conn.commit()
    await u.message.reply_text(f"등록 완료: {exp.date()}까지")

async def stop(u, ctx):
    cur.execute("UPDATE users SET is_active=0 WHERE user_id=?", (u.effective_user.id,))
    conn.commit()
    await u.message.reply_text("번역 중단")

async def extend(u, ctx):
    kb = [
      [InlineKeyboardButton("1개월(30USDT)", url=create_invoice(30,30))],
      [InlineKeyboardButton("1년(300USDT)", url=create_invoice(300,365))]
    ]
    await u.message.reply_text("연장 옵션", reply_markup=InlineKeyboardMarkup(kb))

async def code_user(u, ctx):
    if not ctx.args: return await u.message.reply_text("코드 입력")
    row = cur.execute("SELECT days FROM codes WHERE code=?", (ctx.args[0],)).fetchone()
    if not row: return await u.message.reply_text("유효하지 않은 코드")
    days = row[0]; uid, name = u.effective_user.id, u.effective_user.username
    old = cur.execute("SELECT expires_at FROM users WHERE user_id=?", (uid,)).fetchone()
    new = datetime.fromisoformat(old[0]) + timedelta(days=days) if old else datetime.utcnow()+timedelta(days=days)
    cur.execute("REPLACE INTO users VALUES(?,?,?,1)", (uid,name,new.isoformat()))
    conn.commit()
    await u.message.reply_text(f"{days}일 연장 (만료:{new.date()})")

async def translate_message(u, ctx):
    uid = u.effective_user.id
    if not cur.execute("SELECT is_active FROM users WHERE user_id=?", (uid,)).fetchone(): return
    txt = u.message.text; src = detect_language(txt)
    targets = {"ko","zh","vi","km"} - {src}
    res = [f"{lang}: {translate_text(txt,lang)}" for lang in targets]
    cur.execute("INSERT INTO chats(user_id,message,timestamp) VALUES(?,?,?)",
                (uid,txt,datetime.utcnow().isoformat()))
    conn.commit()
    await u.message.reply_text("\n".join(res))

owner_sessions=set()
async def owner_auth(u,ctx):
    if u.message.text.lstrip(".")==OWNER_PASSWORD:
        owner_sessions.add(u.effective_user.id); await u.message.reply_text("소유자 인증")
    else: await u.message.reply_text("인증 실패")

def is_owner(id): return id in owner_sessions

async def owner_register(u,ctx):
    if not is_owner(u.effective_user.id): return
    _,code,days = u.message.text.split()
    cur.execute("INSERT INTO codes VALUES(?,?,?)",(code,int(days),u.effective_user.username))
    conn.commit(); await u.message.reply_text(f"코드 {code} 저장")

async def owner_list_codes(u,ctx):
    if not is_owner(u.effective_user.id): return
    rows=cur.execute("SELECT code,days,created_by FROM codes").fetchall()
    await u.message.reply_text("\n".join(f"{c}/{d}일 by {b}" for c,d,b in rows) or "없음")

async def owner_delete(u,ctx):
    if not is_owner(u.effective_user.id): return
    _,typ,val=u.message.text.split()
    if typ=="user": cur.execute("DELETE FROM users WHERE user_id=?",(int(val),))
    else:       cur.execute("DELETE FROM codes WHERE code=?",(val,))
    conn.commit(); await u.message.reply_text("삭제 완료")

async def owner_list_users(u,ctx):
    if not is_owner(u.effective_user.id): return
    rows=cur.execute("SELECT user_id,username,expires_at FROM users").fetchall()
    await u.message.reply_text("\n".join(f"{u}/{n}/{e[:10]}" for u,n,e in rows) or "없음")

async def owner_chats(u,ctx):
    if not is_owner(u.effective_user.id): return
    rows=cur.execute("SELECT user_id,message,timestamp FROM chats").fetchall()
    await u.message.reply_text("\n".join(f"{u}: {m}@{t[:19]}" for u,m,t in rows[-20:]) or "없음")

async def owner_commands(u,ctx):
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text(
        ".ownerss501\n.등록 <코드> <기간>\n.코드\n.삭제 <user|code> <값>\n.목록\n.채팅\n.명령어"
    )

app_flask=Flask(__name__)
@app_flask.route("/dashboard")
def dash():
    us=cur.execute("SELECT user_id,username,expires_at FROM users").fetchall()
    cs=cur.execute("SELECT code,days,created_by FROM codes").fetchall()
    return render_template_string(
        "<h2>Users</h2><ul>{% for u,n,e in users %}<li>{{u}}/{{n}}/{{e[:10]}}</li>{% endfor %}</ul>"
        "<h2>Codes</h2><ul>{% for c,d,b in codes %}<li>{{c}}/{{d}}일 by {{b}}</li>{% endfor %}</ul>",
        users=us, codes=cs
    )

def main():
    bot=ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("start",start))
    bot.add_handler(CommandHandler("help",help_cmd))
    bot.add_handler(CommandHandler("등록",register))
    bot.add_handler(CommandHandler("종료",stop))
    bot.add_handler(CommandHandler("연장",extend))
    bot.add_handler(CommandHandler("코드",code_user))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,translate_message))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.ownerss501$"),owner_auth))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.등록 "),owner_register))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.코드$"),owner_list_codes))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.삭제 "),owner_delete))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.목록$"),owner_list_users))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.채팅$"),owner_chats))
    bot.add_handler(MessageHandler(filters.Regex(r"^\.명령어$"),owner_commands))
    threading.Thread(target=lambda:app_flask.run(host="0.0.0.0",port=5000)).start()
    bot.run_polling()

if __name__=="__main__":
    main()
