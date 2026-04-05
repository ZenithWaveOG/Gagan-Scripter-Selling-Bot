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

# Suppress PTB warning about CallbackQueryHandler in ConversationHandler
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

# Flask for health check server
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from supabase import create_client, Client

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------- CONFIG --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://yourproject.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))  # Set to 0 if not provided
PORT = int(os.environ.get("PORT", 10000))

if ADMIN_USER_ID == 0:
    logger.warning("ADMIN_USER_ID not set! Admin commands will be disabled.")

# -------------------- SUPABASE SETUP --------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- Database helper functions ----------
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

def update_stock(type_: str, category: str, option_name: str, new_stock: int):
    supabase.table('stocks').update({'available_stock': new_stock}).eq('type', type_).eq('category', category).eq('option_name', option_name).execute()

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
    update_data = {'status': status}
    if codes:
        update_data['codes'] = codes
    supabase.table('orders').update(update_data).eq('order_id', order_id).execute()

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

# -------------------- HELPER FUNCTIONS --------------------
def generate_order_id(user_id: int) -> str:
    return f"ORD_{user_id}_{int(datetime.utcnow().timestamp())}_{''.join(random.choices(string.digits, k=4))}"

def format_order_invoice(order_id: str, option_name: str, quantity: int, total: float) -> str:
    return (
        f"🧾 **INVOICE**\n━━━━━━━━━━━━━━\n"
        f"🆔 `{order_id}`\n"
        f"📦 {option_name} × {quantity}\n"
        f"💰 Pay Exactly: ₹{total:.2f}\n"
        f"⚠️ CRITICAL: Pay exact amount including paise.\n"
        f"⏳ QR valid for 10 minutes."
    )

def format_my_orders(orders: List[Dict]) -> str:
    if not orders:
        return "📭 You have no confirmed orders yet."
    lines = ["✅ **Your Confirmed Orders**\n"]
    for o in orders:
        lines.append(f"🆔 `{o['order_id']}`\n📦 {o['option_name']} (x{o['quantity']})\n💰 ₹{o['total_amount']}\n")
    return "\n".join(lines)

def format_last_10(purchases: List[Dict]) -> str:
    if not purchases:
        return "No purchases yet."
    lines = ["🏆 **Last 10 Successful Purchases**\n"]
    for p in purchases:
        lines.append(f"🆔 `{p['order_id']}` | 👤 {p['user_id']} | {p['option_name']} x{p['quantity']} | ₹{p['total_amount']}")
    return "\n".join(lines)

def format_stock_report() -> str:
    res = supabase.table('stocks').select('*').execute()
    if not res.data:
        return "No products found."
    lines = ["📦 **STOCK REPORT**\n"]
    for item in res.data:
        lines.append(f"🔹 {item['type'].upper()} | {item['category']} | {item['option_name']}\n   Stock: {item['available_stock']} | Min: {item['min_quantity']} | Price: ₹{item['price']}")
    return "\n".join(lines)

# -------------------- KEYBOARDS --------------------
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🛍️ Buy Items", "📦 My Orders"],
        ["🔄 Recover Orders", "🆘 Support"],
        ["📢 Our Channels"]
    ], resize_keyboard=True)

