#!/usr/bin/env python3
import os
import logging
import asyncio
import random
import string
import warnings
from datetime import datetime
from typing import Dict, Any, List, Optional
from threading import Thread

from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------- CONFIG --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://yourproject.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
PORT = int(os.environ.get("PORT", 10000))

if ADMIN_USER_ID == 0:
    logger.warning("ADMIN_USER_ID not set! Admin commands disabled.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------- DATABASE FUNCTIONS (same as before) --------------------
def add_user(user_id: int, username: str, first_name: str):
    supabase.table('users').upsert({
        'user_id': user_id,
        'username': username,
        'first_name': first_name,
        'joined_at': datetime.utcnow().isoformat()
    }).execute()

def is_user_blocked(user_id: int) -> bool:
    res = supabase.table('users').select('blocked').eq('user_id', user_id).execute()
    return res.data[0]['blocked'] if res.data else False

def block_user(user_id: int):
    supabase.table('users').update({'blocked': True}).eq('user_id', user_id).execute()

def unblock_user(user_id: int):
    supabase.table('users').update({'blocked': False}).eq('user_id', user_id).execute()

def get_all_users() -> List[int]:
    res = supabase.table('users').select('user_id').execute()
    return [u['user_id'] for u in res.data]

def get_stock(type_: str, category: str, option_name: str) -> Optional[Dict]:
    res = supabase.table('stocks').select('*').eq('type', type_).eq('category', category).eq('option_name', option_name).execute()
    return res.data[0] if res.data else None

def set_min_qty(type_: str, category: str, option_name: str, min_qty: int):
    supabase.table('stocks').update({'min_quantity': min_qty}).eq('type', type_).eq('category', category).eq('option_name', option_name).execute()

def set_price(type_: str, category: str, option_name: str, price: float):
    supabase.table('stocks').update({'price': price}).eq('type', type_).eq('category', category).eq('option_name', option_name).execute()

def add_codes(type_: str, category: str, option_name: str, codes_list: List[str]):
    existing = get_stock(type_, category, option_name)
    if existing:
        new_codes = existing['codes'] + codes_list
        supabase.table('stocks').update({
            'codes': new_codes,
            'available_stock': len(new_codes)
        }).eq('type', type_).eq('category', category).eq('option_name', option_name).execute()
    else:
        supabase.table('stocks').insert({
            'type': type_,
            'category': category,
            'option_name': option_name,
            'codes': codes_list,
            'available_stock': len(codes_list),
            'min_quantity': 1,
            'price': 0.0
        }).execute()

def add_premium_account(type_: str, category: str, option_name: str, account_message: str):
    existing = get_stock(type_, category, option_name)
    if existing:
        new_codes = existing['codes'] + [account_message]
        supabase.table('stocks').update({
            'codes': new_codes,
            'available_stock': len(new_codes)
        }).eq('type', type_).eq('category', category).eq('option_name', option_name).execute()
    else:
        supabase.table('stocks').insert({
            'type': type_,
            'category': category,
            'option_name': option_name,
            'codes': [account_message],
            'available_stock': 1,
            'min_quantity': 1,
            'price': 0.0
        }).execute()

def create_order(order_id: str, user_id: int, type_: str, category: str,
                 option_name: str, quantity: int, price_per_unit: float,
                 total_amount: float, payer_name: str = None, screenshot_url: str = None):
    supabase.table('orders').insert({
        'order_id': order_id,
        'user_id': user_id,
        'type': type_,
        'category': category,
        'option_name': option_name,
        'quantity': quantity,
        'price_per_unit': price_per_unit,
        'total_amount': total_amount,
        'payer_name': payer_name,
        'screenshot_url': screenshot_url,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat()
    }).execute()

def update_order_status(order_id: str, status: str, codes: str = None):
    supabase.table('orders').update({'status': status, 'codes': codes}).eq('order_id', order_id).execute()

def get_user_orders(user_id: int, status: str = 'accepted') -> List[Dict]:
    res = supabase.table('orders').select('order_id, option_name, quantity, total_amount, status, codes').eq('user_id', user_id).eq('status', status).execute()
    return res.data

def get_order_by_id(order_id: str) -> Optional[Dict]:
    res = supabase.table('orders').select('*').eq('order_id', order_id).execute()
    return res.data[0] if res.data else None

def get_last_10_purchases() -> List[Dict]:
    res = supabase.table('orders').select('*').eq('status', 'accepted').order('created_at', desc=True).limit(10).execute()
    return res.data

def get_qr() -> Optional[str]:
    res = supabase.table('qr_config').select('qr_url').eq('id', True).execute()
    return res.data[0]['qr_url'] if res.data else None

def update_qr(url: str):
    supabase.table('qr_config').upsert({'id': True, 'qr_url': url}).execute()

def is_bot_on() -> bool:
    res = supabase.table('bot_status').select('is_on').eq('id', True).execute()
    return res.data[0]['is_on'] if res.data else True

def set_bot_on_off(status: bool):
    supabase.table('bot_status').upsert({'id': True, 'is_on': status}).execute()

# -------------------- FORMATTERS --------------------
def generate_order_id(user_id: int) -> str:
    return f"ORD_{user_id}_{int(datetime.utcnow().timestamp())}_{''.join(random.choices(string.digits, k=4))}"

def format_my_orders(orders: List[Dict]) -> str:
    if not orders:
        return "📭 *No confirmed orders yet.*\n\nUse the *Buy Items* button to place an order."
    lines = ["✅ *YOUR CONFIRMED ORDERS*\n━━━━━━━━━━━━━━━━━━━"]
    for o in orders:
        lines.append(
            f"🆔 `{o['order_id']}`\n"
            f"📦 {o['option_name']} × {o['quantity']}\n"
            f"💰 ₹{o['total_amount']}\n"
            f"───────────────────"
        )
    return "\n".join(lines)

def format_last_10(purchases: List[Dict]) -> str:
    if not purchases:
        return "📭 *No successful purchases yet.*"
    lines = ["🏆 *LAST 10 SUCCESSFUL PURCHASES*\n━━━━━━━━━━━━━━━━━━━"]
    for p in purchases:
        lines.append(
            f"🆔 `{p['order_id']}`\n"
            f"👤 User: {p['user_id']}\n"
            f"📦 {p['option_name']} × {p['quantity']}\n"
            f"💰 ₹{p['total_amount']}\n"
            f"───────────────────"
        )
    return "\n".join(lines)

def format_stock_report() -> str:
    res = supabase.table('stocks').select('*').execute()
    if not res.data:
        return "📦 *No products found in stock.*"
    lines = ["📦 *STOCK REPORT*\n━━━━━━━━━━━━━━━━━━━"]
    for item in res.data:
        lines.append(
            f"🔹 *{item['type'].upper()}* | *{item['category'].upper()}*\n"
            f"   📛 Option: {item['option_name']}\n"
            f"   📦 Stock: {item['available_stock']}\n"
            f"   ⚠️ Min Qty: {item['min_quantity']}\n"
            f"   💰 Price: ₹{item['price']}\n"
            f"───────────────────"
        )
    return "\n".join(lines)

def format_invoice(order_id: str, option_name: str, quantity: int, total: float) -> str:
    return (
        f"🧾 *INVOICE*\n━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 `{order_id}`\n"
        f"📦 {option_name} × {quantity}\n"
        f"💰 *Pay Exactly:* ₹{total:.2f}\n"
        f"⚠️ *CRITICAL:* Pay exact amount including paise.\n"
        f"⏳ QR valid for 10 minutes.\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

# -------------------- KEYBOARDS --------------------
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🛍️ Buy Items", "📦 My Orders"],
        ["🔄 Recover Orders", "🆘 Support"],
        ["📢 Our Channels"]
    ], resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ ADD", "📦 STOCK"],
        ["💰 CHANGE PRICES", "📉 SET MIN QUANTITY"],
        ["📢 BROADCAST", "📋 LAST 10 PURCHASES"],
        ["🖼️ UPDATE QR", "👥 ACTIVE USERS"],
        ["🚫 BLOCK", "✅ UNBLOCK"],
        ["🔌 TURN OFF", "🔌 TURN ON"],
        ["🔙 User Menu"]
    ], resize_keyboard=True)

