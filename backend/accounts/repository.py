from __future__ import annotations

import hashlib
import copy
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

import requests

from accounts.models import AlertEvent, AlertRule, SavedPlanOutcome, SavedPlanVersion, User, UserPreferences


def normalize_username(username: str) -> str:
    return username.strip().casefold()


class AccountRepository(Protocol):
    def register(self, username: str, pin: str) -> User: ...

    def authenticate(self, username: str, pin: str) -> Tuple[Optional[User], str]: ...

    def create_session(self, user_id: str) -> str: ...

    def get_session_user(self, token: str) -> Optional[User]: ...

    def revoke_session(self, token: str) -> None: ...

    def get_preferences(self, user_id: str) -> UserPreferences: ...

    def save_preferences(self, user_id: str, preferences: UserPreferences) -> None: ...

    def list_favorites(self, user_id: str) -> List[str]: ...

    def add_favorite(self, user_id: str, ticker: str) -> None: ...

    def remove_favorite(self, user_id: str, ticker: str) -> None: ...

    def get_active_plan(self, user_id: str, ticker: str) -> Optional[SavedPlanVersion]: ...

    def save_plan(
        self, user_id: str, ticker: str, plan_data: Dict[str, Any], analysis_timestamp: str,
    ) -> SavedPlanVersion: ...

    def list_plan_versions(self, user_id: str, ticker: str) -> List[SavedPlanVersion]: ...

    def list_plan_outcomes(self, user_id: str, plan_id: str) -> List[SavedPlanOutcome]: ...

    def record_plan_outcome(
        self, user_id: str, plan_id: str, plan_version: int, outcome_data: Dict[str, Any],
    ) -> SavedPlanOutcome: ...

    def replace_alert_rules(
        self, user_id: str, plan_id: str, plan_version: int,
        rules: Sequence[Tuple[str, Dict[str, Any]]],
    ) -> List[AlertRule]: ...

    def list_alert_rules(self, user_id: str, plan_id: str) -> List[AlertRule]: ...

    def list_alert_events(self, user_id: str, unread_only: bool = False) -> List[AlertEvent]: ...

    def mark_alert_event_read(self, user_id: str, event_id: str) -> None: ...


class UsernameTakenError(ValueError):
    pass


