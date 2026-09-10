import os
import asyncio
import logging
import sqlite3
from contextlib import closing

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatMemberStatus
from aiogram.utils.text_decorations import html_decoration as html
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID_RAW = os.getenv("ADMIN_USER_ID") or os.getenv("OWNER_USER_ID")
DB_PATH = os.getenv("DB_PATH", "bot.db")
LEGACY_CHANNEL_ID = os.getenv("REQUIRED_CHANNEL_ID", "").strip()
LEGACY_CHANNEL_URL = os.getenv("CHANNEL_JOIN_URL", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not OWNER_ID_RAW:
    raise RuntimeError("ADMIN_USER_ID is missing")
try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_USER_ID must be numeric")

logging.basicConfig(level=logging.INFO)
router = Router()
editing = {}
broadcast_mode = set()

DEFAULTS = {
    "welcome": "⚡ <b>You're Almost There!</b>\n\nYou're missing one step to access the full details.\n\n👉 Join our Telegram channel to continue.\n\nAfter joining, tap <b>“✅ I've Joined”</b> to verify.",
    "not_joined": "⚠️ <b>One Step Remaining</b>\n\nPlease join all required channels first, then tap <b>“✅ I've Joined”</b>.",
    "verified": "🎉 <b>Successfully Joined!</b> ✅\n\nYou're all set. Choose an option below to continue.",
    "how": "📖 <b>HOW IT WORKS</b>\n\n🎯 <b>STEP 1:</b> Check the daily tasks.\n\n✅ <b>STEP 2:</b> Select an eligible task.\n\n🛒 <b>STEP 3:</b> Complete the activity according to the instructions.\n\n💰 <b>STEP 4:</b> Receive the applicable reward according to the stated terms.\n\n🔄 <b>STEP 5:</b> Check daily for new tasks.",
    "tasks": "🎯 <b>AVAILABLE TASKS</b>\n\nDaily tasks and offers can be listed here.\n\n⚠️ Rewards and eligibility vary by task. Follow the published terms.",
    "support": "👨‍💻 <b>SUPPORT</b>\n\nContact the admin for help.",
    "guide": "📚 <b>GUIDE</b>\n\nComplete eligible tasks by following the instructions posted in the channel.\n\n⚠️ Reward amounts can vary. No earning is guaranteed.",
    "reward_amount": "180",
    "reward_claim_text": "🎉 <b>₹180 Salary Unlocked!</b>\n\nYou joined all required Telegram channels. You can now submit your claim for the ₹180 salary.\n\nTap <b>💰 Claim ₹180</b> and enter your UPI ID. Your claim will be reviewed by the admin.",
    "reward_pending_text": "⏳ <b>Claim Submitted</b>\n\nYour ₹{amount} salary claim is pending admin review.\n\nYou will receive a message when the admin accepts or rejects your claim.",
    "reward_paid_text": "✅ <b>User Accepted</b>\n\nYour ₹{amount} salary claim has been accepted by the admin. Thank you!",
    "reward_rejected_text": "❌ <b>Claim Rejected</b>\n\nYour ₹{amount} salary claim was rejected by the admin. Please contact support.",
    "btn_tasks": "🎯 Available Tasks", "btn_how": "💰 How It Works", "btn_support": "👨‍💻 Support",
    "btn_claim": "💰 Claim ₹180",
    "btn_guide": "📖 Guide", "btn_join": "📢 Join Channel", "btn_check": "✅ I've Joined", "guide_image": "",
}

CONTENT_KEYS = [("welcome", "Welcome"), ("not_joined", "Not Joined"), ("verified", "Verified"), ("how", "How It Works"),
                ("tasks", "Tasks"), ("guide", "Guide text"), ("support", "Support"), ("reward_claim_text", "Reward Claim"),
                ("reward_pending_text", "Claim Pending"), ("reward_paid_text", "Accepted Message"), ("reward_rejected_text", "Rejected Message"),
                ("btn_claim", "Claim Button Text") ]
LEGACY_BUTTONS = [("btn_tasks", "🎯 Available Tasks", "tasks"), ("btn_how", "💰 How It Works", "how"),
                  ("btn_guide", "📖 Guide", "guide"), ("btn_support", "👨‍💻 Support", "support")]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, joined_channel INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER UNIQUE NOT NULL, title TEXT NOT NULL, join_url TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS buttons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', image_file_id TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1)")
    conn.execute("CREATE TABLE IF NOT EXISTS reward_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE NOT NULL, amount INTEGER NOT NULL DEFAULT 180, upi_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reviewed_at TIMESTAMP)")
    for k, v in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (OWNER_ID,))
    # Migrate the old single channel into the new channel manager once.
    if LEGACY_CHANNEL_ID:
        try:
            cid = int(LEGACY_CHANNEL_ID)
            exists = conn.execute("SELECT 1 FROM channels WHERE chat_id=?", (cid,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO channels(chat_id,title,join_url) VALUES(?,?,?)", (cid, "Required Channel", LEGACY_CHANNEL_URL))
        except ValueError:
            logging.warning("Ignoring invalid REQUIRED_CHANNEL_ID")
    # Seed the four old buttons only when no dynamic buttons exist.
    if conn.execute("SELECT COUNT(*) FROM buttons").fetchone()[0] == 0:
        for i, (key, fallback, content_key) in enumerate(LEGACY_BUTTONS):
            conn.execute("INSERT INTO buttons(name,content,sort_order) VALUES(?,?,?)", (fallback, content_key, i))
    conn.commit()
    return conn

def get_setting(key):
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else DEFAULTS.get(key, "")

def set_setting(key, value):
    with closing(db()) as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()

def save_user(user_id, first_name="", username=""):
    with closing(db()) as conn:
        conn.execute("INSERT INTO users(user_id,first_name,username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,username=excluded.username,last_seen=CURRENT_TIMESTAMP", (user_id, first_name or "", username or ""))
        conn.commit()

def mark_joined(user_id, joined=True):
    with closing(db()) as conn:
        conn.execute("UPDATE users SET joined_channel=?,last_seen=CURRENT_TIMESTAMP WHERE user_id=?", (1 if joined else 0, user_id)); conn.commit()

def mark_blocked(user_id):
    with closing(db()) as conn:
        conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,)); conn.commit()