# -------------------- CONVERSATION STATES --------------------
ASK_QUANTITY, ASK_PAYER_NAME, ASK_SCREENSHOT = range(3)

# -------------------- USER HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    context.user_data.clear()

    if not is_bot_on() and user_id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 *Bot is currently OFF.*\nPlease wait for the admin to turn it on.", parse_mode="Markdown")
        return
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ *You have been blocked* by the admin and cannot use this bot.", parse_mode="Markdown")
        return

    add_user(user_id, user.username, user.first_name)

    welcome = (
        "✨ *WELCOME TO AUTOEARNX STORE* ✨\n\n"
        "🛒 Your one‑stop shop for vouchers & premium accounts.\n"
        "💳 We accept payments via QR – exact amount only.\n\n"
        "👇 *Use the buttons below to navigate.*"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=get_main_keyboard())
    if user_id == ADMIN_USER_ID:
        await update.message.reply_text("ℹ️ *Admin notice:* You are now in user mode. Use /admin to return to admin panel.", parse_mode="Markdown")

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ *You are blocked.* You cannot use this bot.", parse_mode="Markdown")
        return

    text = update.message.text
    if text in ["🛍️ Buy Items", "Buy Items"]:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎫 Vouchers", callback_data="buy_vouchers")],
            [InlineKeyboardButton("⭐ Premiums", callback_data="buy_premiums")]
        ])
        await update.message.reply_text("🛒 *Select a category:*", parse_mode="Markdown", reply_markup=keyboard)
    elif text in ["📦 My Orders", "My Orders"]:
        orders = get_user_orders(user_id, 'accepted')
        await update.message.reply_text(format_my_orders(orders), parse_mode="Markdown")
    elif text in ["🔄 Recover Orders", "Recover Orders"]:
        await update.message.reply_text("📝 *Send your Order ID* (e.g., `ORD_1234567890_1234`):", parse_mode="Markdown")
        context.user_data['recover_mode'] = True
    elif text in ["🆘 Support", "Support", "support"]:
        await update.message.reply_text(
            "🆘 *SUPPORT CONTACT*\n━━━━━━━━━━━━━━━━━━━\n👤 @AutoEarnX_SupportBot\n\n"
            "Please message us for any issues or queries.",
            parse_mode="Markdown"
        )
    elif text in ["📢 Our Channels", "Our Channels"]:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url="https://t.me/your_channel")]])
        await update.message.reply_text("📢 *Join our official channels* for updates and deals:", parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text("🔁 *Please use the buttons below.*", parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_recover_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ You are blocked.")
        return
    if context.user_data.get('recover_mode'):
        order_id = update.message.text.strip()
        # Accept any order ID format (must start with ORD_)
        if not order_id.startswith("ORD_"):
            await update.message.reply_text("❌ *Invalid Order ID format.* It should start with `ORD_`.", parse_mode="Markdown")
            context.user_data['recover_mode'] = False
            return
        order = get_order_by_id(order_id)
        if order and order['status'] == 'accepted':
            await update.message.reply_text(
                f"✅ *ORDER FOUND*\n━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 `{order['order_id']}`\n"
                f"📦 {order['option_name']} × {order['quantity']}\n"
                f"💰 ₹{order['total_amount']}\n"
                f"💎 *Codes/Account:*\n{order['codes']}\n"
                f"━━━━━━━━━━━━━━━━━━━",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ *Order not found* or not yet accepted.", parse_mode="Markdown")
        context.user_data['recover_mode'] = False

# -------------------- BUY CONVERSATION --------------------
async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if is_user_blocked(user_id):
        await query.answer("You are blocked", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data == "buy_vouchers":
        await query.edit_message_text("🎫 *Select voucher brand:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👗 Shein", callback_data="voucher_shein")],
            [InlineKeyboardButton("🛍️ Myntra", callback_data="voucher_myntra")],
            [InlineKeyboardButton("🛒 BigBasket", callback_data="voucher_bigbasket")]
        ]))
        return
    elif data == "buy_premiums":
        await query.edit_message_text("⭐ *Select premium service:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Netflix Premium", callback_data="premium_netflix")]
        ]))
        return
    elif data.startswith("voucher_"):
        brand = data.split("_")[1]
        context.user_data['buy_type'] = 'voucher'
        context.user_data['category'] = brand
        options = {
            "shein": ["500 Off On 500", "1000 Off On 1000", "2000 Off On 2000", "4000 Off On 4000"],
            "myntra": ["100rs Off", "150rs Off"],
            "bigbasket": ["150rs CashBack On 150rs"]
        }.get(brand, [])
        buttons = [[InlineKeyboardButton(opt, callback_data=f"opt_{opt.replace(' ', '_')}")] for opt in options]
        await query.edit_message_text(f"📌 *Choose {brand.upper()} option:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return
    elif data.startswith("opt_"):
        option_raw = data[4:].replace('_', ' ')
        context.user_data['option_name'] = option_raw
        stock = get_stock(context.user_data['buy_type'], context.user_data['category'], option_raw)
        if not stock or stock['available_stock'] <= 0:
            await query.edit_message_text("❌ *Out of stock* or option not found.", parse_mode="Markdown")
            return
        context.user_data['product_info'] = stock
        await query.edit_message_text(
            f"🏷️ *{option_raw}*\n"
            f"📦 *Available stock:* {stock['available_stock']}\n"
            f"⚠️ *Minimum quantity:* {stock['min_quantity']}\n"
            f"💰 *Price per unit:* ₹{stock['price']}\n\n"
            f"📝 *Enter the quantity you want to buy:*",
            parse_mode="Markdown"
        )
        return ASK_QUANTITY
    elif data.startswith("premium_"):
        service = data.split("_")[1]
        context.user_data['buy_type'] = 'premium'
        context.user_data['category'] = service
        option_name = "Netflix Premium"
        context.user_data['option_name'] = option_name
        stock = get_stock('premium', service, option_name)
        if not stock or stock['available_stock'] <= 0:
            await query.edit_message_text("❌ *No premium accounts available* at the moment.", parse_mode="Markdown")
            return
        context.user_data['product_info'] = stock
        await query.edit_message_text(
            f"⭐ *{option_name}*\n"
            f"📦 *Available:* {stock['available_stock']}\n"
            f"⚠️ *Minimum quantity:* {stock['min_quantity']}\n"
            f"💰 *Price per unit:* ₹{stock['price']}\n\n"
            f"📝 *Enter quantity:*",
            parse_mode="Markdown"
        )
        return ASK_QUANTITY
    return ConversationHandler.END

async def ask_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ You are blocked.")
        return ConversationHandler.END
    try:
        qty = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ *Invalid number.* Please send a valid integer quantity.", parse_mode="Markdown")
        return ASK_QUANTITY
    info = context.user_data['product_info']
    if qty < info['min_quantity']:
        await update.message.reply_text(f"⚠️ *Quantity below minimum* ({info['min_quantity']}). Please enter a higher number.", parse_mode="Markdown")
        return ASK_QUANTITY
    if qty > info['available_stock']:
        await update.message.reply_text(f"❌ *Only {info['available_stock']} codes available* for this option.", parse_mode="Markdown")
        return ASK_QUANTITY
    context.user_data['quantity'] = qty
    total = qty * info['price']
    context.user_data['total_amount'] = total
    order_id = generate_order_id(update.effective_user.id)
    context.user_data['order_id'] = order_id
    qr_url = get_qr()
    if not qr_url:
        await update.message.reply_text("⚠️ *QR code not configured.* Please contact admin.", parse_mode="Markdown")
        return ConversationHandler.END
    invoice_text = format_invoice(order_id, context.user_data['option_name'], qty, total)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment")]])
    await update.message.reply_photo(photo=qr_url, caption=invoice_text, parse_mode="Markdown", reply_markup=keyboard)
    # Now go to ASK_PAYER_NAME state and wait for the button press
    return ASK_PAYER_NAME

# --- Global handler for Verify Payment button ---
async def verify_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if is_user_blocked(user_id):
        await query.answer("You are blocked", show_alert=True)
        return
    await query.answer()
    # Check if the user is in the correct state and has an active order
    if context.user_data.get('order_id') and context.user_data.get('product_info'):
        await query.edit_message_text("📝 *Please enter the payer name* (the name used for payment):", parse_mode="Markdown")
        # Set a flag to indicate we are waiting for payer name
        context.user_data['awaiting_payer_name'] = True
        # Stay in the same conversation (we don't return a state, but the conversation is still active)
        # The next message will be handled by ask_payer_name
    else:
        await query.answer("No active order. Please start a new purchase with /start", show_alert=True)

async def ask_payer_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ You are blocked.")
        return ConversationHandler.END
    # If we are waiting for payer name from the button
    if context.user_data.get('awaiting_payer_name'):
        context.user_data['awaiting_payer_name'] = False
        payer_name = update.message.text.strip()
        if not payer_name:
            await update.message.reply_text("❌ Payer name cannot be empty. Please send the name again.")
            return ASK_PAYER_NAME
        context.user_data['payer_name'] = payer_name
        await update.message.reply_text("📸 *Now send the screenshot* of your payment (as a photo).", parse_mode="Markdown")
        return ASK_SCREENSHOT
    else:
        # If someone sends a message while not expecting payer name
        await update.message.reply_text("❌ Please click the 'Verify Payment' button first to confirm your payment.")
        return ASK_PAYER_NAME

async def ask_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_blocked(user_id):
        await update.message.reply_text("❌ You are blocked.")
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("❌ *Please send a photo* as screenshot.", parse_mode="Markdown")
        return ASK_SCREENSHOT
    photo_file = await update.message.photo[-1].get_file()
    file_id = photo_file.file_id
    context.user_data['screenshot_url'] = file_id
    create_order(
        order_id=context.user_data['order_id'],
        user_id=update.effective_user.id,
        type_=context.user_data['buy_type'],
        category=context.user_data['category'],
        option_name=context.user_data['option_name'],
        quantity=context.user_data['quantity'],
        price_per_unit=context.user_data['product_info']['price'],
        total_amount=context.user_data['total_amount'],
        payer_name=context.user_data['payer_name'],
        screenshot_url=file_id
    )
    await update.message.reply_text("⏳ *Order placed!* Waiting for admin approval.\nYou will receive the codes once approved.", parse_mode="Markdown")
    admin_text = (
        f"🆕 *NEW ORDER PENDING*\n━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 `{context.user_data['order_id']}`\n"
        f"👤 {update.effective_user.first_name} (@{update.effective_user.username})\n"
        f"📦 {context.user_data['option_name']} × {context.user_data['quantity']}\n"
        f"💰 ₹{context.user_data['total_amount']}\n"
        f"🧾 Payer: {context.user_data['payer_name']}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{context.user_data['order_id']}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"decline_{context.user_data['order_id']}")]
    ])
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_text, parse_mode="Markdown", reply_markup=keyboard)
    # Clean up
    for key in ['buy_type', 'category', 'option_name', 'quantity', 'total_amount', 'order_id', 'payer_name', 'product_info', 'awaiting_payer_name']:
        context.user_data.pop(key, None)
    return ConversationHandler.END

