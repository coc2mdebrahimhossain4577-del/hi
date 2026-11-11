# -*- coding: utf-8 -*-
"""
Telegram Shop Bot - COMPLETE EDITION
------------------------------------
Install :  pip install "python-telegram-bot==21.6"
Run     :  BOT_TOKEN=123:ABC python shop_bot.py
Setup   :  put your numeric Telegram ID in ADMIN_IDS (send /id to the bot to see it).
Payment :  bKash / Nagad / Rocket -> user sends  AMOUNT TRXID -> admin Approve / Reject
"""
import os, io, math, random, sqlite3, time, asyncio, logging, shutil, tempfile
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton as IB, InlineKeyboardMarkup as IM
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

BOT_TOKEN = os.getenv("BOT_TOKEN", "9276877776:AAHJRNGGfVHW08EZepSgxPjAp8MO_KEOxE4").strip()
# Admin IDs: env ADMIN_IDS="111,222" অথবা নিচের ডিফল্ট বদলান
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "8949881011").replace(" ", "").split(",") if x.isdigit()}
DB_FILE = "shop.db"
LOW_STOCK = 3             # shown in the dashboard "Low Stock" list
ALERT_PCT = 0.8           # alert admins when 80% of a product's stock is sold
PAGE = 8                  # buttons per page in Buy Product
logging.basicConfig(level=logging.INFO)