# -------------------- USER HANDLERS --------------------
# Conversation states
(CHOOSE_TYPE, CHOOSE_VOUCHER_CAT, CHOOSE_VOUCHER_OPTION,
 CHOOSE_PREMIUM_CAT, CHOOSE_PREMIUM_OPTION, ASK_QUANTITY,
 ASK_PAYER_NAME, ASK_SCREENSHOT) = range(8)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_bot_on() and user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Bot is currently OFF. Please wait for admin to turn it ON.")
        return
    if is_user_blocked(user.id):
        await update.message.reply_text("❌ You have been blocked by the admin.")
        return
    add_user(user.id, user.username, user.first_name)
    welcome_msg = (
        "✨ *Welcome to AutoEarnX Store* ✨\n\n"
        "🛒 Your one-stop shop for vouchers & premium accounts.\n"
        "Use the buttons below to navigate.\n"
        "📌 We accept payments via QR (exact amount only)."
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    logger.info(f"Menu text received from {user_id}: '{text}'")
    
    if text in ["🛍️ Buy Items", "Buy Items"]:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎫 Vouchers", callback_data="buy_vouchers")],
            [InlineKeyboardButton("⭐ Premiums", callback_data="buy_premiums")]
        ])
        await update.message.reply_text("Select category:", reply_markup=keyboard)
    elif text in ["📦 My Orders", "My Orders"]:
        orders = get_user_orders(user_id, 'accepted')
        await update.message.reply_text(format_my_orders(orders), parse_mode="Markdown")
    elif text in ["🔄 Recover Orders", "Recover Orders"]:
        await update.message.reply_text("📝 Please send the Order ID you want to recover:")
        context.user_data['recover_mode'] = True
    elif text in ["🆘 Support", "Support", "support"]:
        await update.message.reply_text(
            "🆘 **Support Contact**\n━━━━━━━━━━━━━━\n@AutoEarnX_SupportBot",
            parse_mode="Markdown"
        )
    elif text in ["📢 Our Channels", "Our Channels"]:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url="https://t.me/your_channel")]
        ])
        await update.message.reply_text("Join our official channels for updates and deals:", reply_markup=keyboard)
    else:
        await update.message.reply_text("Please use the buttons below.", reply_markup=get_main_keyboard())

async def handle_recover_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('recover_mode'):
        order_id = update.message.text.strip()
        order = get_order_by_id(order_id)
        if order and order['status'] == 'accepted':
            await update.message.reply_text(
                f"✅ **Order Found**\n━━━━━━━━━━━━━━\n🆔 `{order['order_id']}`\n📦 {order['option_name']} x{order['quantity']}\n💎 **Codes/Account:**\n{order['codes']}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ No accepted order found with that ID.")
        context.user_data['recover_mode'] = False

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "buy_vouchers":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👗 Shein", callback_data="voucher_shein")],
            [InlineKeyboardButton("🛍️ Myntra", callback_data="voucher_myntra")],
            [InlineKeyboardButton("🛒 BigBasket", callback_data="voucher_bigbasket")]
        ])
        await query.edit_message_text("Select voucher brand:", reply_markup=keyboard)
        return
    elif data == "buy_premiums":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Netflix Premium", callback_data="premium_netflix")]
        ])
        await query.edit_message_text("Select premium service:", reply_markup=keyboard)
        return
    elif data.startswith("voucher_"):
        brand = data.split("_")[1]
        context.user_data['buy_type'] = 'voucher'
        context.user_data['category'] = brand
        if brand == "shein":
            options = ["500 Off On 500", "1000 Off On 1000", "2000 Off On 2000", "4000 Off On 4000"]
        elif brand == "myntra":
            options = ["100rs Off", "150rs Off"]
        elif brand == "bigbasket":
            options = ["150rs CashBack On 150rs"]
        else:
            return
        buttons = [[InlineKeyboardButton(opt, callback_data=f"opt_{opt.replace(' ', '_')}")] for opt in options]
        await query.edit_message_text(f"Choose {brand.upper()} option:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    elif data.startswith("opt_"):
        option_raw = data[4:].replace('_', ' ')
        context.user_data['option_name'] = option_raw
        stock = get_stock(context.user_data['buy_type'], context.user_data['category'], option_raw)
        if not stock:
            await query.edit_message_text("❌ Option not found in database. Contact admin.")
            return
        if stock['available_stock'] <= 0:
            await query.edit_message_text("❌ Out of stock. Please try later.")
            return
        context.user_data['product_info'] = stock
        await query.edit_message_text(
            f"🏷️ *{option_raw}*\n"
            f"📦 Available stock: {stock['available_stock']}\n"
            f"⚠️ Minimum quantity: {stock['min_quantity']}\n"
            f"🏷️ Price per unit: ₹{stock['price']}\n\n"
            f"📋 Enter the amount to buy:",
            parse_mode="Markdown"
        )
        return ASK_QUANTITY
    elif data.startswith("premium_"):
        service = data.split("_")[1]
        context.user_data['buy_type'] = 'premium'
        context.user_data['category'] = service
        if service == "netflix":
            option_name = "Netflix Premium"
            context.user_data['option_name'] = option_name
            stock = get_stock('premium', service, option_name)
            if not stock:
                await query.edit_message_text("❌ Premium option not configured. Contact admin.")
                return
            if stock['available_stock'] <= 0:
                await query.edit_message_text("❌ No premium accounts available.")
                return
            context.user_data['product_info'] = stock
            await query.edit_message_text(
                f"⭐ *Netflix Premium*\n"
                f"📦 Available: {stock['available_stock']}\n"
                f"⚠️ Min quantity: {stock['min_quantity']}\n"
                f"🏷️ Price per unit: ₹{stock['price']}\n\n"
                f"📋 Enter quantity:",
                parse_mode="Markdown"
            )
            return ASK_QUANTITY
    return ConversationHandler.END

async def ask_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ Please send a valid number.")
        return ASK_QUANTITY
    info = context.user_data['product_info']
    if qty < info['min_quantity']:
        await update.message.reply_text(f"❌ Quantity below minimum ({info['min_quantity']}). Please enter a higher number.")
        return ASK_QUANTITY
    if qty > info['available_stock']:
        await update.message.reply_text(f"❌ Only {info['available_stock']} codes available for this option.")
        return ASK_QUANTITY
    context.user_data['quantity'] = qty
    total = qty * info['price']
    context.user_data['total_amount'] = total
    order_id = generate_order_id(update.effective_user.id)
    context.user_data['order_id'] = order_id
    qr_url = get_qr()
    if not qr_url:
        await update.message.reply_text("⚠️ QR code not configured. Please contact admin.")
        return ConversationHandler.END
    invoice_text = format_order_invoice(order_id, context.user_data['option_name'], qty, total)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment")]
    ])
    await update.message.reply_photo(photo=qr_url, caption=invoice_text, parse_mode="Markdown", reply_markup=keyboard)
    return ASK_PAYER_NAME