class AccountStorageError(RuntimeError):
    def __init__(self, code: str, status_code: Optional[int] = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class InMemoryAccountRepository:
    """Test/local repository with the same ownership rules as the production adapter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: Dict[str, Dict[str, str]] = {}
        self._sessions: Dict[str, str] = {}
        self._preferences: Dict[str, UserPreferences] = {}
        self._favorites: Dict[str, List[str]] = {}
        self._plans: Dict[Tuple[str, str], List[SavedPlanVersion]] = {}
        self._alerts: Dict[Tuple[str, str], List[AlertRule]] = {}
        self._outcomes: Dict[Tuple[str, str, int, int, int], SavedPlanOutcome] = {}
        self._alert_events: Dict[str, Tuple[str, AlertEvent]] = {}

    def register(self, username: str, pin: str) -> User:
        normalized = normalize_username(username)
        if not normalized:
            raise ValueError("username_required")
        with self._lock:
            if normalized in self._users:
                raise UsernameTakenError("username_taken")
            user = User(id=str(uuid.uuid4()), username=username.strip())
            self._users[normalized] = {"id": user.id, "username": user.username, "pin": pin}
            return user

    def authenticate(self, username: str, pin: str) -> Tuple[Optional[User], str]:
        with self._lock:
            row = self._users.get(normalize_username(username))
            if row is None:
                return None, "username_not_found"
            if row["pin"] != pin:
                return None, "incorrect_pin"
            return User(id=row["id"], username=row["username"]), "ok"

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = user_id
        return token

    def get_session_user(self, token: str) -> Optional[User]:
        with self._lock:
            user_id = self._sessions.get(token)
            if not user_id:
                return None
            for row in self._users.values():
                if row["id"] == user_id:
                    return User(id=row["id"], username=row["username"])
        return None

    def revoke_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def get_preferences(self, user_id: str) -> UserPreferences:
        with self._lock:
            return self._preferences.get(user_id, UserPreferences())

    def save_preferences(self, user_id: str, preferences: UserPreferences) -> None:
        with self._lock:
            self._preferences[user_id] = preferences

    def list_favorites(self, user_id: str) -> List[str]:
        with self._lock:
            return list(self._favorites.get(user_id, []))

    def add_favorite(self, user_id: str, ticker: str) -> None:
        symbol = ticker.strip().upper()
        if not symbol:
            return
        with self._lock:
            favorites = self._favorites.setdefault(user_id, [])
            if symbol not in favorites:
                favorites.append(symbol)

    def remove_favorite(self, user_id: str, ticker: str) -> None:
        with self._lock:
            favorites = self._favorites.get(user_id, [])
            symbol = ticker.strip().upper()
            if symbol in favorites:
                favorites.remove(symbol)

    def get_active_plan(self, user_id: str, ticker: str) -> Optional[SavedPlanVersion]:
        with self._lock:
            versions = self._plans.get((user_id, ticker.strip().upper()), [])
            return versions[-1] if versions else None

    def save_plan(
        self, user_id: str, ticker: str, plan_data: Dict[str, Any], analysis_timestamp: str,
    ) -> SavedPlanVersion:
        symbol = ticker.strip().upper()
        with self._lock:
            versions = self._plans.setdefault((user_id, symbol), [])
            plan_id = versions[0].plan_id if versions else str(uuid.uuid4())
            saved = SavedPlanVersion(
                plan_id=plan_id, ticker=symbol, version=len(versions) + 1,
                plan_data=copy.deepcopy(plan_data), analysis_timestamp=analysis_timestamp,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            versions.append(saved)
            self._alerts.pop((user_id, plan_id), None)
            return saved

    def list_plan_versions(self, user_id: str, ticker: str) -> List[SavedPlanVersion]:
        with self._lock:
            return list(reversed(self._plans.get((user_id, ticker.strip().upper()), [])))

    def list_plan_outcomes(self, user_id: str, plan_id: str) -> List[SavedPlanOutcome]:
        with self._lock:
            values = [
                value for key, value in self._outcomes.items()
                if key[0] == user_id and key[1] == plan_id
            ]
            return sorted(values, key=lambda value: (-value.plan_version, value.horizon_days))

    def record_plan_outcome(
        self, user_id: str, plan_id: str, plan_version: int, outcome_data: Dict[str, Any],
    ) -> SavedPlanOutcome:
        horizon = int(outcome_data["horizon_days"])
        calculation_version = int(outcome_data.get("calculation_version", 1))
        if horizon not in (5, 20, 60):
            raise ValueError("invalid_outcome_horizon")
        with self._lock:
            owned = any(
                any(version.plan_id == plan_id and version.version == plan_version for version in versions)
                for (owner_id, _), versions in self._plans.items() if owner_id == user_id
            )
            if not owned:
                raise ValueError("plan_not_found")
            key = (user_id, plan_id, plan_version, horizon, calculation_version)
            if key not in self._outcomes:
                self._outcomes[key] = SavedPlanOutcome(
                    id=str(uuid.uuid4()), saved_plan_id=plan_id, plan_version=plan_version,
                    horizon_days=horizon, calculation_version=calculation_version,
                    outcome_data=copy.deepcopy(outcome_data),
                )
            return copy.deepcopy(self._outcomes[key])

    def replace_alert_rules(
        self, user_id: str, plan_id: str, plan_version: int,
        rules: Sequence[Tuple[str, Dict[str, Any]]],
    ) -> List[AlertRule]:
        with self._lock:
            owned = any(
                any(version.plan_id == plan_id and version.version == plan_version for version in versions)
                for (owner_id, _), versions in self._plans.items() if owner_id == user_id
            )
            if not owned:
                raise ValueError("plan_not_found")
            saved = [
                AlertRule(
                    id=str(uuid.uuid4()), saved_plan_id=plan_id, plan_version=plan_version,
                    event_type=event_type, rule_data=rule_data, monitoring_enabled=True,
                )
                for event_type, rule_data in rules
            ]
            self._alerts[(user_id, plan_id)] = saved
            return saved

    def list_alert_rules(self, user_id: str, plan_id: str) -> List[AlertRule]:
        with self._lock:
            return list(self._alerts.get((user_id, plan_id), []))

    def list_alert_events(self, user_id: str, unread_only: bool = False) -> List[AlertEvent]:
        with self._lock:
            events = [event for owner, event in self._alert_events.values() if owner == user_id]
            if unread_only:
                events = [event for event in events if event.read_at is None]
            return sorted(events, key=lambda event: event.created_at, reverse=True)

    def mark_alert_event_read(self, user_id: str, event_id: str) -> None:
        with self._lock:
            record = self._alert_events.get(event_id)
            if not record or record[0] != user_id:
                return
            event = record[1]
            self._alert_events[event_id] = (user_id, AlertEvent(
                id=event.id, ticker=event.ticker, event_type=event.event_type,
                price=event.price, quote_time=event.quote_time, created_at=event.created_at,
                read_at=datetime.now(timezone.utc).isoformat(),
            ))


class SupabaseAccountRepository:
    """Server-side PostgREST adapter for the custom username/PIN account schema."""

    def __init__(self, url: str, service_role_key: str, timeout: float = 10.0) -> None:
        self.base_url = f"{url.rstrip('/')}/rest/v1"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, table: str, **kwargs):
        response = self.session.request(method, f"{self.base_url}/{table}", timeout=self.timeout, **kwargs)
        if response.status_code == 409 and table == "users":
            raise UsernameTakenError("username_taken")
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            postgres_code = str(payload.get("code") or "")
            message = str(payload.get("message") or "").lower()
            if response.status_code == 404 or postgres_code == "PGRST202":
                code = "migration_required"
            elif postgres_code == "42702" or "ambiguous" in message:
                code = "migration_update_required"
            elif response.status_code in (401, 403):
                code = "permission_denied"
            else:
                code = "database_error"
            raise AccountStorageError(code, response.status_code) from None
        if not response.content:
            return []
        return response.json()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, username: str, pin: str) -> User:
        normalized = normalize_username(username)
        if not normalized:
            raise ValueError("username_required")
        rows = self._request(
            "POST", "users",
            headers={**self.session.headers, "Prefer": "return=representation"},
            json={"username": username.strip(), "normalized_username": normalized, "pin": pin},
        )
        row = rows[0]
        return User(id=row["id"], username=row["username"])

    def authenticate(self, username: str, pin: str) -> Tuple[Optional[User], str]:
        rows = self._request(
            "GET", "users",
            params={
                "normalized_username": f"eq.{normalize_username(username)}",
                "select": "id,username,pin",
                "limit": "1",
            },
        )
        if not rows:
            return None, "username_not_found"
        row = rows[0]
        if row["pin"] != pin:
            return None, "incorrect_pin"
        return User(id=row["id"], username=row["username"]), "ok"

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        self._request("POST", "user_sessions", json={
            "user_id": user_id,
            "token_hash": self._token_hash(token),
            "expires_at": expires_at.isoformat(),
        })
        return token

    def get_session_user(self, token: str) -> Optional[User]:
        now = datetime.now(timezone.utc).isoformat()
        sessions = self._request("GET", "user_sessions", params={
            "token_hash": f"eq.{self._token_hash(token)}",
            "revoked_at": "is.null",
            "expires_at": f"gt.{now}",
            "select": "user_id",
            "limit": "1",
        })
        if not sessions:
            return None
        users = self._request("GET", "users", params={
            "id": f"eq.{sessions[0]['user_id']}", "select": "id,username", "limit": "1",
        })
        return User(id=users[0]["id"], username=users[0]["username"]) if users else None

    def revoke_session(self, token: str) -> None:
        self._request(
            "PATCH", "user_sessions",
            params={"token_hash": f"eq.{self._token_hash(token)}"},
            json={"revoked_at": datetime.now(timezone.utc).isoformat()},
        )

    def get_preferences(self, user_id: str) -> UserPreferences:
        rows = self._request("GET", "user_preferences", params={
            "user_id": f"eq.{user_id}", "select": "*", "limit": "1",
        })
        if not rows:
            return UserPreferences()
        row = rows[0]
        return UserPreferences(
            language=row["language"], portfolio_capital=int(row["portfolio_capital"]),
            risk_budget_pct=float(row["risk_budget_pct"]),
            include_ai_news=bool(row["include_ai_news"]), schema_version=int(row["schema_version"]),
        )

    def save_preferences(self, user_id: str, preferences: UserPreferences) -> None:
        self._request(
            "POST", "user_preferences",
            headers={**self.session.headers, "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "user_id"},
            json={
                "user_id": user_id, "schema_version": preferences.schema_version,
                "language": preferences.language, "portfolio_capital": preferences.portfolio_capital,
                "risk_budget_pct": preferences.risk_budget_pct,
                "include_ai_news": preferences.include_ai_news,
            },
        )

    def list_favorites(self, user_id: str) -> List[str]:
        rows = self._request("GET", "favorites", params={
            "user_id": f"eq.{user_id}", "select": "ticker", "order": "created_at.asc",
        })
        return [row["ticker"] for row in rows]

    def add_favorite(self, user_id: str, ticker: str) -> None:
        symbol = ticker.strip().upper()
        if symbol:
            self._request(
                "POST", "favorites",
                headers={**self.session.headers, "Prefer": "resolution=ignore-duplicates"},
                json={"user_id": user_id, "ticker": symbol},
            )

    def remove_favorite(self, user_id: str, ticker: str) -> None:
        self._request("DELETE", "favorites", params={
            "user_id": f"eq.{user_id}", "ticker": f"eq.{ticker.strip().upper()}",
        })

    @staticmethod
    def _saved_plan(row: Dict[str, Any]) -> SavedPlanVersion:
        return SavedPlanVersion(
            plan_id=row["plan_id"], ticker=row["ticker"], version=int(row["version"]),
            plan_data=row["plan_data"], analysis_timestamp=row["analysis_timestamp"],
            created_at=row.get("created_at"),
        )

    def get_active_plan(self, user_id: str, ticker: str) -> Optional[SavedPlanVersion]:
        plans = self._request("GET", "saved_plans", params={
            "user_id": f"eq.{user_id}", "ticker": f"eq.{ticker.strip().upper()}",
            "select": "id,ticker,active_version", "limit": "1",
        })
        if not plans:
            return None
        plan = plans[0]
        versions = self._request("GET", "saved_plan_versions", params={
            "saved_plan_id": f"eq.{plan['id']}", "version": f"eq.{plan['active_version']}",
            "select": "saved_plan_id,version,plan_data,analysis_timestamp,created_at", "limit": "1",
        })
        if not versions:
            return None
        row = versions[0]
        row.update({"plan_id": row.pop("saved_plan_id"), "ticker": plan["ticker"]})
        return self._saved_plan(row)

    def save_plan(
        self, user_id: str, ticker: str, plan_data: Dict[str, Any], analysis_timestamp: str,
    ) -> SavedPlanVersion:
        rows = self._request("POST", "rpc/save_saved_plan_version", json={
            "p_user_id": user_id, "p_ticker": ticker.strip().upper(),
            "p_plan_data": plan_data, "p_analysis_timestamp": analysis_timestamp,
        })
        return self._saved_plan(rows[0])

    def list_plan_versions(self, user_id: str, ticker: str) -> List[SavedPlanVersion]:
        plans = self._request("GET", "saved_plans", params={
            "user_id": f"eq.{user_id}", "ticker": f"eq.{ticker.strip().upper()}",
            "select": "id,ticker", "limit": "1",
        })
        if not plans:
            return []
        plan = plans[0]
        rows = self._request("GET", "saved_plan_versions", params={
            "saved_plan_id": f"eq.{plan['id']}",
            "select": "saved_plan_id,version,plan_data,analysis_timestamp,created_at",
            "order": "version.desc",
        })
        return [self._saved_plan({**row, "plan_id": row["saved_plan_id"], "ticker": plan["ticker"]}) for row in rows]

    def list_plan_outcomes(self, user_id: str, plan_id: str) -> List[SavedPlanOutcome]:
        rows = self._request("GET", "saved_plan_outcomes", params={
            "user_id": f"eq.{user_id}", "saved_plan_id": f"eq.{plan_id}",
            "select": "id,saved_plan_id,plan_version,horizon_days,calculation_version,outcome_data",
            "order": "plan_version.desc,horizon_days.asc",
        })
        return [self._saved_plan_outcome(row) for row in rows]

    def record_plan_outcome(
        self, user_id: str, plan_id: str, plan_version: int, outcome_data: Dict[str, Any],
    ) -> SavedPlanOutcome:
        rows = self._request("POST", "rpc/record_saved_plan_outcome", json={
            "p_user_id": user_id, "p_saved_plan_id": plan_id,
            "p_plan_version": plan_version, "p_outcome_data": outcome_data,
        })
        return self._saved_plan_outcome(rows[0])

    @staticmethod
    def _saved_plan_outcome(row: Dict[str, Any]) -> SavedPlanOutcome:
        return SavedPlanOutcome(
            id=row["id"], saved_plan_id=row["saved_plan_id"], plan_version=int(row["plan_version"]),
            horizon_days=int(row["horizon_days"]), calculation_version=int(row["calculation_version"]),
            outcome_data=row["outcome_data"],
        )

    def replace_alert_rules(
        self, user_id: str, plan_id: str, plan_version: int,
        rules: Sequence[Tuple[str, Dict[str, Any]]],
    ) -> List[AlertRule]:
        rows = self._request("POST", "rpc/replace_plan_alert_rules", json={
            "p_user_id": user_id, "p_saved_plan_id": plan_id, "p_plan_version": plan_version,
            "p_rules": [{"event_type": event_type, "rule_data": rule_data} for event_type, rule_data in rules],
        })
        return [self._alert_rule(row) for row in rows]

    def list_alert_rules(self, user_id: str, plan_id: str) -> List[AlertRule]:
        rows = self._request("GET", "price_alerts", params={
            "user_id": f"eq.{user_id}", "saved_plan_id": f"eq.{plan_id}",
            "select": "id,saved_plan_id,plan_version,event_type,rule_data,monitoring_enabled",
            "order": "event_type.asc",
        })
        return [self._alert_rule(row) for row in rows]

    def list_alert_events(self, user_id: str, unread_only: bool = False) -> List[AlertEvent]:
        params = {
            "user_id": f"eq.{user_id}",
            "select": "id,ticker,event_type,price,quote_time,created_at,read_at",
            "order": "created_at.desc", "limit": "50",
        }
        if unread_only:
            params["read_at"] = "is.null"
        rows = self._request("GET", "alert_events", params=params)
        return [AlertEvent(
            id=row["id"], ticker=row["ticker"], event_type=row["event_type"],
            price=float(row["price"]), quote_time=row["quote_time"],
            created_at=row["created_at"], read_at=row.get("read_at"),
        ) for row in rows]

    def mark_alert_event_read(self, user_id: str, event_id: str) -> None:
        self._request(
            "PATCH", "alert_events",
            params={"id": f"eq.{event_id}", "user_id": f"eq.{user_id}"},
            json={"read_at": datetime.now(timezone.utc).isoformat()},
        )

    def list_enabled_alerts(self) -> List[Dict[str, Any]]:
        rows = self._request("GET", "price_alerts", params={
            "monitoring_enabled": "eq.true",
            "select": (
                "id,user_id,saved_plan_id,plan_version,event_type,rule_data,armed,"
                "last_price,last_quote_time,last_triggered_at,saved_plans!inner(ticker)"
            ),
        })
        for row in rows:
            row["ticker"] = row.get("saved_plans", {}).get("ticker")
        return rows

    def record_alert_evaluation(
        self, alert_id: str, price: float, quote_time: str, armed: bool,
        triggered: bool, idempotency_key: str, event_data: Dict[str, Any],
    ) -> Optional[str]:
        rows = self._request("POST", "rpc/record_alert_evaluation", json={
            "p_alert_id": alert_id, "p_price": price, "p_quote_time": quote_time,
            "p_armed": armed, "p_triggered": triggered,
            "p_idempotency_key": idempotency_key, "p_event_data": event_data,
        })
        if isinstance(rows, str):
            return rows
        return rows[0] if isinstance(rows, list) and rows else None

    def claim_pending_alert_deliveries(self, limit: int = 25) -> List[Dict[str, Any]]:
        # Migration 006 retains email-prefixed database names for compatibility.
        rows = self._request("POST", "rpc/claim_alert_email_deliveries", json={
            "p_limit": limit,
        })
        return rows if isinstance(rows, list) else []

    def record_alert_delivery(self, event_id: str, delivered: bool, error: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            "email_status": "sent" if delivered else "failed",
            "email_last_error": None if delivered else str(error or "delivery_failed")[:500],
        }
        if delivered:
            payload["email_sent_at"] = datetime.now(timezone.utc).isoformat()
        self._request("POST", "rpc/record_alert_email_delivery", json={
            "p_event_id": event_id, "p_delivered": delivered,
            "p_error": payload["email_last_error"],
        })

    @staticmethod
    def _alert_rule(row: Dict[str, Any]) -> AlertRule:
        return AlertRule(
            id=row["id"], saved_plan_id=row["saved_plan_id"], plan_version=int(row["plan_version"]),
            event_type=row["event_type"], rule_data=row["rule_data"],
            monitoring_enabled=bool(row.get("monitoring_enabled", False)),
        )
