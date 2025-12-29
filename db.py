from pymongo import MongoClient
from pymongo.errors import OperationFailure
from datetime import datetime
import os
from bson.objectid import ObjectId

# ============================
# الاتصال بقاعدة البيانات
# ============================

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("🔴 MONGODB_URI غير موجود في متغيرات البيئة!")

client = MongoClient(MONGODB_URI)
db = client["ichancy_bot"]

# ============================
# مجموعات البيانات
# ============================

users = db.users
transactions = db.transactions
referrals = db.referrals

# ============================
# إنشاء الفهارس (بدون كسر الإقلاع)
# ============================

def ensure_indexes():
    try:
        users.create_index("telegram_id", unique=True)
    except OperationFailure as e:
        print(f"⚠️ index telegram_id skipped: {e}")

    try:
        users.create_index("player_id", unique=True)
    except OperationFailure as e:
        print(f"⚠️ index player_id skipped: {e}")

    try:
        transactions.create_index("telegram_id")
    except OperationFailure as e:
        print(f"⚠️ index transactions.telegram_id skipped: {e}")

    try:
        transactions.create_index("created_at")
    except OperationFailure as e:
        print(f"⚠️ index transactions.created_at skipped: {e}")

    try:
        referrals.create_index("referrer_id")
    except OperationFailure as e:
        print(f"⚠️ index referrals.referrer_id skipped: {e}")

    try:
        referrals.create_index("referred_id", unique=True)
    except OperationFailure as e:
        print(f"⚠️ index referrals.referred_id skipped: {e}")

# تنفيذ آمن
ensure_indexes()

# ============================
# الدوال الأساسية للمستخدمين
# ============================

def get_user(telegram_id):
    return users.find_one({"telegram_id": telegram_id})

def create_user(telegram_id, username, first_name, last_name):
    user_data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "balance": 0.0,
        "referral_balance": 0.0,
        "total_earned": 0.0,
        "total_withdrawn": 0.0,
        "referral_link": f"https://t.me/{os.getenv('BOT_USERNAME', '')}?start={telegram_id}",
        "referrals_count": 0,
        "active_referrals_count": 0,
        "player_id": None,
        "player_username": None,
        "player_email": None,
        "player_password": None,
        "accepted_terms": False,
        "joined_channel": False,
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        users.insert_one(user_data)
        return True
    except Exception as e:
        print(f"❌ خطأ في إنشاء المستخدم: {e}")
        return False

def update_user(telegram_id, update_data):
    update_data["updated_at"] = datetime.utcnow()
    return users.update_one(
        {"telegram_id": telegram_id},
        {"$set": update_data}
    )

def accept_terms(telegram_id):
    return update_user(telegram_id, {"accepted_terms": True})

def mark_channel_joined(telegram_id):
    return update_user(telegram_id, {"joined_channel": True})

def update_player_info(telegram_id, player_id, player_username, player_email, player_password):
    return update_user(telegram_id, {
        "player_id": player_id,
        "player_username": player_username,
        "player_email": player_email,
        "player_password": player_password
    })

def update_balance(telegram_id, amount, is_withdrawal=False):
    user = get_user(telegram_id)
    if not user:
        return False
    
    new_balance = user["balance"] + amount
    
    transaction_type = "withdrawal" if is_withdrawal else "deposit"
    status = "completed" if amount > 0 else "pending"
    
    log_transaction(
        telegram_id=telegram_id,
        player_id=user.get("player_id"),
        amount=abs(amount),
        ttype=transaction_type,
        status=status
    )
    
    update_data = {"balance": new_balance}
    
    if amount > 0:
        update_data["total_earned"] = user.get("total_earned", 0) + amount
    elif is_withdrawal and amount < 0:
        update_data["total_withdrawn"] = user.get("total_withdrawn", 0) + abs(amount)
    
    return update_user(telegram_id, update_data)

# ============================
# إدارة الإحالات
# ============================

def add_referral(referrer_id, referred_id):
    referral_data = {
        "referrer_id": referrer_id,
        "referred_id": referred_id,
        "is_active": False,
        "has_deposited": False,
        "referral_reward": 0.0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        referrals.insert_one(referral_data)
        users.update_one(
            {"telegram_id": referrer_id},
            {"$inc": {"referrals_count": 1}}
        )
        return True
    except Exception as e:
        print(f"❌ خطأ في إضافة الإحالة: {e}")
        return False

def activate_referral(referred_id):
    referral = referrals.find_one({"referred_id": referred_id})
    if not referral:
        return False
    
    referrals.update_one(
        {"referred_id": referred_id},
        {"$set": {"is_active": True, "has_deposited": True}}
    )
    
    users.update_one(
        {"telegram_id": referral["referrer_id"]},
        {"$inc": {"active_referrals_count": 1}}
    )
    
    reward_amount = float(os.getenv("REFERRAL_REWARD", "5.0"))
    users.update_one(
        {"telegram_id": referral["referrer_id"]},
        {"$inc": {"referral_balance": reward_amount}}
    )
    
    referrals.update_one(
        {"referred_id": referred_id},
        {"$set": {"referral_reward": reward_amount}}
    )
    
    return True

# ============================
# إدارة المعاملات
# ============================

def log_transaction(telegram_id, player_id, amount, ttype, status="pending"):
    return transactions.insert_one({
        "telegram_id": telegram_id,
        "player_id": player_id,
        "type": ttype,
        "amount": amount,
        "status": status,
        "created_at": datetime.utcnow()
    })

def get_user_transactions(telegram_id, limit=10, transaction_type=None):
    query = {"telegram_id": telegram_id}
    if transaction_type:
        query["type"] = transaction_type
    return list(transactions.find(query).sort("created_at", -1).limit(limit))

def get_user_referrals(telegram_id):
    return list(referrals.find({"referrer_id": telegram_id}))

def get_user_stats(telegram_id):
    user = get_user(telegram_id)
    if not user:
        return None
    
    referrals_list = get_user_referrals(telegram_id)
    transactions_list = get_user_transactions(telegram_id, limit=20)
    
    return {
        "user": user,
        "referrals": referrals_list,
        "transactions": transactions_list,
        "stats": {
            "total_referrals": len(referrals_list),
            "active_referrals": len([r for r in referrals_list if r.get("is_active")]),
            "total_referral_rewards": sum(r.get("referral_reward", 0) for r in referrals_list),
            "total_transactions": len(transactions_list)
        }
    }