def user_stats():
    with closing(db()) as conn:
        return tuple(conn.execute("SELECT COUNT(*), SUM(CASE WHEN joined_channel=1 THEN 1 ELSE 0 END), SUM(CASE WHEN blocked=1 THEN 1 ELSE 0 END) FROM users").fetchone())

def is_admin(user_id):
    with closing(db()) as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None

def add_admin(uid):
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (uid,)); conn.commit()

def remove_admin(uid):
    if uid == OWNER_ID: return False
    with closing(db()) as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (uid,)); conn.commit(); return True

def list_admins():
    with closing(db()) as conn: return [r[0] for r in conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()]

def channels():
    with closing(db()) as conn: return conn.execute("SELECT id,chat_id,title,join_url FROM channels ORDER BY id").fetchall()

def add_channel(chat_id, title, join_url):
    with closing(db()) as conn:
        conn.execute("INSERT OR REPLACE INTO channels(id,chat_id,title,join_url) VALUES((SELECT id FROM channels WHERE chat_id=?),?,?,?)", (chat_id, chat_id, title, join_url)); conn.commit()

def delete_channel(row_id):
    with closing(db()) as conn: conn.execute("DELETE FROM channels WHERE id=?", (row_id,)); conn.commit()

def get_buttons():
    with closing(db()) as conn: return conn.execute("SELECT id,name,content,image_file_id,sort_order,active FROM buttons WHERE active=1 ORDER BY sort_order,id").fetchall()

def get_button(bid):
    with closing(db()) as conn: return conn.execute("SELECT id,name,content,image_file_id,sort_order,active FROM buttons WHERE id=?", (bid,)).fetchone()

