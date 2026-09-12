import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


def get_secret(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None


def normalize_supabase_url(value: Optional[str]) -> Optional[str]:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return None
    if "://" not in url and "/" not in url:
        url = f"https://{url}.supabase.co"
    return url


@dataclass(frozen=True)
class AccountSettings:
    supabase_url: Optional[str]
    supabase_service_role_key: Optional[str]

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache(maxsize=1)
def get_account_settings() -> AccountSettings:
    return AccountSettings(
        supabase_url=normalize_supabase_url(get_secret("SUPABASE_URL")),
        supabase_service_role_key=get_secret("SUPABASE_SERVICE_ROLE_KEY"),
    )


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str
    owner_user_id: str

    @property
    def ops_configured(self) -> bool:
        token = (self.bot_token or "").strip()
        chat = (self.chat_id or "").strip()
        if not token or not chat:
            return False
        return not (_is_secret_placeholder(token) or _is_secret_placeholder(chat))

    @property
    def configured(self) -> bool:
        owner = (self.owner_user_id or "").strip()
        return self.ops_configured and bool(owner) and not _is_secret_placeholder(owner)


def _is_secret_placeholder(value: str) -> bool:
    low = (value or "").strip().lower()
    return any(
        token in low
        for token in (
            "replace-with",
            "your-bot-token",
            "your-chat-id",
            "your-stock-analyzer",
        )
    )


@lru_cache(maxsize=1)
def get_telegram_settings() -> TelegramSettings:
    return TelegramSettings(
        bot_token=str(get_secret("TELEGRAM_BOT_TOKEN") or ""),
        chat_id=str(get_secret("TELEGRAM_CHAT_ID") or ""),
        owner_user_id=str(get_secret("ALERT_OWNER_USER_ID") or ""),
    )
