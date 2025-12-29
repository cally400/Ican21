import os
import json
import random
import string
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Tuple, Optional, Dict, Any, List

import cloudscraper
import redis


class IChancyAPI:
    """
    IChancy API client with:
    - Lazy login
    - Redis-backed session (cookies + expiry)
    - Auto-healing on failure / captcha / 403
    - Suitable for 24/7 bots on Railway
    """

    def __init__(self):
        self._setup_logging()
        self._load_config()
        self._init_redis()
        self.scraper = None
        self.is_logged_in = False

    # =========================
    # إعدادات عامة
    # =========================
    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s"
        )
        self.logger = logging.getLogger("IChancyAPI")

    def _load_config(self):
        # Agent credentials
        self.AGENT_USERNAME = os.getenv("AGENT_USERNAME", "tsa_robert@tsa.com")
        self.AGENT_PASSWORD = os.getenv("AGENT_PASSWORD", "K041@051kkk")
        self.PARENT_ID = os.getenv("PARENT_ID", "2307909")

        # IChancy endpoints
        self.ORIGIN = "https://agents.ichancy.com"
        self.ENDPOINTS = {
            "signin": "/global/api/User/signIn",
            "create": "/global/api/Player/registerPlayer",
            "statistics": "/global/api/Statistics/getPlayersStatisticsPro",
            "deposit": "/global/api/Player/depositToPlayer",
            "withdraw": "/global/api/Player/withdrawFromPlayer",
            "balance": "/global/api/Player/getPlayerBalanceById",
        }

        self.USER_AGENT = (
            "Mozilla/5.0 (Linux; Android 6.0.1; SM-G532F) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/106.0.5249.126 Mobile Safari/537.36"
        )
        self.REFERER = self.ORIGIN + "/dashboard"

        # Session timings
        self.SESSION_DURATION_MIN = int(os.getenv("ICHANCY_SESSION_MIN", "30"))
        self.MAX_SESSION_AGE_HOURS = int(os.getenv("ICHANCY_MAX_SESSION_HOURS", "2"))

        # Redis keys
        self.REDIS_COOKIES_KEY = "ichancy:cookies"
        self.REDIS_SESSION_EXPIRY_KEY = "ichancy:session_expiry"
        self.REDIS_LAST_LOGIN_KEY = "ichancy:last_login"

    def _init_redis(self):
        """
        Connect to Redis using Railway env vars:
        REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
        """
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", None)

        self.redis = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,  # store strings not bytes
        )
        try:
            self.redis.ping()
            self.logger.info("✅ Connected to Redis successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Redis: {e}")
            raise

    def _init_scraper(self):
        """
        Initialize cloudscraper and load cookies from Redis if available.
        """
        self.scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            }
        )

        cookies_json = self.redis.get(self.REDIS_COOKIES_KEY)
        if cookies_json:
            try:
                cookies = json.loads(cookies_json)
                self.scraper.cookies.update(cookies)
                if self._is_session_valid():
                    self.is_logged_in = True
                    self.logger.info("✅ Restored valid session from Redis")
                    return
                else:
                    self.logger.info("ℹ️ Redis session expired, need relogin")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to load cookies from Redis: {e}")

        # If here: no valid session
        self.is_logged_in = False

    # =========================
    # أدوات الجلسة / Redis
    # =========================
    def _is_session_valid(self) -> bool:
        """
        Check if session is still valid based on expiry & last login.
        """
        session_expiry_str = self.redis.get(self.REDIS_SESSION_EXPIRY_KEY)
        last_login_str = self.redis.get(self.REDIS_LAST_LOGIN_KEY)

        if not session_expiry_str or not last_login_str:
            return False

        try:
            session_expiry = datetime.fromisoformat(session_expiry_str)
            last_login_time = datetime.fromisoformat(last_login_str)
        except Exception:
            return False

        now = datetime.utcnow()
        session_duration = timedelta(minutes=self.SESSION_DURATION_MIN)
        max_session_age = timedelta(hours=self.MAX_SESSION_AGE_HOURS)

        time_since_login = now - last_login_time

        return (now < session_expiry) and (time_since_login < max_session_age)

    def _save_session_to_redis(self):
        """
        Save cookies & timestamps to Redis.
        """
        try:
            cookies = dict(self.scraper.cookies)
            cookies_json = json.dumps(cookies)
            now = datetime.utcnow()
            session_expiry = now + timedelta(minutes=self.SESSION_DURATION_MIN)

            self.redis.set(self.REDIS_COOKIES_KEY, cookies_json)
            self.redis.set(self.REDIS_SESSION_EXPIRY_KEY, session_expiry.isoformat())
            self.redis.set(self.REDIS_LAST_LOGIN_KEY, now.isoformat())

            self.logger.info(
                f"💾 Session saved to Redis (expires at {session_expiry.isoformat()})"
            )
        except Exception as e:
            self.logger.error(f"❌ Failed to save session to Redis: {e}")

    def _clear_session_in_redis(self):
        """
        Clear session data from Redis.
        """
        self.redis.delete(self.REDIS_COOKIES_KEY)
        self.redis.delete(self.REDIS_SESSION_EXPIRY_KEY)
        self.redis.delete(self.REDIS_LAST_LOGIN_KEY)
        self.is_logged_in = False
        self.logger.info("🧹 Cleared session from Redis")

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": self.USER_AGENT,
            "Origin": self.ORIGIN,
            "Referer": self.REFERER,
        }

    # =========================
    # كابتشا / Cloudflare
    # =========================
    def _check_captcha(self, response_text: str) -> bool:
        text = response_text.lower()
        if "captcha" in text or "cloudflare" in text:
            self.logger.warning("⚠️ Possible captcha / Cloudflare challenge detected")
            return True
        return False

    # =========================
    # تسجيل الدخول وإعادة المحاولة
    # =========================
    def login(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Login to agent and save session in Redis.
        """
        if not self.scraper:
            self._init_scraper()

        payload = {
            "username": self.AGENT_USERNAME,
            "password": self.AGENT_PASSWORD,
        }

        try:
            resp = self.scraper.post(
                self.ORIGIN + self.ENDPOINTS["signin"],
                json=payload,
                headers=self._get_headers(),
            )

            if self._check_captcha(resp.text):
                self.logger.warning("❌ Captcha detected during login")
                return False, {"error": "captcha_detected"}

            try:
                data = resp.json()
            except Exception:
                self.logger.error("❌ Failed to parse JSON during login")
                return False, {"error": "invalid_json"}

            if data.get("result", False):
                # Save session in Redis
                self._save_session_to_redis()
                self.is_logged_in = True
                self.logger.info("✅ Login successful")
                return True, data

            error_msg = (
                data.get("notification", [{}])[0].get("content", "Login failed")
            )
            self.logger.error(f"❌ Login failed: {error_msg}")
            return False, data

        except Exception as e:
            self.logger.error(f"❌ Exception during login: {e}")
            return False, {"error": str(e)}

    def ensure_login(self) -> bool:
        """
        Ensure we have a valid session.
        """
        if not self.scraper:
            self._init_scraper()

        if self.is_logged_in and self._is_session_valid():
            return True

        self.logger.info("🔄 Session invalid or missing, logging in...")
        success, data = self.login()
        if not success:
            error_msg = data.get(
                "error", data.get("notification", [{}])[0].get("content", "Login failed")
            )
            raise RuntimeError(f"❌ Failed to login: {error_msg}")
        return True

    @staticmethod
    def with_retry(func):
        """
        Decorator to:
        - ensure login
        - retry once on failure / captcha / 403
        """

        @wraps(func)
        def wrapper(self: "IChancyAPI", *args, **kwargs):
            # First attempt
            self.ensure_login()
            result = func(self, *args, **kwargs)

            if result is None:
                self.logger.warning(f"⚠️ {func.__name__} returned None, retrying...")
                self._clear_session_in_redis()
                self.ensure_login()
                return func(self, *args, **kwargs)

            # Expect first element to be HTTP status
            status = result[0] if isinstance(result, tuple) and len(result) > 0 else 0
            data = result[1] if isinstance(result, tuple) and len(result) > 1 else {}

            # Detect failure (non-200, result=False, captcha)
            data_str = str(data).lower()
            captcha_like = "captcha" in data_str or "cloudflare" in data_str

            if status != 200 or (isinstance(data, dict) and not data.get("result", True)) or captcha_like:
                self.logger.warning(
                    f"⚠️ {func.__name__} failed (status={status}), retrying with new login..."
                )
                self._clear_session_in_redis()
                self.ensure_login()
                return func(self, *args, **kwargs)

            return result

        return wrapper

    # =========================
    # أدوات مساعدة
    # =========================
    @staticmethod
    def _generate_random_credentials() -> Tuple[str, str]:
        login = "u" + "".join(
            random.choice(string.ascii_lowercase + string.digits) for _ in range(7)
        )
        pwd = "".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(10)
        )
        return login, pwd

    # =========================
    # دوال الـ API
    # =========================
    @with_retry
    def create_player(self) -> Tuple[int, Dict[str, Any], str, str, Optional[str]]:
        """
        Create random player.
        Returns: (status_code, data, login, password, player_id)
        """
        login, pwd = self._generate_random_credentials()
        email = f"{login}@example.com"

        payload = {
            "player": {
                "email": email,
                "password": pwd,
                "parentId": self.PARENT_ID,
                "login": login,
            }
        }

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["create"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
        except Exception:
            data = {}

        player_id = self.get_player_id_by_login(login)
        return resp.status_code, data, login, pwd, player_id

    @with_retry
    def get_player_id_by_login(self, login: str) -> Optional[str]:
        payload = {"page": 1, "pageSize": 100, "filter": {"login": login}}

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["statistics"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
            records = data.get("result", {}).get("records", [])
            for record in records:
                if record.get("username") == login:
                    return record.get("playerId")
        except Exception:
            pass
        return None

    @with_retry
    def deposit_to_player(self, player_id: str, amount: float) -> Tuple[int, Dict]:
        payload = {
            "amount": amount,
            "comment": None,
            "playerId": player_id,
            "currencyCode": "NSP",
            "currency": "NSP",
            "moneyStatus": 5,
        }

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["deposit"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.status_code, data

    @with_retry
    def withdraw_from_player(self, player_id: str, amount: float) -> Tuple[int, Dict]:
        payload = {
            "amount": amount,
            "comment": None,
            "playerId": player_id,
            "currencyCode": "NSP",
            "currency": "NSP",
            "moneyStatus": 5,
        }

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["withdraw"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.status_code, data

    @with_retry
    def get_player_balance(self, player_id: str) -> Tuple[int, Dict, float]:
        """
        Returns: (status_code, full_json, balance: float)
        """
        payload = {"playerId": str(player_id)}

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["balance"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
        except Exception:
            return resp.status_code, {}, 0.0

        results = data.get("result", [])
        balance = (
            results[0].get("balance", 0) if isinstance(results, list) and results else 0
        )
        return resp.status_code, data, float(balance)

    @with_retry
    def create_player_with_credentials(
        self, login: str, pwd: str
    ) -> Tuple[int, Dict[str, Any], Optional[str], str]:
        """
        Returns: (status_code, data, player_id, email)
        """
        email = f"{login}@TSA.com"

        # Ensure email is unique
        while self.check_email_exists(email):
            random_suffix = random.randint(1000, 9999)
            email = f"{login}_{random_suffix}@TSA.com"

        payload = {
            "player": {
                "email": email,
                "password": pwd,
                "parentId": self.PARENT_ID,
                "login": login,
            }
        }

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["create"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
        except Exception:
            data = {}

        player_id = self.get_player_id_by_login(login)
        return resp.status_code, data, player_id, email

    def check_email_exists(self, email: str) -> bool:
        payload = {"page": 1, "pageSize": 100, "filter": {"email": email}}

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["statistics"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
            records = data.get("result", {}).get("records", [])
            for record in records:
                if record.get("email") == email:
                    return True
        except Exception:
            pass
        return False

    @with_retry
    def check_player_exists(self, login: str) -> bool:
        payload = {"page": 1, "pageSize": 100, "filter": {"login": login}}

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["statistics"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
            records = data.get("result", {}).get("records", [])
            for record in records:
                if record.get("username") == login:
                    return True
        except Exception:
            pass
        return False

    @with_retry
    def get_all_players(self) -> List[Dict[str, Any]]:
        payload = {"page": 1, "pageSize": 100, "filter": {}}

        resp = self.scraper.post(
            self.ORIGIN + self.ENDPOINTS["statistics"],
            json=payload,
            headers=self._get_headers(),
        )

        try:
            data = resp.json()
            return data.get("result", {}).get("records", [])
        except Exception:
            return []