def create_button(name, content):
    with closing(db()) as conn:
        order = (conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM buttons").fetchone()[0])
        cur = conn.execute("INSERT INTO buttons(name,content,sort_order) VALUES(?,?,?)", (name, content, order)); bid=cur.lastrowid; conn.commit(); return bid

def update_button(bid, field, value):
    if field not in {"name","content","image_file_id"}: return
    with closing(db()) as conn: conn.execute(f"UPDATE buttons SET {field}=? WHERE id=?", (value,bid)); conn.commit()

def delete_button(bid):
    with closing(db()) as conn: conn.execute("DELETE FROM buttons WHERE id=?", (bid,)); conn.commit()

def render_text(text, user):
    return (text or "").replace("{name}", html.quote(user.first_name or "there")).replace("{username}", html.quote(f"@{user.username}" if user.username else ""))

def strip_html(text):
    import re
    return re.sub(r"<[^>]+>", "", text or "")

def main_keyboard(show_claim=False):
    rows=[[InlineKeyboardButton(text=r[1], callback_data=f"btn:{r[0]}")] for r in get_buttons()]
    if show_claim:
        rows.append([InlineKeyboardButton(text=get_setting("btn_claim"), callback_data="claim_reward")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def join_keyboard():
    rows=[]
    for _,_,title,url in channels():
        if url: rows.append([InlineKeyboardButton(text=f"📢 {title}", url=url)])
    rows.append([InlineKeyboardButton(text=get_setting("btn_check"), callback_data="check_join")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎛️ Button Manager", callback_data="button_manager")],
        [InlineKeyboardButton(text="📢 Channel Manager", callback_data="channel_manager")],
        [InlineKeyboardButton(text="👑 Admin Manager", callback_data="admin_manager")],
        [InlineKeyboardButton(text="✏️ Edit Welcome", callback_data="edit:welcome"), InlineKeyboardButton(text="✏️ Edit Verified", callback_data="edit:verified")],
        [InlineKeyboardButton(text="✏️ Edit How It Works", callback_data="edit:how"), InlineKeyboardButton(text="✏️ Edit Tasks", callback_data="edit:tasks")],
        [InlineKeyboardButton(text="✏️ Edit Guide", callback_data="edit:guide"), InlineKeyboardButton(text="✏️ Edit Support", callback_data="edit:support")],
        [InlineKeyboardButton(text="✏️ Edit Reward Claim", callback_data="edit:reward_claim_text"), InlineKeyboardButton(text="✏️ Edit Pending", callback_data="edit:reward_pending_text")],
        [InlineKeyboardButton(text="✏️ Edit Accepted", callback_data="edit:reward_paid_text"), InlineKeyboardButton(text="✏️ Edit Rejected", callback_data="edit:reward_rejected_text")],
        [InlineKeyboardButton(text="✏️ Edit Claim Button", callback_data="edit:btn_claim")],
        [InlineKeyboardButton(text="🖼️ Guide Image", callback_data="guide_image"), InlineKeyboardButton(text="🗑️ Clear Content", callback_data="delete_menu")],
        [InlineKeyboardButton(text="🔘 Old Button Names", callback_data="buttons")],
        [InlineKeyboardButton(text="📊 Statistics", callback_data="stats"), InlineKeyboardButton(text="📢 Broadcast", callback_data="broadcast")],
        [InlineKeyboardButton(text="💰 Salary Claims", callback_data="reward_admin")],
        [InlineKeyboardButton(text="👀 Preview", callback_data="preview")],
    ])

async def all_joined(bot, user_id):
    rows=channels()
    if not rows: return True
    ok=True
    for _,chat_id,_,_ in rows:
        try:
            member=await bot.get_chat_member(chat_id,user_id)
            joined=member.status in {ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR}
            if not joined: ok=False
        except TelegramBadRequest:
            ok=False
    mark_joined(user_id, ok)
    return ok

async def show_welcome(message):
    await message.answer(render_text(get_setting("welcome"), message.from_user), reply_markup=join_keyboard(), parse_mode="HTML")

def reward_claim(user_id):
    with closing(db()) as conn:
        return conn.execute("SELECT id,amount,upi_id,status,created_at FROM reward_claims WHERE user_id=?", (user_id,)).fetchone()

def create_reward_claim(user_id, upi_id, amount):
    with closing(db()) as conn:
        conn.execute("INSERT INTO reward_claims(user_id,amount,upi_id,status) VALUES(?,?,?,'pending') ON CONFLICT(user_id) DO UPDATE SET upi_id=excluded.upi_id,amount=excluded.amount,status='pending',created_at=CURRENT_TIMESTAMP,reviewed_at=NULL", (user_id, amount, upi_id))
        conn.commit()

def reward_claims(status=None):
    with closing(db()) as conn:
        if status:
            return conn.execute("SELECT id,user_id,amount,upi_id,status,created_at FROM reward_claims WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        return conn.execute("SELECT id,user_id,amount,upi_id,status,created_at FROM reward_claims ORDER BY id DESC").fetchall()

def update_claim(claim_id, status):
    with closing(db()) as conn:
        conn.execute("UPDATE reward_claims SET status=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (status, claim_id)); conn.commit()

@router.chat_join_request()
async def join_request_handler(request: ChatJoinRequest, bot: Bot):
    if not any(r[1] == request.chat.id for r in channels()): return
    user=request.from_user; save_user(user.id,user.first_name,user.username)
    try:
        await bot.approve_chat_join_request(chat_id=request.chat.id,user_id=user.id)
        if await all_joined(bot,user.id):
            await bot.send_message(user.id, render_text(get_setting("verified"),user), parse_mode="HTML")
            await bot.send_message(user.id, render_text(get_setting("reward_claim_text"),user), reply_markup=main_keyboard(show_claim=True), parse_mode="HTML")
    except (TelegramBadRequest,TelegramForbiddenError) as e: logging.warning("Join request failed for %s: %s",user.id,e)

@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    save_user(message.from_user.id,message.from_user.first_name,message.from_user.username)
    if await all_joined(bot,message.from_user.id):
        await message.answer(render_text(get_setting("verified"),message.from_user), parse_mode="HTML")
        await message.answer(render_text(get_setting("reward_claim_text"),message.from_user), reply_markup=main_keyboard(show_claim=True), parse_mode="HTML")
    else: await show_welcome(message)

@router.callback_query(F.data=="check_join")
async def check_join(callback: CallbackQuery, bot: Bot):
    if not await all_joined(bot,callback.from_user.id):
        await callback.answer("⚠️ " + strip_html(get_setting("not_joined"))[:180],show_alert=True); return
    await callback.answer("✅ Verified!")
    if callback.message:
        await callback.message.edit_text(render_text(get_setting("verified"),callback.from_user),parse_mode="HTML")
        await callback.message.answer(render_text(get_setting("reward_claim_text"),callback.from_user), reply_markup=main_keyboard(show_claim=True), parse_mode="HTML")

@router.callback_query(F.data.startswith("btn:"))
async def dynamic_button(callback: CallbackQuery, bot: Bot):
    if not await all_joined(bot,callback.from_user.id): await callback.answer("⚠️ " + strip_html(get_setting("not_joined"))[:180],show_alert=True); return
    bid=int(callback.data.split(":",1)[1]); row=get_button(bid)
    if not row or not row[5]: await callback.answer("This button is no longer available.",show_alert=True); return
    await callback.answer(); content=render_text(row[2],callback.from_user)
    if row[3]: await callback.message.answer_photo(row[3],caption=content,parse_mode="HTML")
    else: await callback.message.answer(content,parse_mode="HTML")

@router.callback_query(F.data=="claim_reward")
async def claim_reward(callback: CallbackQuery, bot: Bot):
    if not await all_joined(bot, callback.from_user.id):
        await callback.answer("⚠️ " + strip_html(get_setting("not_joined"))[:180], show_alert=True); return
    amount=int(get_setting("reward_amount") or "180")
    existing=reward_claim(callback.from_user.id)
    if existing and existing[3] == "accepted":
        await callback.answer("✅ Your claim has already been accepted.", show_alert=True); return
    if existing and existing[3] == "pending":
        await callback.answer("⏳ Your claim is already pending review.", show_alert=True); return
    editing[callback.from_user.id]="reward_upi"
    await callback.answer()
    await callback.message.answer(f"💰 <b>CLAIM ₹{amount}</b>\n\nSend your UPI ID in the next message.\nExample: <code>name@upi</code>\n\nSend /cancel to stop.", parse_mode="HTML")

@router.callback_query(F.data.startswith("claim:"))
async def claim_admin_action(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Not authorized.", show_alert=True); return
    _, action, raw_id = callback.data.split(":")
    claim_id=int(raw_id)
    rows=[r for r in reward_claims() if r[0]==claim_id]
    if not rows:
        await callback.answer("Claim not found.", show_alert=True); return
    claim=rows[0]; status="accepted" if action=="accept" else "rejected"
    update_claim(claim_id,status)
    try:
        amount=claim[2]
        if status=="accepted":
            text=get_setting("reward_paid_text").replace("{amount}",str(amount))
        else:
            text=get_setting("reward_rejected_text").replace("{amount}",str(amount))
        await bot.send_message(claim[1], text, parse_mode="HTML")
    except Exception as e:
        logging.warning("Could not notify claim user %s: %s", claim[1], e)
    await callback.answer("User status updated.")
    await callback.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data=="reward_admin")
async def reward_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Not authorized.", show_alert=True); return
    rows=reward_claims("pending")
    if not rows:
        await callback.answer(); await callback.message.answer("💰 <b>SALARY CLAIMS</b>\n\nNo pending claims.",parse_mode="HTML"); return
    buttons=[]
    text="💰 <b>PENDING SALARY CLAIMS</b>\n\n"
    for r in rows[:20]:
        text += f"<b>#{r[0]}</b> • User <code>{r[1]}</code> • ₹{r[2]} • <code>{html.quote(r[3])}</code>\n"
        buttons.append([InlineKeyboardButton(text=f"#{r[0]} ✅ Accept User", callback_data=f"claim:accept:{r[0]}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"claim:reject:{r[0]}")])
    await callback.answer(); await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("🛠️ <b>CONTROL CENTER</b>\n\nManage buttons, channels, admins and bot content below.",reply_markup=admin_keyboard(),parse_mode="HTML")

@router.callback_query(F.data.startswith("edit:"))
async def edit_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    key=callback.data.split(":",1)[1]; editing[callback.from_user.id]=key; await callback.answer()
    await callback.message.answer(f"✏️ <b>Editing: {key}</b>\n\nSend the new text. HTML formatting is supported.\n\n<b>Current:</b>\n{get_setting(key)}",parse_mode="HTML")

@router.callback_query(F.data=="guide_image")
async def guide_image_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    editing[callback.from_user.id]="guide_image"; await callback.answer(); await callback.message.answer("🖼️ <b>GUIDE IMAGE</b>\n\nSend a photo. Send /deleteguideimage to remove it.",parse_mode="HTML")

@router.message(Command("deleteguideimage"))
async def delete_guide_image(message: Message):
    if not is_admin(message.from_user.id): return
    set_setting("guide_image",""); await message.answer("🗑️ Guide image removed.")

@router.callback_query(F.data=="delete_menu")
async def delete_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗑️ {label}",callback_data=f"delete:{key}")] for key,label in CONTENT_KEYS]+[[InlineKeyboardButton(text="🗑️ Remove Guide Image",callback_data="delete:guide_image")]])
    await callback.answer(); await callback.message.answer("🗑️ <b>CLEAR CONTENT</b>\n\nChoose what to clear.",reply_markup=kb,parse_mode="HTML")

@router.callback_query(F.data.startswith("delete:"))
async def delete_setting(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    key=callback.data.split(":",1)[1]; set_setting(key,""); await callback.answer("🗑️ Cleared"); await callback.message.answer(f"🗑️ <b>{key}</b> cleared.",parse_mode="HTML")

@router.callback_query(F.data=="button_manager")
async def button_manager(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    rows=[[InlineKeyboardButton(text=f"✏️ {r[1]}",callback_data=f"bedit:{r[0]}"),InlineKeyboardButton(text="🗑️",callback_data=f"bdel:{r[0]}")] for r in get_buttons()]
    rows += [[InlineKeyboardButton(text="➕ Add New Button",callback_data="badd")],[InlineKeyboardButton(text="⬅️ Admin",callback_data="admin_home")]]
    await callback.answer(); await callback.message.answer("🎛️ <b>BUTTON MANAGER</b>\n\nEdit or permanently delete buttons. Add as many as you need.",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode="HTML")

@router.callback_query(F.data=="badd")
async def button_add_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    editing[callback.from_user.id]="new_button"; await callback.answer(); await callback.message.answer("➕ <b>NEW BUTTON</b>\n\nSend in one message:\n<code>Button Name</code>\n<code>Button content here...</code>\n\nPut the name on the first line and content below it.",parse_mode="HTML")

@router.callback_query(F.data.startswith("bedit:"))
async def button_edit_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    bid=int(callback.data.split(":")[1]); r=get_button(bid)
    if not r: await callback.answer("Button not found",show_alert=True); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Edit Name",callback_data=f"bn:{bid}"),InlineKeyboardButton(text="📝 Edit Content",callback_data=f"bc:{bid}")],[InlineKeyboardButton(text="🖼️ Set Image",callback_data=f"bi:{bid}"),InlineKeyboardButton(text="🗑️ Delete",callback_data=f"bdel:{bid}")],[InlineKeyboardButton(text="⬅️ Back",callback_data="button_manager")]])
    await callback.answer(); await callback.message.answer(f"🔘 <b>{html.quote(r[1])}</b>\n\nEdit this button:",reply_markup=kb,parse_mode="HTML")

@router.callback_query(F.data.startswith("bn:"))
async def button_name(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    editing[callback.from_user.id]=f"bn:{callback.data.split(':')[1]}"; await callback.answer(); await callback.message.answer("✏️ Send the new button name.")

@router.callback_query(F.data.startswith("bc:"))
async def button_content(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    editing[callback.from_user.id]=f"bc:{callback.data.split(':')[1]}"; await callback.answer(); await callback.message.answer("📝 Send the new button content. HTML is supported.")

@router.callback_query(F.data.startswith("bi:"))
async def button_image(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    editing[callback.from_user.id]=f"bi:{callback.data.split(':')[1]}"; await callback.answer(); await callback.message.answer("🖼️ Send a photo for this button.")

@router.callback_query(F.data.startswith("bdel:"))
async def button_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    bid=int(callback.data.split(":")[1]); delete_button(bid); await callback.answer("🗑️ Button deleted permanently",show_alert=True); await button_manager(callback)

@router.callback_query(F.data=="admin_manager")
async def admin_manager(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    rows=[]
    for uid in list_admins(): rows.append([InlineKeyboardButton(text=f"👑 {uid}{' (Owner)' if uid==OWNER_ID else ''}",callback_data="noop"), InlineKeyboardButton(text="🔒",callback_data=f"adel:{uid}") if uid!=OWNER_ID else InlineKeyboardButton(text="🔒",callback_data="noop")])
    rows += [[InlineKeyboardButton(text="➕ Add Admin",callback_data="aadd")],[InlineKeyboardButton(text="⬅️ Admin",callback_data="admin_home")]]
    await callback.answer(); await callback.message.answer("👑 <b>ADMIN MANAGER</b>\n\nOwner cannot be removed.",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode="HTML")

@router.callback_query(F.data=="aadd")
async def admin_add(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    editing[callback.from_user.id]="new_admin"; await callback.answer(); await callback.message.answer("➕ Send the Telegram numeric User ID to make an admin.")

@router.callback_query(F.data.startswith("adel:"))
async def admin_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    uid=int(callback.data.split(":")[1]); remove_admin(uid); await callback.answer("Admin removed.",show_alert=True); await admin_manager(callback)

@router.callback_query(F.data=="channel_manager")
async def channel_manager(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): await callback.answer("Not authorized.",show_alert=True); return
    rows=[[InlineKeyboardButton(text=f"📢 {r[2]}",callback_data=f"noop"),InlineKeyboardButton(text="🗑️",callback_data=f"cdel:{r[0]}")] for r in channels()]
    rows += [[InlineKeyboardButton(text="➕ Add Channel",callback_data="cadd")],[InlineKeyboardButton(text="⬅️ Admin",callback_data="admin_home")]]
    text="📢 <b>CHANNEL MANAGER</b>\n\nAll listed channels are mandatory for users.\n\n" + ("\n".join(f"• {r[2]}  <code>{r[1]}</code>" for r in channels()) or "No channels added yet.")
    await callback.answer(); await callback.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode="HTML")

@router.callback_query(F.data=="cadd")
async def channel_add(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    editing[callback.from_user.id]="new_channel"; await callback.answer(); await callback.message.answer("➕ <b>ADD CHANNEL</b>\n\nSend exactly 3 lines:\n<code>-1001234567890</code>\n<code>Channel Name</code>\n<code>https://t.me/yourchannel</code>\n\nThe bot must be admin in the channel to verify members / approve join requests.",parse_mode="HTML")

@router.callback_query(F.data.startswith("cdel:"))
async def channel_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    delete_channel(int(callback.data.split(":")[1])); await callback.answer("Channel removed.",show_alert=True); await channel_manager(callback)

@router.callback_query(F.data=="admin_home")
async def admin_home(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer(); await callback.message.answer("🛠️ <b>CONTROL CENTER</b>",reply_markup=admin_keyboard(),parse_mode="HTML")

@router.callback_query(F.data=="noop")
async def noop(callback: CallbackQuery): await callback.answer()

@router.callback_query(F.data=="buttons")
async def old_buttons(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"Edit {label}",callback_data=f"edit:{key}")] for key,label,_ in LEGACY_BUTTONS])
    await callback.answer(); await callback.message.answer("🔘 <b>LEGACY BUTTON SETTINGS</b>",reply_markup=kb,parse_mode="HTML")

@router.callback_query(F.data=="stats")
async def stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    total,joined,blocked=user_stats(); await callback.answer(); await callback.message.answer(f"📊 <b>BOT STATISTICS</b>\n\n👥 Total: <b>{total or 0}</b>\n✅ Verified: <b>{joined or 0}</b>\n🚫 Blocked/failed: <b>{blocked or 0}</b>\n📢 Required channels: <b>{len(channels())}</b>\n🔘 Active buttons: <b>{len(get_buttons())}</b>\n👑 Admins: <b>{len(list_admins())}</b>\n💰 Pending salary claims: <b>{len(reward_claims("pending"))}</b>",parse_mode="HTML")

@router.callback_query(F.data=="broadcast")
async def broadcast_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    broadcast_mode.add(callback.from_user.id); await callback.answer(); await callback.message.answer("📢 <b>BROADCAST MODE</b>\n\nSend text, photo, video or any copyable Telegram message.\nSend /cancel to stop.",parse_mode="HTML")

@router.callback_query(F.data=="preview")
async def preview(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.answer(); await callback.message.answer(render_text(get_setting("verified"),callback.from_user),parse_mode="HTML"); await callback.message.answer(render_text(get_setting("reward_claim_text"),callback.from_user),reply_markup=main_keyboard(show_claim=True),parse_mode="HTML")

@router.message(Command("cancel"))
async def cancel(message: Message):
    editing.pop(message.from_user.id,None); broadcast_mode.discard(message.from_user.id)
    if is_admin(message.from_user.id): await message.answer("❌ Cancelled.")

@router.message()
async def admin_input(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    uid=message.from_user.id
    if uid in editing:
        key=editing.pop(uid)
        if key=="guide_image":
            if not message.photo: await message.answer("❌ Send a photo."); editing[uid]=key; return
            set_setting("guide_image",message.photo[-1].file_id); await message.answer("✅ Guide image updated."); return
        if key=="reward_upi":
            upi=(message.text or "").strip()
            if "@" not in upi or len(upi)>100:
                await message.answer("❌ Invalid UPI ID. Send something like <code>name@upi</code>.",parse_mode="HTML"); editing[uid]=key; return
            amount=int(get_setting("reward_amount") or "180")
            create_reward_claim(uid,upi,amount)
            await message.answer(render_text(get_setting("reward_pending_text").replace("{amount}",str(amount)),message.from_user),parse_mode="HTML")
            for admin_id in list_admins():
                try:
                    await bot.send_message(admin_id,f"💰 <b>New ₹{amount} Salary Claim</b>\n\nUser: <code>{uid}</code>\nUPI: <code>{html.quote(upi)}</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Reward Claims",callback_data="reward_admin")]]),parse_mode="HTML")
                except Exception: pass
            return
        if key=="new_admin":
            try: new_id=int((message.text or "").strip()); add_admin(new_id); await message.answer(f"✅ Admin <code>{new_id}</code> added.",parse_mode="HTML")
            except ValueError: await message.answer("❌ Invalid numeric User ID.")
            return
        if key=="new_channel":
            parts=(message.text or "").splitlines()
            if len(parts)<3:
                await message.answer("❌ Send 3 lines: Channel ID, Channel Name, Join URL."); editing[uid]=key; return
            try: cid=int(parts[0].strip())
            except ValueError: await message.answer("❌ Channel ID must be numeric."); editing[uid]=key; return
            add_channel(cid,parts[1].strip(),parts[2].strip()); await message.answer("✅ Channel added. All users must join it."); return
        if key=="new_button":
            parts=(message.text or "").splitlines()
            if len(parts)<2: await message.answer("❌ First line = button name, remaining lines = content."); editing[uid]=key; return
            bid=create_button(parts[0].strip(),"\n".join(parts[1:]).strip()); await message.answer(f"✅ New button added (#{bid})."); return
        if key.startswith("bn:"):
            update_button(int(key.split(":")[1]),"name",message.text or ""); await message.answer("✅ Button name updated."); return
        if key.startswith("bc:"):
            update_button(int(key.split(":")[1]),"content",message.text or ""); await message.answer("✅ Button content updated."); return
        if key.startswith("bi:"):
            if not message.photo: await message.answer("❌ Send a photo."); editing[uid]=key; return
            update_button(int(key.split(":")[1]),"image_file_id",message.photo[-1].file_id); await message.answer("✅ Button image updated."); return
        if not message.text: await message.answer("❌ Please send text."); editing[uid]=key; return
        set_setting(key,message.text); await message.answer(f"✅ <b>{key}</b> updated.",parse_mode="HTML"); return

    if uid in broadcast_mode:
        broadcast_mode.discard(uid)
        with closing(db()) as conn: ids=[r[0] for r in conn.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()]
        sent=failed=0
        for target in ids:
            try: await bot.copy_message(chat_id=target,from_chat_id=message.chat.id,message_id=message.message_id); sent+=1; await asyncio.sleep(0.04)
            except (TelegramForbiddenError,TelegramBadRequest): failed+=1; mark_blocked(target)
            except Exception: failed+=1
        await message.answer(f"📢 <b>BROADCAST COMPLETE</b>\n\n✅ Sent: <b>{sent}</b>\n🚫 Failed: <b>{failed}</b>",parse_mode="HTML")

async def main():
    db().close(); bot=Bot(BOT_TOKEN); dp=Dispatcher(); dp.include_router(router); me=await bot.get_me(); logging.info("Started @%s",me.username); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