# -------------------- ADMIN HANDLERS --------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    context.user_data.clear()
    await update.message.reply_text("🛠️ *Admin Panel* – use the buttons below.", parse_mode="Markdown", reply_markup=get_admin_keyboard())

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    text = update.message.text.strip()
    if text == "🔙 User Menu":
        await update.message.reply_text("🔁 *Returning to user menu...*", parse_mode="Markdown", reply_markup=get_main_keyboard())
        return
    action = context.user_data.get('admin_action')
    if action:
        await process_admin_action(update, context, action, text)
        return
    # Admin button commands
    if text == "🔌 TURN OFF":
        set_bot_on_off(False)
        await update.message.reply_text("🔴 *Bot is now OFF.* Users cannot interact.", parse_mode="Markdown")
    elif text == "🔌 TURN ON":
        set_bot_on_off(True)
        await update.message.reply_text("🟢 *Bot is now ON.*", parse_mode="Markdown")
    elif text == "📦 STOCK":
        await update.message.reply_text(format_stock_report(), parse_mode="Markdown")
    elif text == "📋 LAST 10 PURCHASES":
        await update.message.reply_text(format_last_10(get_last_10_purchases()), parse_mode="Markdown")
    elif text == "👥 ACTIVE USERS":
        users = get_all_users()
        await update.message.reply_text(f"👥 *Total users who started the bot:* {len(users)}", parse_mode="Markdown")
    elif text == "🖼️ UPDATE QR":
        context.user_data['admin_action'] = 'update_qr'
        await update.message.reply_text("📸 *Send the new QR code* as a photo:", parse_mode="Markdown")
    elif text == "➕ ADD":
        context.user_data['admin_action'] = 'add'
        context.user_data['add_step'] = 1
        context.user_data['add_data'] = {}
        await update.message.reply_text("📦 *ADD PRODUCT*\nStep 1/4: Send type (`voucher` / `premium`):", parse_mode="Markdown")
    elif text == "💰 CHANGE PRICES":
        context.user_data['admin_action'] = 'price'
        await update.message.reply_text(
            "💰 *CHANGE PRICE*\n"
            "Send in this format:\n"
            "`voucher shein '500 Off On 500' 99`\n"
            "or\n"
            "`premium netflix 'Netflix Premium' 499`\n\n"
            "Type, category, option name (in quotes if spaces), new price.",
            parse_mode="Markdown"
        )
    elif text == "📉 SET MIN QUANTITY":
        context.user_data['admin_action'] = 'minqty'
        await update.message.reply_text(
            "📉 *SET MINIMUM QUANTITY*\n"
            "Send in this format:\n"
            "`voucher shein '500 Off On 500' 2`\n"
            "or\n"
            "`premium netflix 'Netflix Premium' 1`",
            parse_mode="Markdown"
        )
    elif text == "📢 BROADCAST":
        context.user_data['admin_action'] = 'broadcast'
        await update.message.reply_text("📢 *Send the message* you want to broadcast to all users:", parse_mode="Markdown")
    elif text == "🚫 BLOCK":
        context.user_data['admin_action'] = 'block'
        await update.message.reply_text("🚫 *Send the username* (without @) to block:", parse_mode="Markdown")
    elif text == "✅ UNBLOCK":
        context.user_data['admin_action'] = 'unblock'
        await update.message.reply_text("✅ *Send the username* (without @) to unblock:", parse_mode="Markdown")
    else:
        pass