async def verify_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please enter the payer name (the name used for payment):")
    return ASK_PAYER_NAME

async def ask_payer_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payer_name = update.message.text.strip()
    context.user_data['payer_name'] = payer_name
    await update.message.reply_text("📸 Please send the screenshot of your payment (as photo).")
    return ASK_SCREENSHOT

async def ask_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send a photo as screenshot.")
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
    await update.message.reply_text("⏳ Order placed! Waiting for admin approval.")
    admin_text = (
        f"🆕 New Order Pending\n"
        f"🆔 {context.user_data['order_id']}\n"
        f"👤 {update.effective_user.first_name} (@{update.effective_user.username})\n"
        f"📦 {context.user_data['option_name']} x{context.user_data['quantity']}\n"
        f"💰 ₹{context.user_data['total_amount']}\n"
        f"🧾 Payer: {context.user_data['payer_name']}\n"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{context.user_data['order_id']}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"decline_{context.user_data['order_id']}")]
    ])
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_text, reply_markup=keyboard)
    for key in ['buy_type', 'category', 'option_name', 'quantity', 'total_amount', 'order_id', 'payer_name', 'product_info']:
        context.user_data.pop(key, None)
    return ConversationHandler.END

# -------------------- ADMIN HANDLERS --------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Admin command from user {user_id}, ADMIN_USER_ID={ADMIN_USER_ID}")
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Unauthorized. You are not the admin.")
        return
    keyboard = ReplyKeyboardMarkup([
        ["➕ ADD", "📦 STOCK"],
        ["💰 CHANGE PRICES", "📉 SET MIN QUANTITY"],
        ["📢 BROADCAST", "📋 LAST 10 PURCHASES"],
        ["🖼️ UPDATE QR", "👥 ACTIVE USERS"],
        ["🚫 BLOCK", "✅ UNBLOCK"],
        ["🔌 TURN OFF", "🔌 TURN ON"]
    ], resize_keyboard=True)
    await update.message.reply_text("🛠️ **Admin Panel**", parse_mode="Markdown", reply_markup=keyboard)

