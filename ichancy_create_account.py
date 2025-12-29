# ichancy_create_account.py

import random
import string
import db
from ichancy_api import IChancyAPI

api = IChancyAPI()

# ============================
# توليد لاحقة عشوائية
# ============================
def _random_suffix(length=3):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

# ============================
# توليد اسم مستخدم فريد
# ============================
def generate_username(raw_username: str) -> str:
    base = f"ZEUS_{raw_username}"
    for i in range(6):
        username = base if i == 0 else f"{base}_{_random_suffix()}"
        if not api.check_player_exists(username):
            return username
    raise ValueError("❌ اسم المستخدم غير متاح، جرّب اسمًا آخر")

# ============================
# بدء عملية إنشاء الحساب
# ============================
def start_create_account(bot, call):
    bot.send_message(
        call.message.chat.id,
        "📝 أرسل اسم المستخدم المطلوب (بالإنجليزية فقط، بدون مسافات):"
    )
    bot.register_next_step_handler_by_chat_id(
        call.message.chat.id,
        lambda msg: process_username_step(bot, msg, call.from_user.id)
    )

# ============================
# خطوة إدخال اسم المستخدم
# ============================
def process_username_step(bot, message, telegram_id):
    raw_username = message.text.strip()
    raw_username = ''.join(c for c in raw_username if c.isalnum() or c in ['_', '-'])

    if len(raw_username) < 3:
        bot.send_message(message.chat.id, "❌ الاسم قصير جداً، يجب أن يكون 3 أحرف على الأقل")
        return

    try:
        username = generate_username(raw_username)

        bot.send_message(
            message.chat.id,
            f"✅ الاسم متاح: `{username}`\n\n"
            f"🔐 **الآن أرسل كلمة السر:**\n"
            f"- يجب أن تحتوي على أحرف كبيرة وصغيرة\n"
            f"- يجب أن تحتوي على أرقام\n"
            f"- يجب أن تكون 8 أحرف على الأقل\n\n"
            f"مثال: `Pass1234`",
            parse_mode="Markdown"
        )

        bot.register_next_step_handler_by_chat_id(
            message.chat.id,
            lambda msg: process_password_step(bot, msg, telegram_id, username)
        )

    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ خطأ: {str(e)}\n\nاستخدم /start لإعادة المحاولة"
        )

# ============================
# خطوة إدخال كلمة المرور
# ============================
def process_password_step(bot, message, telegram_id, username):
    password = message.text.strip()

    # التحقق من قوة كلمة المرور
    if len(password) < 8:
        bot.send_message(message.chat.id, "❌ كلمة المرور قصيرة جداً، يجب أن تكون 8 أحرف على الأقل")
        return

    if not any(c.isupper() for c in password) or not any(c.islower() for c in password):
        bot.send_message(message.chat.id, "❌ يجب أن تحتوي كلمة المرور على أحرف كبيرة وصغيرة")
        return

    if not any(c.isdigit() for c in password):
        bot.send_message(message.chat.id, "❌ يجب أن تحتوي كلمة المرور على أرقام")
        return

    try:
        # تحقق من عدم وجود المستخدم مسبقًا
        if api.check_player_exists(username):
            bot.send_message(message.chat.id, "❌ هذا الاسم مستخدم بالفعل، اختر اسمًا آخر")
            return

        # إنشاء الحساب
        status, data, player_id, email_created = api.create_player_with_credentials(username, password)

        if status != 200 or not player_id:
            error_msg = "فشل إنشاء الحساب"
            if isinstance(data, dict):
                notes = data.get("notification", [])
                if notes:
                    error_msg = notes[0].get("content", error_msg)
            raise ValueError(error_msg)

        # حفظ البيانات في MongoDB
        db.update_player_info(
            telegram_id,
            player_id,
            username,
            email_created,
            password
        )

        # رسالة النجاح
        bot.send_message(
            message.chat.id,
            f"""
🎉 **تم إنشاء الحساب بنجاح!**

👤 **اسم المستخدم:** `{username}`
🔐 **كلمة المرور:** `{password}`
📧 **البريد الإلكتروني:** `{email_created}`
🆔 **معرف اللاعب:** `{player_id}`

🔗 **رابط تسجيل الدخول:**
https://www.ichancy.com/login

⚠️ **احفظ هذه البيانات في مكان آمن!**
            """,
            parse_mode="Markdown"
        )

    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ **فشل إنشاء الحساب:**\n\n{str(e)}\n\n"
            f"يرجى المحاولة لاحقاً.",
            parse_mode="Markdown"
        )
