import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


def _secret(name: str) -> Optional[str]:
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
        supabase_url=normalize_supabase_url(_secret("SUPABASE_URL")),
        supabase_service_role_key=_secret("SUPABASE_SERVICE_ROLE_KEY"),
    )


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str
    owner_user_id: str

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id and self.owner_user_id)


@lru_cache(maxsize=1)
def get_telegram_settings() -> TelegramSettings:
    return TelegramSettings(
        bot_token=str(_secret("TELEGRAM_BOT_TOKEN") or ""),
        chat_id=str(_secret("TELEGRAM_CHAT_ID") or ""),
        owner_user_id=str(_secret("ALERT_OWNER_USER_ID") or ""),
    )