async def handle_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    text = update.message.text
    if text == "🔌 TURN OFF":
        set_bot_on_off(False)
        await update.message.reply_text("🔴 Bot is now OFF. Users cannot interact.")
    elif text == "🔌 TURN ON":
        set_bot_on_off(True)
        await update.message.reply_text("🟢 Bot is now ON.")
    elif text == "📦 STOCK":
        await update.message.reply_text(format_stock_report(), parse_mode="Markdown")
    elif text == "📋 LAST 10 PURCHASES":
        purchases = get_last_10_purchases()
        await update.message.reply_text(format_last_10(purchases), parse_mode="Markdown")
    elif text == "👥 ACTIVE USERS":
        users = get_all_users()
        await update.message.reply_text(f"👥 Total users who started bot: {len(users)}")
    elif text == "🖼️ UPDATE QR":
        context.user_data['admin_action'] = 'update_qr'
        await update.message.reply_text("📸 Send me the new QR code as a photo:")
    elif text == "➕ ADD":
        context.user_data['admin_action'] = 'add'
        await update.message.reply_text("Send type (voucher / premium):")
    elif text == "💰 CHANGE PRICES":
        context.user_data['admin_action'] = 'price'
        await update.message.reply_text("Send type (voucher / premium):")
    elif text == "📉 SET MIN QUANTITY":
        context.user_data['admin_action'] = 'minqty'
        await update.message.reply_text("Send type (voucher / premium):")
    elif text == "📢 BROADCAST":
        context.user_data['admin_action'] = 'broadcast'
        await update.message.reply_text("📢 Send the message to broadcast:")
    elif text == "🚫 BLOCK":
        context.user_data['admin_action'] = 'block'
        await update.message.reply_text("🚫 Send the username (without @) to block:")
    elif text == "✅ UNBLOCK":
        context.user_data['admin_action'] = 'unblock'
        await update.message.reply_text("✅ Send the username to unblock:")

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    action = context.user_data.get('admin_action')
    if not action:
        return
    text = update.message.text
    if action == 'update_qr':
        if update.message.photo:
            photo = await update.message.photo[-1].get_file()
            file_id = photo.file_id
            update_qr(file_id)
            await update.message.reply_text("✅ QR updated successfully.")
        else:
            await update.message.reply_text("Please send a photo.")
        context.user_data.pop('admin_action')
    elif action == 'add':
        if 'add_step' not in context.user_data:
            context.user_data['add_step'] = 1
            context.user_data['add_data'] = {}
            await update.message.reply_text("Send category (shein/myntra/bigbasket/netflix):")
            return
        step = context.user_data['add_step']
        if step == 1:
            context.user_data['add_data']['type'] = text.lower()
            await update.message.reply_text("Send option name exactly as stored (e.g., '500 Off On 500'):")
            context.user_data['add_step'] = 2
        elif step == 2:
            context.user_data['add_data']['option'] = text
            await update.message.reply_text("Send codes/accounts line by line. Send /done when finished:")
            context.user_data['add_step'] = 3
            context.user_data['add_codes_list'] = []
        elif step == 3:
            if text == '/done':
                if context.user_data['add_data']['type'] == 'premium':
                    full_message = "\n".join(context.user_data['add_codes_list'])
                    add_premium_account('premium', context.user_data['add_data']['type'], context.user_data['add_data']['option'], full_message)
                else:
                    add_codes('voucher', context.user_data['add_data']['type'], context.user_data['add_data']['option'], context.user_data['add_codes_list'])
                await update.message.reply_text("✅ Added successfully.")
                context.user_data.pop('admin_action')
                context.user_data.pop('add_step')
                context.user_data.pop('add_data')
                context.user_data.pop('add_codes_list')
            else:
                context.user_data['add_codes_list'].append(text)
    elif action == 'price':
        parts = text.split()
        if len(parts) < 4:
            await update.message.reply_text("Format: type category option_name price\nExample: voucher shein '500 Off On 500' 99.0")
            return
        type_, cat, opt, price = parts[0], parts[1], ' '.join(parts[2:-1]), float(parts[-1])
        set_price(type_, cat, opt, price)
        await update.message.reply_text("✅ Price updated.")
        context.user_data.pop('admin_action')
    elif action == 'minqty':
        parts = text.split()
        if len(parts) < 4:
            await update.message.reply_text("Format: type category option_name min_qty\nExample: voucher shein '500 Off On 500' 2")
            return
        type_, cat, opt, minq = parts[0], parts[1], ' '.join(parts[2:-1]), int(parts[-1])
        set_min_qty(type_, cat, opt, minq)
        await update.message.reply_text("✅ Min quantity updated.")
        context.user_data.pop('admin_action')
    elif action == 'broadcast':
        users = get_all_users()
        success = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                success += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await update.message.reply_text(f"📢 Broadcast sent to {success} users.")
        context.user_data.pop('admin_action')
    elif action == 'block':
        username = text.strip()
        res = supabase.table('users').select('user_id').eq('username', username).execute()
        if res.data:
            block_user(res.data[0]['user_id'])
            await update.message.reply_text(f"🚫 Blocked @{username}")
        else:
            await update.message.reply_text("User not found.")
        context.user_data.pop('admin_action')
    elif action == 'unblock':
        username = text.strip()
        res = supabase.table('users').select('user_id').eq('username', username).execute()
        if res.data:
            unblock_user(res.data[0]['user_id'])
            await update.message.reply_text(f"✅ Unblocked @{username}")
        else:
            await update.message.reply_text("User not found.")
        context.user_data.pop('admin_action')

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
            remaining_codes = stock['codes'][order['quantity']:]
            supabase.table('stocks').update({
                'codes': remaining_codes,
                'available_stock': len(remaining_codes)
            }).eq('type', order['type']).eq('category', order['category']).eq('option_name', order['option_name']).execute()
            codes_str = "\n".join(codes_to_give)
            update_order_status(order_id, 'accepted', codes_str)
            await context.bot.send_message(
                chat_id=order['user_id'],
                text=f"✅ Your order {order_id} has been accepted!\n\nHere are your codes/account:\n{codes_str}"
            )
            await query.edit_message_text(f"✅ Order {order_id} accepted.")
        else:
            await query.edit_message_text("Order already processed or invalid.")
    elif data.startswith("decline_"):
        order_id = data[8:]
        update_order_status(order_id, 'declined')
        order = get_order_by_id(order_id)
        if order:
            await context.bot.send_message(chat_id=order['user_id'], text=f"❌ Your order {order_id} was declined by admin.")
        await query.edit_message_text(f"❌ Order {order_id} declined.")