async def process_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, text: str):
    if action == 'update_qr':
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            update_qr(file_id)
            await update.message.reply_text("✅ *QR code updated successfully.*", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Please send a photo.")
        context.user_data.pop('admin_action')
    elif action == 'add':
        step = context.user_data.get('add_step', 1)
        if step == 1:
            if text.lower() not in ['voucher', 'premium']:
                await update.message.reply_text("❌ Type must be 'voucher' or 'premium'. Try again:")
                return
            context.user_data['add_data']['type'] = text.lower()
            await update.message.reply_text("Step 2/4: Send category (`shein` / `myntra` / `bigbasket` / `netflix`):", parse_mode="Markdown")
            context.user_data['add_step'] = 2
        elif step == 2:
            valid = ['shein', 'myntra', 'bigbasket', 'netflix']
            if text.lower() not in valid:
                await update.message.reply_text(f"❌ Category must be one of: {', '.join(valid)}. Try again:")
                return
            context.user_data['add_data']['category'] = text.lower()
            await update.message.reply_text("Step 3/4: Send option name (e.g., '500 Off On 500'):", parse_mode="Markdown")
            context.user_data['add_step'] = 3
        elif step == 3:
            context.user_data['add_data']['option'] = text
            await update.message.reply_text("Step 4/4: Send codes/accounts line by line. Send `/done` when finished:", parse_mode="Markdown")
            context.user_data['add_step'] = 4
            context.user_data['add_codes_list'] = []
        elif step == 4:
            if text == '/done':
                if context.user_data['add_data']['type'] == 'premium':
                    full = "\n".join(context.user_data['add_codes_list'])
                    add_premium_account('premium', context.user_data['add_data']['category'], context.user_data['add_data']['option'], full)
                    await update.message.reply_text("✅ *Premium account added successfully.*", parse_mode="Markdown")
                else:
                    add_codes('voucher', context.user_data['add_data']['category'], context.user_data['add_data']['option'], context.user_data['add_codes_list'])
                    await update.message.reply_text(f"✅ *{len(context.user_data['add_codes_list'])} voucher codes added successfully.*", parse_mode="Markdown")
                context.user_data.pop('admin_action')
                context.user_data.pop('add_step')
                context.user_data.pop('add_data')
                context.user_data.pop('add_codes_list')
            else:
                context.user_data['add_codes_list'].append(text)
                await update.message.reply_text(f"📌 Added. Total codes: {len(context.user_data['add_codes_list'])}. Send another or `/done` to finish.", parse_mode="Markdown")
    elif action == 'price':
        import re
        match = re.match(r'(\w+)\s+(\w+)\s+(.+?)\s+(\d+(?:\.\d+)?)$', text)
        if not match:
            await update.message.reply_text("❌ Invalid format. Use:\n`voucher shein '500 Off On 500' 99`", parse_mode="Markdown")
            return
        type_, cat, opt, price_str = match.groups()
        try:
            price = float(price_str)
        except:
            await update.message.reply_text("❌ Price must be a number.")
            return
        opt = opt.strip("'\"")
        set_price(type_, cat, opt, price)
        await update.message.reply_text(f"✅ *Price for '{opt}' set to ₹{price}.*", parse_mode="Markdown")
        context.user_data.pop('admin_action')
    elif action == 'minqty':
        import re
        match = re.match(r'(\w+)\s+(\w+)\s+(.+?)\s+(\d+)$', text)
        if not match:
            await update.message.reply_text("❌ Invalid format. Use:\n`voucher shein '500 Off On 500' 2`", parse_mode="Markdown")
            return
        type_, cat, opt, minq_str = match.groups()
        try:
            minq = int(minq_str)
        except:
            await update.message.reply_text("❌ Min quantity must be an integer.")
            return
        opt = opt.strip("'\"")
        set_min_qty(type_, cat, opt, minq)
        await update.message.reply_text(f"✅ *Minimum quantity for '{opt}' set to {minq}.*", parse_mode="Markdown")
        context.user_data.pop('admin_action')
    elif action == 'broadcast':
        users = get_all_users()
        if not users:
            await update.message.reply_text("No users to broadcast to.")
            context.user_data.pop('admin_action')
            return
        sent = 0
        failed = 0
        await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 *BROADCAST MESSAGE*\n\n{text}", parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        await update.message.reply_text(f"✅ *Broadcast complete.* Sent to {sent} users. Failed: {failed}", parse_mode="Markdown")
        context.user_data.pop('admin_action')
    elif action == 'block':
        username = text.lstrip('@')
        res = supabase.table('users').select('user_id').eq('username', username).execute()
        if res.data:
            block_user(res.data[0]['user_id'])
            await update.message.reply_text(f"🚫 *Blocked @{username}*", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ User @{username} not found.", parse_mode="Markdown")
        context.user_data.pop('admin_action')
    elif action == 'unblock':
        username = text.lstrip('@')
        res = supabase.table('users').select('user_id').eq('username', username).execute()
        if res.data:
            unblock_user(res.data[0]['user_id'])
            await update.message.reply_text(f"✅ *Unblocked @{username}*", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ User @{username} not found.", parse_mode="Markdown")
        context.user_data.pop('admin_action')
    else:
        context.user_data.pop('admin_action', None)
        await update.message.reply_text("Action timed out. Use admin panel again.")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Unauthorized", show_alert=True)
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("accept_"):
        order_id = data[7:]
        order = get_order_by_id(order_id)
        if order and order['status'] == 'pending':
            stock = get_stock(order['type'], order['category'], order['option_name'])
            if not stock or stock['available_stock'] < order['quantity']:
                await query.edit_message_text("❌ Not enough stock to accept.")
                return
            codes_to_give = stock['codes'][:order['quantity']]
            remaining = stock['codes'][order['quantity']:]
            supabase.table('stocks').update({
                'codes': remaining,
                'available_stock': len(remaining)
            }).eq('type', order['type']).eq('category', order['category']).eq('option_name', order['option_name']).execute()
            codes_str = "\n".join(codes_to_give)
            update_order_status(order_id, 'accepted', codes_str)
            await context.bot.send_message(chat_id=order['user_id'], text=f"✅ *Your order {order_id} has been accepted!*\n\nHere are your codes/account:\n{codes_str}", parse_mode="Markdown")
            await query.edit_message_text(f"✅ *Order {order_id} accepted.*", parse_mode="Markdown")
        else:
            await query.edit_message_text("Order already processed or invalid.")
    elif data.startswith("decline_"):
        order_id = data[8:]
        update_order_status(order_id, 'declined')
        order = get_order_by_id(order_id)
        if order:
            await context.bot.send_message(chat_id=order['user_id'], text=f"❌ *Your order {order_id} was declined by admin.*", parse_mode="Markdown")
        await query.edit_message_text(f"❌ *Order {order_id} declined.*", parse_mode="Markdown")

# -------------------- FLASK HEALTH CHECK --------------------
flask_app = Flask('')
@flask_app.route('/')
def health():
    return "OK", 200
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# -------------------- MAIN --------------------
def main():
    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask health server running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Order of handlers matters
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel, filters.User(user_id=ADMIN_USER_ID)))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(accept_|decline_)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_USER_ID), handle_admin_text))

    # Buy conversation with the global verify_payment handler
    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_callback, pattern="^(buy_|voucher_|opt_|premium_)")],
        states={
            ASK_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_quantity)],
            ASK_PAYER_NAME: [CallbackQueryHandler(verify_payment_callback, pattern="verify_payment"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, ask_payer_name)],
            ASK_SCREENSHOT: [MessageHandler(filters.PHOTO, ask_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("Cancelled."))],
        allow_reentry=True
    )
    app.add_handler(buy_conv)

    # User menu and recover order
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recover_order))  # catches any text; but the regex is inside

    logger.info("Bot started polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
