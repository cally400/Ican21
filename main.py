from ichancy_api import IChancyAPI
import ichancy_create_account as ichancy_create
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request
import os
import db

# =========================
# تهيئة API
# =========================
api = IChancyAPI()

# =========================
# تهيئة البوت
# =========================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN غير موجود")

bot = telebot.TeleBot(TOKEN)

# =========================
# إعدادات القناة
# =========================
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK")

# =========================
# التحقق من الاشتراك
# =========================
def check_channel_membership(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# =========================
# القائمة الرئيسية
# =========================
def build_main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🎮 I Chancy", callback_data="ichancy"))
    kb.row(
        InlineKeyboardButton("💸 سحب رصيد", callback_data="withdraw"),
        InlineKeyboardButton("💰 شحن رصيد", callback_data="deposit")
    )
    kb.add(InlineKeyboardButton("👥 نظام الإحالات", callback_data="referrals"))
    kb.row(
        InlineKeyboardButton("🎁 كود هدية", callback_data="gift_code"),
        InlineKeyboardButton("💝 اهداء رصيد", callback_data="gift_balance")
    )
    kb.row(
        InlineKeyboardButton("📞 تواصل معنا", callback_data="contact"),
        InlineKeyboardButton("✉️ رسالة للادمن", callback_data="admin_msg")
    )
    kb.row(
        InlineKeyboardButton("📚 الشروحات", callback_data="tutorials"),
        InlineKeyboardButton("📜 السجل", callback_data="transactions")
    )
    kb.row(
        InlineKeyboardButton("📱 IChancy APK", callback_data="apk"),
        InlineKeyboardButton("🛡 VPN", callback_data="vpn")
    )
    kb.add(InlineKeyboardButton("📄 الشروط والاحكام", callback_data="terms"))
    kb.add(InlineKeyboardButton("🎰 الجاكبوت", callback_data="jackpot"))
    return kb

def show_main_menu(message):
    bot.send_message(
        message.chat.id,
        "🏠 **القائمة الرئيسية**",
        reply_markup=build_main_menu(),
        parse_mode="Markdown"
    )

# =========================
# /start
# =========================
@bot.message_handler(commands=["start"])
def send_welcome(message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    referral_id = None
    if len(message.text.split()) > 1:
        try:
            referral_id = int(message.text.split()[1])
        except:
            pass

    if not user:
        if not check_channel_membership(CHANNEL_ID, user_id):
            show_channel_requirement(message, referral_id)
            return
        show_terms(message, user_id, referral_id)
        return

    if not user.get("accepted_terms"):
        show_terms(message, user_id)
        return

    if not user.get("joined_channel"):
        if not check_channel_membership(CHANNEL_ID, user_id):
            show_channel_requirement(message)
            return
        db.mark_channel_joined(user_id)

    show_main_menu(message)

# =========================
# الاشتراك بالقناة
# =========================
def show_channel_requirement(message, referral_id=None):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🔗 انضم للقناة", url=CHANNEL_INVITE_LINK),
        InlineKeyboardButton("✅ تحقق", callback_data=f"check_join:{referral_id}")
    )
    bot.send_message(message.chat.id, "📢 يجب الاشتراك بالقناة أولاً", reply_markup=kb)

# =========================
# الشروط
# =========================
def show_terms(message, user_id, referral_id=None):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ أوافق", callback_data=f"accept_terms:{user_id}:{referral_id}"),
        InlineKeyboardButton("❌ أرفض", callback_data=f"reject_terms:{user_id}")
    )
    bot.send_message(
        message.chat.id,
        "📜 **شروط الخدمة**\n\n- باستخدامك للبوت فأنت توافق على الشروط",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# =========================
# تحقق الاشتراك
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("check_join"))
def handle_check_join(call):
    referral_id = call.data.split(":")[1]
    referral_id = int(referral_id) if referral_id.isdigit() else None
    if check_channel_membership(CHANNEL_ID, call.from_user.id):
        db.mark_channel_joined(call.from_user.id)
        show_terms(call.message, call.from_user.id, referral_id)
        bot.answer_callback_query(call.id, "تم التحقق ✅")
    else:
        bot.answer_callback_query(call.id, "❌ غير مشترك")

# =========================
# قبول الشروط
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("accept_terms"))
def handle_accept_terms(call):
    parts = call.data.split(":")
    user_id = int(parts[1])
    referral_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    if call.from_user.id != user_id:
        return

    user = db.get_user(user_id)
    is_new_user = False
    if not user:
        db.create_user(
            telegram_id=user_id,
            username=call.from_user.username,
            first_name=call.from_user.first_name,
            last_name=call.from_user.last_name
        )
        is_new_user = True
        if referral_id and referral_id != user_id:
            db.add_referral(referral_id, user_id)

    db.accept_terms(user_id)
    bot.edit_message_text("✅ تم قبول الشروط", call.message.chat.id, call.message.message_id)
    if is_new_user:
        show_main_menu(call.message)

# =========================
# رفض الشروط
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("reject_terms"))
def handle_reject_terms(call):
    bot.send_message(call.message.chat.id, "❌ لا يمكن استخدام البوت بدون قبول الشروط")

# =========================
# I Chancy
# =========================
@bot.callback_query_handler(func=lambda c: c.data == "ichancy")
def handle_ichancy(call):
    user = db.get_user(call.from_user.id)
    if not user:
        bot.answer_callback_query(call.id, "❌ المستخدم غير موجود")
        return
    has_account = all([
        user.get("player_id"),
        user.get("player_email"),
        user.get("player_username"),
        user.get("player_password")
    ])
    keyboard = InlineKeyboardMarkup(row_width=1)
    if has_account:
        keyboard.add(
            InlineKeyboardButton("💰 تعبئة رصيد في الموقع", callback_data="ichancy_deposit"),
            InlineKeyboardButton("💸 سحب رصيد من الموقع", callback_data="ichancy_withdraw")
        )
        text = "🎮 **I Chancy**\n\n✅ تم العثور على حسابك في الموقع\n\nاختر العملية المطلوبة:"
    else:
        keyboard.add(
            InlineKeyboardButton("➕ إنشاء حساب iChancy", callback_data="ichancy_create")
        )
        text = "🎮 **I Chancy**\n\n❌ لا يوجد لديك حساب في الموقع\n\nاضغط على إنشاء حساب للمتابعة:"
    keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    bot.edit_message_text(
        text=text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def handle_back_main(call):
    bot.edit_message_text(
        "🏠 **القائمة الرئيسية**",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=build_main_menu(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

# =========================
# إنشاء حساب iChancy
# =========================
@bot.callback_query_handler(func=lambda c: c.data == "ichancy_create")
def handle_ichancy_create(call):
    ichancy_create.start_create_account(bot, call)

# =========================
# إعدادات الـ Webhook / Flask
# =========================
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # مثال: https://your-app.up.railway.app
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
PORT = int(os.getenv("PORT", 8080))

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL غير موجود في متغيرات البيئة")

app = Flask(__name__)

# Health check
@app.route("/", methods=["GET"])
def index():
    return "IChancy Telegram Bot is running.", 200

# Webhook endpoint
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    return "Unsupported Media Type", 415

@app.before_first_request
def set_webhook():
    bot.remove_webhook()
    full_url = WEBHOOK_URL + WEBHOOK_PATH
    bot.set_webhook(url=full_url)
    print("Webhook set:", full_url)

if __name__ == "__main__":
    print("Bot is running with webhook on port", PORT)
    app.run(host="0.0.0.0", port=PORT)