# -------------------- FLASK HEALTH CHECK SERVER --------------------
flask_app = Flask('')

@flask_app.route('/')
def health():
    return "Bot is running", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# -------------------- MAIN --------------------
def main():
    # Start Flask health server in background
    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask health server running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^ORD_.*'), handle_recover_order))

    # Buy conversation
    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_callback, pattern="^(buy_|voucher_|opt_|premium_)")],
        states={
            ASK_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_quantity)],
            ASK_PAYER_NAME: [CallbackQueryHandler(verify_payment_callback, pattern="verify_payment"),
                             MessageHandler(filters.TEXT & ~filters.COMMAND, ask_payer_name)],
            ASK_SCREENSHOT: [MessageHandler(filters.PHOTO, ask_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Cancelled."))],
        allow_reentry=True
    )
    app.add_handler(buy_conv)

    # Admin handlers
    app.add_handler(CommandHandler("admin", admin_panel, filters.User(user_id=ADMIN_USER_ID)))
    app.add_handler(MessageHandler(filters.TEXT & filters.User(user_id=ADMIN_USER_ID), handle_admin_actions))
    app.add_handler(MessageHandler(filters.ALL & filters.User(user_id=ADMIN_USER_ID), handle_admin_input))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(accept_|decline_)"))

    logger.info("Bot started polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