# ============================ DATABASE ============================
def _run(sql, args=()):
    con = sqlite3.connect(DB_FILE, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        con.commit()
        data = cur.fetchall() if cur.description else []
        return data, cur.lastrowid
    finally:
        con.close()

def fetch(sql, a=()): return _run(sql, a)[0]
def fetch1(sql, a=()):
    r = fetch(sql, a)
    return r[0] if r else None
def exe(sql, a=()): return _run(sql, a)[1]

def init_db():
    con = sqlite3.connect(DB_FILE)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, balance REAL DEFAULT 0, joined REAL,
        banned INTEGER DEFAULT 0, ref_by INTEGER, has_dep INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, cat_id INTEGER, name TEXT, price REAL, info TEXT);
    CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, data TEXT, sold INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, product_name TEXT, price REAL, data TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, amount REAL, trx TEXT, status TEXT DEFAULT 'pending', ts REAL);
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, status TEXT DEFAULT 'open', ts REAL);
    CREATE TABLE IF NOT EXISTS replacements(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, order_id INTEGER, reason TEXT, status TEXT DEFAULT 'pending', ts REAL);
    CREATE TABLE IF NOT EXISTS saved(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, file_id TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    # migrate databases made by the older version of this bot
    for col in ("banned INTEGER DEFAULT 0", "ref_by INTEGER", "has_dep INTEGER DEFAULT 0"):
        try: con.execute("ALTER TABLE users ADD COLUMN " + col)
        except sqlite3.OperationalError: pass
    con.commit()
    con.close()

DEFAULTS = {
    "welcome": "🏠 WELCOME TO DIGITAL STORE!\n\n✨ Premium Digital Accounts & Services\n⚡ Instant Delivery (24/7)\n🛡 100% Safe & Trusted with Warranty\n💳 Deposit with bKash / Nagad / Rocket\n\n👇 CHOOSE AN OPTION FROM THE MENU BELOW 👇",
    "bkash": "01XXXXXXXXX", "nagad": "01XXXXXXXXX", "rocket": "01XXXXXXXXX",
    "support": "Not set yet", "admin_contact": "", "free_numbers": "",
    "bonus_percent": "5", "bonus_min": "50", "ref_percent": "5", "warranty_hours": "72",
    "proxy": "Not set", "order_group": "", "support_group": "", "history_group": "",
}
SETTING_LABELS = {
    "bkash": "bKash number", "nagad": "Nagad number", "rocket": "Rocket number",
    "support": "Support text", "admin_contact": "Admin contact (@user / link)",
    "welcome": "Welcome message", "free_numbers": "Free numbers (1 per line)",
    "bonus_percent": "Deposit bonus %", "bonus_min": "Min deposit for bonus",
    "ref_percent": "Referral bonus %", "warranty_hours": "Replace warranty (hours)",
    "proxy": "Proxy link", "order_group": "Order group ID",
    "support_group": "Support group ID", "history_group": "History group ID",
}
NUMERIC = {"bonus_percent", "bonus_min", "ref_percent", "warranty_hours"}
GROUPS = {"order_group", "support_group", "history_group"}

def get_set(k):
    r = fetch1("SELECT value FROM settings WHERE key=?", (k,))
    return r["value"] if r else DEFAULTS.get(k, "")
def put_set(k, v):
    exe("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))

def fmt(x): return f"{x:g}"
def is_admin(uid): return uid in ADMIN_IDS
def stock_count(pid): return fetch1("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=0", (pid,))["c"]
def is_banned(uid):
    r = fetch1("SELECT banned FROM users WHERE id=?", (uid,))
    return bool(r and r["banned"])
def register(u, ref=None):
    if fetch1("SELECT 1 FROM users WHERE id=?", (u.id,)): return False
    rb = ref if (ref and ref != u.id and fetch1("SELECT 1 FROM users WHERE id=?", (ref,))) else None
    exe("INSERT INTO users(id,name,joined,ref_by) VALUES(?,?,?,?)", (u.id, u.full_name, time.time(), rb))
    return True

# ---------- bonus / referral ----------
def _num(key, default=0.0):
    try: return float(get_set(key))
    except Exception: return default
def bonus_for(amount):
    if amount < _num("bonus_min"): return 0.0
    return round(amount * _num("bonus_percent") / 100, 2)
def bonus_table():
    pct, mn = _num("bonus_percent"), _num("bonus_min")
    if pct <= 0: return "No deposit bonus right now."
    steps = [mn, 100, 200, 300, 500, 1000]
    seen, lines = set(), []
    for s in steps:
        if s in seen or s < mn: continue
        seen.add(s); lines.append(f"✅ ৳{fmt(s)} deposit → ৳{fmt(round(s * pct / 100, 2))} bonus")
    return "\n".join(lines)

def approve_deposit(d):
    """Credit balance + bonus, and referral bonus on the friend's first deposit."""
    bonus = bonus_for(d["amount"])
    u = fetch1("SELECT * FROM users WHERE id=?", (d["user_id"],))
    ref_bonus, ref = 0.0, None
    if u and not u["has_dep"] and u["ref_by"]:
        ref = u["ref_by"]
        ref_bonus = round(d["amount"] * _num("ref_percent") / 100, 2)
    exe("UPDATE deposits SET status='approved' WHERE id=?", (d["id"],))
    exe("UPDATE users SET balance=balance+?, has_dep=1 WHERE id=?", (d["amount"] + bonus, d["user_id"]))
    if ref and ref_bonus > 0:
        exe("UPDATE users SET balance=balance+? WHERE id=?", (ref_bonus, ref))
    return bonus, ref_bonus, ref

# ---------- purchase (single transaction, no double-selling) ----------
def buy_tx(uid, pid, n):
    con = sqlite3.connect(DB_FILE, isolation_level=None, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        prod = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not prod:
            con.execute("ROLLBACK"); return "noprod", None, None, 0
        items = con.execute("SELECT * FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?", (pid, n)).fetchall()
        if len(items) < n:
            con.execute("ROLLBACK"); return "stock", prod, None, 0
        cost = prod["price"] * n
        bal = con.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()["balance"]
        if bal < cost:
            con.execute("ROLLBACK"); return "bal", prod, bal, cost
        con.execute("UPDATE users SET balance=balance-? WHERE id=?", (cost, uid))
        data, oids = [], []
        for it in items:
            con.execute("UPDATE stock SET sold=1 WHERE id=?", (it["id"],))
            cur = con.execute("INSERT INTO orders(user_id,product_id,product_name,price,data,ts) VALUES(?,?,?,?,?,?)",
                              (uid, pid, prod["name"], prod["price"], it["data"], time.time()))
            data.append(it["data"]); oids.append(cur.lastrowid)
        con.execute("COMMIT")
        return "ok", prod, (data, oids), cost
    except Exception:
        try: con.execute("ROLLBACK")
        except Exception: pass
        raise
    finally:
        con.close()

# ============================ KEYBOARDS ============================
B_BUY, B_MAIL, B_SHEET = "🛒 Buy Product", "📧 Mail Code", "📝 Save Sheet"
B_DEP, B_NAME, B_NEXT = "🏦 Deposit", "🪪 Name Generator", "⏭ Next Page"
B_ADMIN = "👑 Admin Panel"
B_SUP, B_BAL, B_PEND = "☎️ Support Admin", "💼 Balance", "⏳ My Pending Order"
B_FREE, B_REPL, B_ORD, B_REF = "📞 Free Number", "🔄 Replace Product", "📜 My Orders", "🎁 Referral & Bonus"
B_BACK = "⬅️ Back to Main Menu"
A_SHOP, A_MONEY, A_HUB, A_USERS = "🏪 Shop", "💰 Money", "📦 Order Hub", "👥 Users"
A_SET, A_BACKUP, A_USER = "⚙️ Settings", "💾 Backup", "🙋 User Menu"
MENU = {B_BUY, B_MAIL, B_SHEET, B_DEP, B_NAME, B_NEXT, B_ADMIN, B_SUP, B_BAL, B_PEND, B_FREE, B_REPL,
        B_ORD, B_REF, B_BACK, A_SHOP, A_MONEY, A_HUB, A_USERS, A_SET, A_BACKUP, A_USER}

def user_kb(uid):
    rows = [[B_BUY], [B_MAIL, B_SHEET], [B_DEP, B_NAME], [B_NEXT]]
    if is_admin(uid): rows.append([B_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)
PAGE2_KB = ReplyKeyboardMarkup([[B_SUP], [B_BAL, B_PEND], [B_FREE, B_REPL], [B_ORD, B_REF], [B_BACK]], resize_keyboard=True)
ADMIN_KB = ReplyKeyboardMarkup([[A_SHOP, A_MONEY], [A_HUB, A_USERS], [A_SET, A_BACKUP], [A_USER]], resize_keyboard=True)

CANCEL = [IB("❌ Cancel", callback_data="cancel")]

def paged(rows, page, size=PAGE):
    pages = max(1, math.ceil(len(rows) / size))
    page = min(max(page, 0), pages - 1)
    return rows[page * size:(page + 1) * size], page, pages
def nav_row(prefix, page, pages):
    r = []
    if page > 0: r.append(IB("⬅️ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < pages - 1: r.append(IB("Next ➡️", callback_data=f"{prefix}:{page + 1}"))
    return r

def sections_markup(page=0):
    rows, page, pages = paged(fetch("SELECT * FROM categories"), page)
    kb = [[IB(c["name"], callback_data=f"cat:{c['id']}:0")] for c in rows]
    nav = nav_row("sec", page, pages)
    if nav: kb.append(nav)
    kb.append(CANCEL)
    return IM(kb)

def pick_list(sql, prefix, label="name"):
    kb = [[IB(r[label], callback_data=f"{prefix}:{r['id']}")] for r in fetch(sql)]
    kb.append(CANCEL)
    return IM(kb)

def settings_markup(keys, back=None):
    kb, row = [], []
    for k in keys:
        row.append(IB(SETTING_LABELS[k], callback_data=f"ad:set:{k}"))
        if len(row) == 2: kb.append(row); row = []
    if row: kb.append(row)
    if back: kb.append([IB("⬅️ Back", callback_data=back)])
    return IM(kb)

# ============================ NAME GENERATOR ============================
NAMES = {
    "BD": ("🇧🇩 Bangladesh",
           ["Rahim", "Karim", "Hasan", "Tanvir", "Imran", "Sakib", "Nayeem", "Rafi", "Arif", "Shuvo", "Mehedi", "Rakib"],
           ["Jannat", "Nusrat", "Sadia", "Farzana", "Tania", "Mim", "Sumaiya", "Rima", "Nabila", "Sharmin", "Mitu", "Ritu"],
           ["Ahmed", "Hossain", "Rahman", "Islam", "Khan", "Chowdhury", "Uddin", "Sarkar", "Mia", "Sheikh"]),
    "IN": ("🇮🇳 India",
           ["Aarav", "Vivaan", "Arjun", "Rohan", "Karan", "Aditya", "Rahul", "Amit"],
           ["Ananya", "Priya", "Diya", "Neha", "Pooja", "Isha", "Kavya", "Riya"],
           ["Sharma", "Verma", "Patel", "Singh", "Gupta", "Mehta", "Reddy", "Das"]),
    "PK": ("🇵🇰 Pakistan",
           ["Ali", "Usman", "Hamza", "Bilal", "Faisal", "Zain", "Hassan", "Omar"],
           ["Ayesha", "Fatima", "Sana", "Hina", "Zara", "Mariam", "Noor", "Iqra"],
           ["Khan", "Malik", "Sheikh", "Butt", "Raza", "Qureshi", "Siddiqui", "Abbasi"]),
    "US": ("🇺🇸 USA",
           ["Liam", "Noah", "Ethan", "Lucas", "Mason", "Oliver", "James", "Henry"],
           ["Emma", "Olivia", "Sophia", "Mia", "Ava", "Amelia", "Ella", "Chloe"],
           ["Smith", "Johnson", "Brown", "Davis", "Miller", "Wilson", "Moore", "Clark"]),
    "UK": ("🇬🇧 UK",
           ["Harry", "Oliver", "George", "Jack", "Charlie", "Oscar", "Leo", "Archie"],
           ["Amelia", "Isla", "Poppy", "Freya", "Ivy", "Grace", "Lily", "Ruby"],
           ["Taylor", "Evans", "Walker", "Wright", "Thompson", "Roberts", "Hall", "Young"]),
}
def gen_names(cc, g, n=5):
    label, men, women, last = NAMES[cc]
    out = []
    for _ in range(n):
        gg = g if g in ("M", "F") else random.choice("MF")
        first = random.choice(men if gg == "M" else women)
        out.append(f"• {first} {random.choice(last)} ({'Male' if gg == 'M' else 'Female'})")
    return label, out
def ng_markup(cc, g):
    return IM([[IB("🔁 Re-generate", callback_data=f"ng:{cc}:{g}")],
               [IB("👨 Male", callback_data=f"ng:{cc}:M"), IB("👩 Female", callback_data=f"ng:{cc}:F")],
               [IB("🌍 Change Country", callback_data="ngc")]])
def ng_text(cc, g):
    label, out = gen_names(cc, g)
    return f"🪪 Random names — {label}\n\n" + "\n".join(out)

# ============================ HELPERS ============================
def panel_text():
    day = time.time() - 86400
    orders = fetch1("SELECT COUNT(*) c FROM orders WHERE ts>?", (day,))["c"]
    newu = fetch1("SELECT COUNT(*) c FROM users WHERE joined>?", (day,))["c"]
    total = fetch1("SELECT COUNT(*) c FROM users")["c"]
    dep = fetch1("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='approved' AND ts>?", (day,))["s"]
    pdep = fetch1("SELECT COUNT(*) c FROM deposits WHERE status='pending'")["c"]
    req = fetch1("SELECT COUNT(*) c FROM requests WHERE status='open'")["c"]
    rep = fetch1("SELECT COUNT(*) c FROM replacements WHERE status='pending'")["c"]
    low = [(p["name"], stock_count(p["id"])) for p in fetch("SELECT * FROM products")]
    low = [x for x in low if x[1] <= LOW_STOCK]
    t = (f"👑 ADMIN PANEL\n\n🛍 Orders (24h): {orders}\n🆕 New users: {newu} (total {total})\n"
         f"💰 Deposits (24h): {dep:.2f}৳\n⚠️ Pending: deposits {pdep} • order requests {req} • replacements {rep}\n\n"
         f"⚠️ Low Stock ({len(low)}):\n")
    for n, c in low[:5]: t += f"• {n} — {c}\n"
    if len(low) > 5: t += f"... and {len(low) - 5} more\n"
    return t

async def send_long(m, text, **kw):
    for i in range(0, max(len(text), 1), 4000):
        await m.reply_text(text[i:i + 4000], **kw)

async def tell_admins(ctx, text, markup=None):
    for a in ADMIN_IDS:
        try: await ctx.bot.send_message(a, text, reply_markup=markup)
        except Exception: pass

async def to_group(ctx, key, text):
    gid = get_set(key).strip()
    if not gid: return
    try: await ctx.bot.send_message(int(gid), text)
    except Exception: pass

async def check_alert(ctx, pid):
    tot = fetch1("SELECT COUNT(*) c FROM stock WHERE product_id=?", (pid,))["c"]
    if not tot: return
    left = stock_count(pid)
    key = f"alert_{pid}"
    if (tot - left) / tot >= ALERT_PCT and get_set(key) != "1":
        put_set(key, "1")
        p = fetch1("SELECT name FROM products WHERE id=?", (pid,))
        await tell_admins(ctx, f"⚠️ Stock Alert!\n\n{p['name'] if p else pid} is {int(ALERT_PCT * 100)}% sold. Only {left} left. Add stock soon.")

def admin_link():
    c = get_set("admin_contact").strip()
    if c.startswith("@"): return "https://t.me/" + c[1:]
    if c.startswith("http"): return c
    return None

# ============================ COMMANDS ============================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ref = None
    if ctx.args and ctx.args[0].startswith("ref_") and ctx.args[0][4:].isdigit():
        ref = int(ctx.args[0][4:])
    register(update.effective_user, ref)
    if is_banned(update.effective_user.id) and not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 You are banned."); return
    ctx.user_data.clear()
    await update.message.reply_text(get_set("welcome"), reply_markup=user_kb(update.effective_user.id))

async def my_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")

# ============================ TEXT HANDLER ============================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid, t, m = u.id, update.message.text.strip(), update.message
    register(u)
    if is_banned(uid) and not is_admin(uid):
        await m.reply_text("🚫 You are banned."); return
    if t in MENU:
        ctx.user_data.pop("state", None); ctx.user_data.pop("arg", None)
    elif ctx.user_data.get("state"):
        await handle_state(update, ctx, t)
        return

    # ---- user menu ----
    if t == B_BUY:
        if not fetch("SELECT 1 FROM categories"):
            await m.reply_text("No products available yet."); return
        await m.reply_text("📁 Select a Section:", reply_markup=sections_markup())
    elif t == B_DEP:
        kb = IM([[IB("bKash", callback_data="dep:bkash"), IB("Nagad", callback_data="dep:nagad")],
                 [IB("Rocket", callback_data="dep:rocket")], CANCEL])
        await m.reply_text("🏦 Choose deposit method:\n\n🎁 Deposit bonus:\n" + bonus_table(), reply_markup=kb)
    elif t == B_NAME:
        await m.reply_text("🌍 Choose a country:", reply_markup=IM(
            [[IB(v[0], callback_data=f"ng:{k}:A")] for k, v in NAMES.items()] + [CANCEL]))
    elif t == B_MAIL:
        ctx.user_data["state"] = "mailcode"
        await m.reply_text("📧 Send the email address of an account you bought.\nI will show the saved details from your orders.")
    elif t == B_SHEET:
        n = fetch1("SELECT COUNT(*) c FROM saved WHERE user_id=?", (uid,))["c"]
        await m.reply_text(f"📝 Your sheet has {n} saved line(s).", reply_markup=IM([
            [IB("➕ Add lines", callback_data="sh:add"), IB("📄 View", callback_data="sh:view")],
            [IB("📤 Export .txt", callback_data="sh:exp"), IB("🗑 Clear", callback_data="sh:clear")], CANCEL]))
    elif t == B_NEXT:
        await m.reply_text("📄 Page 2", reply_markup=PAGE2_KB)
    elif t == B_BACK:
        await m.reply_text("🏠 Main menu", reply_markup=user_kb(uid))
    elif t == B_BAL:
        b = fetch1("SELECT balance FROM users WHERE id=?", (uid,))["balance"]
        await m.reply_text(f"💼 Your balance: {fmt(b)}৳")
    elif t == B_ORD:
        rows = fetch("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
        if not rows: await m.reply_text("You have no orders yet."); return
        await send_long(m, "📜 Last orders:\n\n" + "\n\n".join(
            f"#{r['id']} {r['product_name']} • {fmt(r['price'])}৳\n{r['data']}" for r in rows))
    elif t == B_PEND:
        deps = fetch("SELECT * FROM deposits WHERE user_id=? AND status='pending'", (uid,))
        reqs = fetch("SELECT r.id, p.name FROM requests r LEFT JOIN products p ON p.id=r.product_id WHERE r.user_id=? AND r.status='open'", (uid,))
        reps = fetch("SELECT id, order_id FROM replacements WHERE user_id=? AND status='pending'", (uid,))
        if not (deps or reqs or reps):
            await m.reply_text("✅ You have nothing pending."); return
        txt = "⏳ Pending items\n"
        for d in deps: txt += f"\n💰 Deposit #{d['id']} • {d['method'].upper()} • {fmt(d['amount'])}৳ • {d['trx']}"
        for r in reqs: txt += f"\n📩 Order request #{r['id']} • {r['name']}"
        for r in reps: txt += f"\n🔄 Replace request #{r['id']} • order #{r['order_id']}"
        await m.reply_text(txt)
    elif t == B_SUP:
        kb = []
        link = admin_link()
        if link: kb.append([IB("👤 Admin Contact", url=link)])
        kb.append([IB("💬 Live Chat", callback_data="live")])
        kb.append(CANCEL)
        await m.reply_text(f"☎️ Support\n\n{get_set('support')}\n\nTap a button below:", reply_markup=IM(kb))
    elif t == B_FREE:
        nums = [x.strip() for x in get_set("free_numbers").splitlines() if x.strip()]
        await m.reply_text(f"📞 Free number: {random.choice(nums)}" if nums else "No free numbers available right now.")
    elif t == B_REPL:
        rows = fetch("SELECT id, product_name FROM orders WHERE user_id=? AND price>0 ORDER BY id DESC LIMIT 10", (uid,))
        if not rows: await m.reply_text("You have no orders to replace."); return
        kb = [[IB(f"#{r['id']} {r['product_name']}", callback_data=f"rp:{r['id']}")] for r in rows] + [CANCEL]
        await m.reply_text(f"🔄 Choose the order to replace (warranty {get_set('warranty_hours')}h):", reply_markup=IM(kb))
    elif t == B_REF:
        n = fetch1("SELECT COUNT(*) c FROM users WHERE ref_by=?", (uid,))["c"]
        link = f"https://t.me/{ctx.bot.username}?start=ref_{uid}"
        await m.reply_text(f"🎁 REFERRAL PROGRAM\n\nInvite friends with your link:\n{link}\n\n"
                           f"You earn {get_set('ref_percent')}% of your friend's FIRST deposit.\n👥 Your referrals: {n}\n\n"
                           f"💳 Deposit bonus for everyone:\n{bonus_table()}")

    # ---- admin menu ----
    elif t in (B_ADMIN, A_SHOP, A_MONEY, A_HUB, A_USERS, A_SET, A_BACKUP, A_USER):
        if not is_admin(uid):
            await m.reply_text("⛔ Not allowed."); return
        if t == B_ADMIN:
            await m.reply_text(panel_text(), reply_markup=IM([[IB("🔄 Refresh", callback_data="ad:refresh")]]))
            await m.reply_text("Admin keyboard ready 👇", reply_markup=ADMIN_KB)
        elif t == A_USER:
            await m.reply_text("🏠 User menu", reply_markup=user_kb(uid))
        elif t == A_SHOP:
            await m.reply_text("🏪 Shop management", reply_markup=IM([
                [IB("➕ Category", callback_data="ad:addcat"), IB("➕ Product", callback_data="ad:addprod")],
                [IB("📥 Add Stock", callback_data="ad:addstock"), IB("✏️ Edit Price", callback_data="ad:editprice")],
                [IB("📊 Live Stock", callback_data="ad:livestock")],
                [IB("🗑 Delete Product", callback_data="ad:delprod"), IB("🗑 Delete Category", callback_data="ad:delcat")]]))
        elif t == A_MONEY:
            await m.reply_text("💰 Money", reply_markup=IM([
                [IB("⏳ Deposit Requests", callback_data="ad:pending"), IB("📚 Deposit History", callback_data="ad:dephist")],
                [IB("✏️ Edit Balance", callback_data="ad:addbal"), IB("💵 Total Deposit", callback_data="ad:totaldep")],
                [IB("💳 Payment Methods", callback_data="ad:paymethods")]]))
        elif t == A_HUB:
            await m.reply_text("📦 Order Hub", reply_markup=IM([
                [IB("🧾 Orders", callback_data="ad:recent"), IB("📩 Order Requests", callback_data="ad:orders")],
                [IB("🔄 Replacements", callback_data="ad:repl"), IB("🔎 Invoice Search", callback_data="ad:invoice")],
                [IB("📣 Order Group", callback_data="ad:set:order_group"), IB("🆘 Support Group", callback_data="ad:set:support_group")]]))
        elif t == A_USERS:
            total = fetch1("SELECT COUNT(*) c FROM users")["c"]
            banned = fetch1("SELECT COUNT(*) c FROM users WHERE banned=1")["c"]
            await m.reply_text(f"👥 Total users: {total} • Banned: {banned}", reply_markup=IM([
                [IB("🔎 Find user", callback_data="ad:finduser"), IB("📢 Broadcast", callback_data="ad:broadcast")],
                [IB("🚫 Ban", callback_data="ad:ban"), IB("✅ Unban", callback_data="ad:unban")]]))
        elif t == A_SET:
            await m.reply_text("⚙️ Settings", reply_markup=IM([
                [IB("🤖 Bot Settings", callback_data="ad:botset"), IB("🧩 Get Code", callback_data="ad:code")],
                [IB("🔗 Proxy Link", callback_data="ad:set:proxy"), IB("🗂 History Group", callback_data="ad:set:history_group")],
                [IB("🧰 Menu Editor", callback_data="ad:menued"), IB("📁 File Management", callback_data="ad:files")]]))
        elif t == A_BACKUP:
            await m.reply_text("💾 Backup", reply_markup=IM([
                [IB("⬇️ Backup DB", callback_data="ad:backup"), IB("⬆️ Restore DB", callback_data="ad:restore")]]))

# ============================ STATE HANDLER (typed replies) ============================
async def handle_state(update, ctx, t):
    u = update.effective_user
    uid, m = u.id, update.message
    st, arg = ctx.user_data.get("state"), ctx.user_data.get("arg")
    def done():
        ctx.user_data.pop("state", None); ctx.user_data.pop("arg", None)

    # ----- user states -----
    if st == "dep":
        p = t.split()
        try:
            amount = float(p[0]); trx = p[1].upper()
            if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000: raise ValueError
        except Exception:
            await m.reply_text("❌ Format: AMOUNT TRXID\nExample: 100 8N7A6D5E4C"); return
        if fetch1("SELECT 1 FROM deposits WHERE trx=? AND method=? AND status!='rejected'", (trx, arg)):
            await m.reply_text("❌ This TrxID was already submitted."); return
        did = exe("INSERT INTO deposits(user_id,method,amount,trx,ts) VALUES(?,?,?,?,?)", (uid, arg, amount, trx, time.time()))
        await m.reply_text("✅ Deposit request sent. Please wait for approval.")
        await tell_admins(ctx, f"💰 New deposit #{did}\nUser: {u.full_name} ({uid})\n{arg.upper()} • {fmt(amount)}৳\nTrxID: {trx}",
                          IM([[IB("✅ Approve", callback_data=f"da:{did}"), IB("❌ Reject", callback_data=f"dr:{did}")]]))
        done()
    elif st == "live":
        await tell_admins(ctx, f"💬 Live chat • {u.full_name} ({uid})\n\n{t}",
                          IM([[IB("↩️ Reply", callback_data=f"lc:{uid}")]]))
        await to_group(ctx, "support_group", f"💬 {u.full_name} ({uid}): {t}")
        await m.reply_text("✅ Sent. Admin will reply here. (Tap any menu button to leave live chat.)")
    elif st == "mailcode":
        rows = fetch("SELECT id, product_name, data FROM orders WHERE user_id=? AND data LIKE ? ORDER BY id DESC LIMIT 5",
                     (uid, f"%{t}%"))
        if not rows:
            await m.reply_text("No order found with that email. Try again or tap a menu button.")
        else:
            await send_long(m, "📧 Found:\n\n" + "\n\n".join(f"#{r['id']} {r['product_name']}\n{r['data']}" for r in rows))
            done()
    elif st == "sheet":
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        for l in lines: exe("INSERT INTO saved(user_id,text,ts) VALUES(?,?,?)", (uid, l, time.time()))
        await m.reply_text(f"✅ Saved {len(lines)} line(s). Send more, or tap a menu button to finish.")
    elif st == "replace":
        o = fetch1("SELECT * FROM orders WHERE id=? AND user_id=?", (arg, uid))
        if not o:
            await m.reply_text("Order not found."); done(); return
        rid = exe("INSERT INTO replacements(user_id,order_id,reason,ts) VALUES(?,?,?,?)", (uid, o["id"], t[:500], time.time()))
        await m.reply_text("✅ Replace request sent. Admin will review it.")
        txt = f"🔄 Replace request #{rid}\nUser: {u.full_name} ({uid})\nOrder #{o['id']}: {o['product_name']}\nData: {o['data']}\nReason: {t[:500]}"
        await tell_admins(ctx, txt, IM([[IB("✅ Replace", callback_data=f"ra:{rid}"), IB("❌ Reject", callback_data=f"rj:{rid}")]]))
        await to_group(ctx, "support_group", txt)
        done()

    # ----- admin states -----
    elif not is_admin(uid):
        done()
    elif st == "lreply":
        try:
            await ctx.bot.send_message(int(arg), f"👨‍💼 Admin:\n{t}")
            await m.reply_text("✅ Sent. (Reply mode stays ON — tap any menu button to exit.)")
        except Exception:
            await m.reply_text("❌ Could not send (user blocked the bot?)."); done()
    elif st == "addcat":
        exe("INSERT INTO categories(name) VALUES(?)", (t,))
        await m.reply_text(f"✅ Category added: {t}"); done()
    elif st == "addprod":
        p = [x.strip() for x in t.split("|")]
        try:
            price = float(p[1])
            if not math.isfinite(price) or price < 0: raise ValueError
        except Exception:
            await m.reply_text("❌ Format: Name | Price | Description"); return
        exe("INSERT INTO products(cat_id,name,price,info) VALUES(?,?,?,?)", (arg, p[0], price, p[2] if len(p) > 2 else ""))
        await m.reply_text(f"✅ Product added: {p[0]} • {fmt(price)}৳"); done()
    elif st == "addstock":
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        for l in lines: exe("INSERT INTO stock(product_id,data) VALUES(?,?)", (arg, l))
        put_set(f"alert_{arg}", "0")
        await m.reply_text(f"✅ {len(lines)} item(s) added to stock."); done()
    elif st == "editprice":
        try:
            price = float(t)
            if not math.isfinite(price) or price < 0: raise ValueError
        except Exception:
            await m.reply_text("❌ Send a number, example: 12.5"); return
        exe("UPDATE products SET price=? WHERE id=?", (price, arg))
        await m.reply_text(f"✅ Price updated: {fmt(price)}৳"); done()
    elif st == "addbal":
        try:
            p = t.split(); target, amt = int(p[0]), float(p[1])
            if not math.isfinite(amt): raise ValueError
            assert fetch1("SELECT 1 FROM users WHERE id=?", (target,))
        except Exception:
            await m.reply_text("❌ Format: USER_ID AMOUNT   (negative to remove)"); return
        exe("UPDATE users SET balance=balance+? WHERE id=?", (amt, target))
        await m.reply_text("✅ Balance updated.")
        await to_group(ctx, "history_group", f"✏️ Balance edit: user {target} {amt:+g}৳ by admin {uid}")
        try: await ctx.bot.send_message(target, f"💰 Your balance changed by {amt:+g}৳")
        except Exception: pass
        done()
    elif st == "finduser":
        r = fetch1("SELECT * FROM users WHERE id=?", (int(t),)) if t.isdigit() else None
        if not r: await m.reply_text("User not found."); return
        n = fetch1("SELECT COUNT(*) c FROM orders WHERE user_id=?", (r["id"],))["c"]
        await m.reply_text(f"👤 {r['name']}\nID: {r['id']}\nBalance: {fmt(r['balance'])}৳\nOrders: {n}\nBanned: {'yes' if r['banned'] else 'no'}"); done()
    elif st in ("ban", "unban"):
        if not t.isdigit() or not fetch1("SELECT 1 FROM users WHERE id=?", (int(t),)):
            await m.reply_text("User not found."); return
        if int(t) in ADMIN_IDS:
            await m.reply_text("You cannot ban an admin."); done(); return
        exe("UPDATE users SET banned=? WHERE id=?", (1 if st == "ban" else 0, int(t)))
        await m.reply_text("🚫 User banned." if st == "ban" else "✅ User unbanned."); done()
    elif st == "invoice":
        n = t.lstrip("#")
        o = fetch1("SELECT * FROM orders WHERE id=?", (int(n),)) if n.isdigit() else None
        if not o: await m.reply_text("Order not found."); return
        await m.reply_text(f"🧾 Invoice #{o['id']}\nUser: {o['user_id']}\nProduct: {o['product_name']}\nPrice: {fmt(o['price'])}৳\n"
                           f"Time: {time.strftime('%Y-%m-%d %H:%M', time.localtime(o['ts']))}\nData: {o['data']}"); done()
    elif st == "broadcast":
        ok = 0
        for r in fetch("SELECT id FROM users WHERE banned=0"):
            try: await ctx.bot.send_message(r["id"], t); ok += 1
            except Exception: pass
            await asyncio.sleep(0.05)
        await m.reply_text(f"📢 Sent to {ok} users."); done()
    elif st == "set":
        if arg in NUMERIC:
            try:
                v = float(t)
                if not math.isfinite(v) or v < 0: raise ValueError
                t = fmt(v)
            except Exception:
                await m.reply_text("❌ Send a number (example: 5)."); return
        if arg in GROUPS:
            if t.lower() in ("off", "none", "0"): t = ""
            elif not t.lstrip("-").isdigit():
                await m.reply_text("❌ Send the numeric chat ID (example: -1001234567890) or 'off'."); return
        put_set(arg, t)
        await m.reply_text(f"✅ {SETTING_LABELS.get(arg, arg)} updated."); done()

# ============================ DOCUMENT HANDLER (restore / file upload) ============================
async def on_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid, m = update.effective_user.id, update.message
    st = ctx.user_data.get("state")
    if not is_admin(uid) or st not in ("restore", "upfile"): return
    doc = m.document
    if st == "upfile":
        exe("INSERT INTO files(name,file_id) VALUES(?,?)", (doc.file_name or "file", doc.file_id))
        ctx.user_data.pop("state", None)
        await m.reply_text("✅ File saved."); return
    tmp = os.path.join(tempfile.gettempdir(), f"restore_{int(time.time())}.db")
    f = await doc.get_file()
    await f.download_to_drive(tmp)
    try:
        con = sqlite3.connect(tmp)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"users", "products", "stock", "orders"} <= names
    except Exception:
        try: os.remove(tmp)
        except Exception: pass
        await m.reply_text("❌ This is not a valid shop database."); return
    if os.path.exists(DB_FILE): shutil.copyfile(DB_FILE, DB_FILE + ".bak")
    shutil.copyfile(tmp, DB_FILE)
    os.remove(tmp)
    init_db()
    ctx.user_data.pop("state", None)
    await m.reply_text("✅ Database restored. (Old one saved as shop.db.bak on the server.)")

# ============================ BUTTON (CALLBACK) HANDLER ============================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid, msg, p = q.from_user.id, q.message, q.data.split(":")
    c = p[0]
    if is_banned(uid) and not is_admin(uid): return

    # ---------- user side ----------
    if c == "cancel":
        await msg.delete()
    elif c == "sec":
        await msg.edit_text("📁 Select a Section:", reply_markup=sections_markup(int(p[1])))
    elif c == "cat":
        cid, page = p[1], int(p[2]) if len(p) > 2 else 0
        prods = fetch("SELECT * FROM products WHERE cat_id=?", (cid,))
        rows, page, pages = paged(prods, page)
        kb = [[IB(f"{'🔴' if stock_count(x['id']) == 0 else '🟢'} {x['name']} • {fmt(x['price'])}৳ • {stock_count(x['id'])} left",
                  callback_data=f"prod:{x['id']}")] for x in rows]
        nav = nav_row(f"cat:{cid}", page, pages)
        if nav: kb.append(nav)
        kb.append([IB("⬅️ Back", callback_data="sec:0")])
        await msg.edit_text(f"📦 Choose a product (Page {page + 1}/{pages}):" if prods else "No products in this section yet.",
                            reply_markup=IM(kb))
    elif c == "prod":
        x = fetch1("SELECT * FROM products WHERE id=?", (p[1],))
        if not x: return
        kb = IM([[IB("Buy 1", callback_data=f"buy:{x['id']}:1"), IB("Buy 5", callback_data=f"buy:{x['id']}:5"),
                  IB("Buy 10", callback_data=f"buy:{x['id']}:10")],
                 [IB("⬅️ Back", callback_data=f"cat:{x['cat_id']}:0")]])
        await msg.edit_text(f"📦 {x['name']}\n💵 Price: {fmt(x['price'])}৳\n📊 In stock: {stock_count(x['id'])}\n\n{x['info'] or ''}", reply_markup=kb)
    elif c == "buy":
        pid, n = int(p[1]), int(p[2])
        st, prod, payload, cost = buy_tx(uid, pid, n)
        if st == "noprod":
            await msg.reply_text("Product not found."); return
        if st == "stock":
            await msg.reply_text("⚠️ Not enough stock.", reply_markup=IM([[IB("📩 Request Order", callback_data=f"req:{pid}")]])); return
        if st == "bal":
            await msg.reply_text(f"❌ Not enough balance.\nPrice: {fmt(cost)}৳ • Your balance: {fmt(payload)}৳\nUse 🏦 Deposit first."); return
        data, oids = payload
        await send_long(msg, f"✅ Purchase successful!\n📦 {prod['name']} x{n}\n💸 Paid: {fmt(cost)}৳\n"
                             f"🧾 Order: {', '.join('#' + str(i) for i in oids)}\n\n" + "\n".join(data))
        await to_group(ctx, "order_group", f"🛍 New order\nUser: {q.from_user.full_name} ({uid})\n{prod['name']} x{n} • {fmt(cost)}৳")
        await check_alert(ctx, pid)
    elif c == "req":
        exe("INSERT INTO requests(user_id,product_id,ts) VALUES(?,?,?)", (uid, p[1], time.time()))
        await msg.edit_text("📩 Request sent. Admin will add stock soon.")
        pr = fetch1("SELECT name FROM products WHERE id=?", (p[1],))
        await tell_admins(ctx, f"📩 New order request from {q.from_user.full_name} ({uid}) for {pr['name'] if pr else '#' + p[1]}")
    elif c == "dep":
        ctx.user_data.update(state="dep", arg=p[1])
        await msg.edit_text(f"💳 {p[1].upper()} number: {get_set(p[1])}\n\nSend money (Personal / Send Money), then reply here with:\nAMOUNT TRXID\nExample: 100 8N7A6D5E4C")
    elif c == "ngc":
        await msg.edit_text("🌍 Choose a country:", reply_markup=IM(
            [[IB(v[0], callback_data=f"ng:{k}:A")] for k, v in NAMES.items()] + [CANCEL]))
    elif c == "ng":
        if p[1] in NAMES:
            await msg.edit_text(ng_text(p[1], p[2]), reply_markup=ng_markup(p[1], p[2]))
    elif c == "live":
        ctx.user_data["state"] = "live"
        await msg.edit_text("💬 Live chat is ON. Type your message now.\nTap any menu button to leave.",
                            reply_markup=IM([[IB("🔚 End chat", callback_data="lend")]]))
    elif c == "lend":
        ctx.user_data.pop("state", None)
        await msg.edit_text("✅ Live chat ended.")
    elif c == "sh":
        a = p[1]
        if a == "add":
            ctx.user_data["state"] = "sheet"
            await msg.reply_text("📝 Send the lines to save (one per line). Tap any menu button to finish.")
        elif a == "view":
            rows = fetch("SELECT text FROM saved WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,))
            await send_long(msg, "📄 Your sheet (latest 100):\n\n" + ("\n".join(r["text"] for r in rows) or "Empty."))
        elif a == "exp":
            rows = fetch("SELECT text FROM saved WHERE user_id=? ORDER BY id", (uid,))
            if not rows: await msg.reply_text("Your sheet is empty."); return
            await msg.reply_document(io.BytesIO("\n".join(r["text"] for r in rows).encode("utf-8")), filename="sheet.txt")
        elif a == "clear":
            exe("DELETE FROM saved WHERE user_id=?", (uid,))
            await msg.reply_text("🗑 Sheet cleared.")
    elif c == "rp":
        o = fetch1("SELECT * FROM orders WHERE id=? AND user_id=?", (p[1], uid))
        if not o:
            await msg.edit_text("Order not found."); return
        if time.time() - o["ts"] > _num("warranty_hours", 72) * 3600:
            await msg.edit_text("⌛ The warranty period for this order has ended."); return
        if fetch1("SELECT 1 FROM replacements WHERE order_id=? AND status IN ('pending','approved')", (o["id"],)):
            await msg.edit_text("This order already has a replace request."); return
        ctx.user_data.update(state="replace", arg=str(o["id"]))
        await msg.edit_text(f"🔄 Order #{o['id']} — {o['product_name']}\n\nSend the problem / reason in one message:")

    # ---------- admin side ----------
    elif not is_admin(uid):
        await msg.reply_text("⛔ Not allowed.")
    elif c == "ad":
        a = p[1]
        if a == "refresh":
            try: await msg.edit_text(panel_text(), reply_markup=IM([[IB("🔄 Refresh", callback_data="ad:refresh")]]))
            except Exception: pass
        elif a == "addcat":
            ctx.user_data["state"] = "addcat"; await msg.reply_text("Send the new category name (example: Mail):")
        elif a == "addprod":
            await msg.reply_text("Choose category:", reply_markup=pick_list("SELECT * FROM categories", "apc"))
        elif a == "addstock":
            await msg.reply_text("Choose product:", reply_markup=pick_list("SELECT * FROM products", "asp"))
        elif a == "editprice":
            await msg.reply_text("Edit price of:", reply_markup=pick_list("SELECT * FROM products", "epp"))
        elif a == "livestock":
            rows = fetch("SELECT p.id, p.name, c.name cn FROM products p LEFT JOIN categories c ON c.id=p.cat_id ORDER BY c.id, p.id")
            txt, last = "📊 LIVE STOCK\n", None
            for r in rows:
                if r["cn"] != last: txt += f"\n📁 {r['cn']}\n"; last = r["cn"]
                s = stock_count(r["id"])
                txt += f"{'🔴' if s == 0 else '🟢'} {r['name']} — {s}\n"
            await send_long(msg, txt if rows else "No products yet.")
        elif a == "delprod":
            await msg.reply_text("Delete which product?", reply_markup=pick_list("SELECT * FROM products", "dpp"))
        elif a == "delcat":
            await msg.reply_text("Delete which category?", reply_markup=pick_list("SELECT * FROM categories", "dcc"))
        elif a == "pending":
            rows = fetch("SELECT * FROM deposits WHERE status='pending'")
            if not rows: await msg.reply_text("No pending deposits.")
            for d in rows:
                await msg.reply_text(f"💰 Deposit #{d['id']}\nUser: {d['user_id']}\n{d['method'].upper()} • {fmt(d['amount'])}৳\nTrxID: {d['trx']}",
                    reply_markup=IM([[IB("✅ Approve", callback_data=f"da:{d['id']}"), IB("❌ Reject", callback_data=f"dr:{d['id']}")]]))
        elif a == "dephist":
            rows = fetch("SELECT * FROM deposits WHERE status!='pending' ORDER BY id DESC LIMIT 15")
            icon = {"approved": "✅", "rejected": "❌"}
            await send_long(msg, "📚 Deposit history (last 15):\n\n" + ("\n".join(
                f"{icon.get(r['status'], '•')} #{r['id']} user {r['user_id']} • {r['method']} • {fmt(r['amount'])}৳ • {r['trx']}" for r in rows) or "None"))
        elif a == "totaldep":
            tot = fetch1("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='approved'")["s"]
            m30 = fetch1("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='approved' AND ts>?", (time.time() - 30 * 86400,))["s"]
            bal = fetch1("SELECT COALESCE(SUM(balance),0) s FROM users")["s"]
            await msg.reply_text(f"💵 Total approved deposits: {tot:.2f}৳\n📅 Last 30 days: {m30:.2f}৳\n💼 Users' total balance: {bal:.2f}৳")
        elif a == "paymethods":
            await msg.reply_text(f"💳 Payment methods\n\nbKash: {get_set('bkash')}\nNagad: {get_set('nagad')}\nRocket: {get_set('rocket')}\n"
                                 f"Bonus: {get_set('bonus_percent')}% (min {get_set('bonus_min')}৳)",
                                 reply_markup=settings_markup(["bkash", "nagad", "rocket", "bonus_percent", "bonus_min"]))
        elif a == "addbal":
            ctx.user_data["state"] = "addbal"; await msg.reply_text("Send: USER_ID AMOUNT\nExample: 123456789 50  (negative to remove)")
        elif a == "orders":
            rows = fetch("SELECT r.id, r.user_id, p.name FROM requests r LEFT JOIN products p ON p.id=r.product_id WHERE r.status='open'")
            if not rows: await msg.reply_text("No open order requests.")
            for r in rows:
                await msg.reply_text(f"📩 Request #{r['id']}\nUser: {r['user_id']}\nProduct: {r['name']}",
                                     reply_markup=IM([[IB("✅ Done", callback_data=f"rd:{r['id']}")]]))
        elif a == "recent":
            rows = fetch("SELECT * FROM orders ORDER BY id DESC LIMIT 10")
            await send_long(msg, "🧾 Recent orders:\n\n" + ("\n".join(f"#{r['id']} user {r['user_id']} • {r['product_name']} • {fmt(r['price'])}৳" for r in rows) or "None"))
        elif a == "repl":
            rows = fetch("SELECT * FROM replacements WHERE status='pending'")
            if not rows: await msg.reply_text("No pending replace requests.")
            for r in rows:
                await msg.reply_text(f"🔄 Replace #{r['id']}\nUser: {r['user_id']}\nOrder #{r['order_id']}\nReason: {r['reason']}",
                    reply_markup=IM([[IB("✅ Replace", callback_data=f"ra:{r['id']}"), IB("❌ Reject", callback_data=f"rj:{r['id']}")]]))
        elif a == "invoice":
            ctx.user_data["state"] = "invoice"; await msg.reply_text("Send the order number (example: 15):")
        elif a == "finduser":
            ctx.user_data["state"] = "finduser"; await msg.reply_text("Send the user ID:")
        elif a == "broadcast":
            ctx.user_data["state"] = "broadcast"; await msg.reply_text("Send the message to broadcast to all users:")
        elif a in ("ban", "unban"):
            ctx.user_data["state"] = a; await msg.reply_text(f"Send the user ID to {a}:")
        elif a == "set":
            ctx.user_data.update(state="set", arg=p[2])
            cur = get_set(p[2]) or "(empty)"
            hint = " (send 'off' to disable)" if p[2] in GROUPS else ""
            await msg.reply_text(f"{SETTING_LABELS.get(p[2], p[2])}\nCurrent: {cur}\n\nSend the new value{hint}:")
        elif a == "botset":
            await msg.reply_text("🤖 Bot Settings", reply_markup=settings_markup(
                ["bonus_percent", "bonus_min", "ref_percent", "warranty_hours", "admin_contact", "support"]))
        elif a == "menued":
            await msg.reply_text("🧰 Menu Editor", reply_markup=settings_markup(["welcome", "support", "free_numbers"]))
        elif a == "code":
            with open(os.path.abspath(__file__), "rb") as f:
                await msg.reply_document(f, filename="shop_bot.py")
        elif a == "files":
            rows = fetch("SELECT * FROM files ORDER BY id DESC LIMIT 20")
            kb = [[IB(f"📄 {r['name']}", callback_data=f"fs:{r['id']}"), IB("🗑", callback_data=f"fd:{r['id']}")] for r in rows]
            kb.append([IB("⬆️ Upload file", callback_data="ad:upfile")])
            await msg.reply_text("📁 File Management", reply_markup=IM(kb))
        elif a == "upfile":
            ctx.user_data["state"] = "upfile"; await msg.reply_text("Send the file (as a document):")
        elif a == "backup":
            with open(DB_FILE, "rb") as f:
                await msg.reply_document(f, filename="shop_backup.db")
        elif a == "restore":
            ctx.user_data["state"] = "restore"
            await msg.reply_text("⬆️ Send the .db backup file as a document.\n⚠️ It will REPLACE the current database (a .bak copy is kept on the server).")
    elif c == "apc":
        ctx.user_data.update(state="addprod", arg=p[1])
        await msg.edit_text("Send product like this:\nName | Price | Description\nExample: Hotmail Fresh | 12 | Instant delivery")
    elif c == "asp":
        ctx.user_data.update(state="addstock", arg=p[1])
        await msg.edit_text("Send stock items, ONE item per line (each line = one sellable item):")
    elif c == "epp":
        ctx.user_data.update(state="editprice", arg=p[1])
        await msg.edit_text("Send the new price (number):")
    elif c == "dpp":
        await msg.edit_text("⚠️ Delete this product and ALL its stock?", reply_markup=IM([[IB("✅ Yes, delete", callback_data=f"dpy:{p[1]}"), IB("❌ No", callback_data="cancel")]]))
    elif c == "dpy":
        exe("DELETE FROM stock WHERE product_id=?", (p[1],)); exe("DELETE FROM products WHERE id=?", (p[1],))
        await msg.edit_text("🗑 Product deleted.")
    elif c == "dcc":
        await msg.edit_text("⚠️ Delete this category with ALL its products and stock?", reply_markup=IM([[IB("✅ Yes, delete", callback_data=f"dcy:{p[1]}"), IB("❌ No", callback_data="cancel")]]))
    elif c == "dcy":
        for x in fetch("SELECT id FROM products WHERE cat_id=?", (p[1],)):
            exe("DELETE FROM stock WHERE product_id=?", (x["id"],))
        exe("DELETE FROM products WHERE cat_id=?", (p[1],)); exe("DELETE FROM categories WHERE id=?", (p[1],))
        await msg.edit_text("🗑 Category deleted.")
    elif c == "rd":
        exe("UPDATE requests SET status='done' WHERE id=?", (p[1],))
        await msg.edit_text("✅ Request marked done.")
    elif c == "lc":
        ctx.user_data.update(state="lreply", arg=p[1])
        await msg.reply_text(f"↩️ Reply mode ON for user {p[1]}. Type your reply.\nTap any menu button to exit.")
    elif c == "fs":
        r = fetch1("SELECT * FROM files WHERE id=?", (p[1],))
        if r: await msg.reply_document(r["file_id"])
    elif c == "fd":
        exe("DELETE FROM files WHERE id=?", (p[1],))
        await msg.edit_text("🗑 File removed.")
    elif c in ("ra", "rj"):
        r = fetch1("SELECT * FROM replacements WHERE id=?", (p[1],))
        if not r or r["status"] != "pending":
            await msg.edit_text("Already handled."); return
        if c == "rj":
            exe("UPDATE replacements SET status='rejected' WHERE id=?", (r["id"],))
            await msg.edit_text(f"❌ Replace #{r['id']} rejected.")
            note = f"❌ Your replace request #{r['id']} was rejected. Contact support."
        else:
            o = fetch1("SELECT * FROM orders WHERE id=?", (r["order_id"],))
            item = fetch1("SELECT * FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (o["product_id"],)) if o else None
            if not item:
                await msg.reply_text("⚠️ No stock left for this product. Add stock, then press Replace again."); return
            exe("UPDATE stock SET sold=1 WHERE id=?", (item["id"],))
            exe("INSERT INTO orders(user_id,product_id,product_name,price,data,ts) VALUES(?,?,?,?,?,?)",
                (o["user_id"], o["product_id"], o["product_name"] + " (replacement)", 0, item["data"], time.time()))
            exe("UPDATE replacements SET status='approved' WHERE id=?", (r["id"],))
            await msg.edit_text(f"✅ Replace #{r['id']} done.")
            note = f"✅ Replacement for order #{o['id']} ({o['product_name']}):\n\n{item['data']}"
            await check_alert(ctx, o["product_id"])
        try: await ctx.bot.send_message(r["user_id"], note)
        except Exception: pass
    elif c in ("da", "dr"):
        d = fetch1("SELECT * FROM deposits WHERE id=?", (p[1],))
        if not d or d["status"] != "pending":
            await msg.edit_text("Already handled."); return
        if c == "da":
            bonus, rb, ref = approve_deposit(d)
            extra = f" + {fmt(bonus)}৳ bonus" if bonus else ""
            await msg.edit_text(f"✅ Deposit #{d['id']} approved ({fmt(d['amount'])}৳{extra}).")
            note = f"✅ Your deposit of {fmt(d['amount'])}৳ was approved." + (f"\n🎁 Bonus: +{fmt(bonus)}৳" if bonus else "")
            await to_group(ctx, "history_group", f"✅ Deposit #{d['id']} approved • user {d['user_id']} • {fmt(d['amount'])}৳{extra}")
            if ref and rb > 0:
                try: await ctx.bot.send_message(ref, f"🎁 Your friend made their first deposit. Referral bonus: +{fmt(rb)}৳")
                except Exception: pass
        else:
            exe("UPDATE deposits SET status='rejected' WHERE id=?", (d["id"],))
            await msg.edit_text(f"❌ Deposit #{d['id']} rejected.")
            note = f"❌ Your deposit of {fmt(d['amount'])}৳ was rejected. Contact support."
            await to_group(ctx, "history_group", f"❌ Deposit #{d['id']} rejected • user {d['user_id']} • {fmt(d['amount'])}৳")
        try: await ctx.bot.send_message(d["user_id"], note)
        except Exception: pass

# ============================ RUN ============================
async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.exception("Error while handling an update", exc_info=ctx.error)

def main():
    import re, sys
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", BOT_TOKEN):
        sys.exit("❌ BOT_TOKEN সঠিক নয়। @BotFather থেকে নতুন টোকেন নিয়ে BOT_TOKEN সেট করুন।")
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
