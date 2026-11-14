import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def _list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "./data"))
    tz_name: str = field(default_factory=lambda: _env("TZ", "UTC"))
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8080")))
    # Public URL, needed for the OIDC redirect (e.g. https://ticklight.example.com)
    base_url: str = field(default_factory=lambda: _env("BASE_URL").rstrip("/"))

    # ---- auth ----
    # "none" = trusted LAN, everything acts as the first user. "oidc" = log in via OIDC.
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "none").lower())
    oidc_issuer: str = field(default_factory=lambda: _env("OIDC_ISSUER").rstrip("/"))
    oidc_client_id: str = field(default_factory=lambda: _env("OIDC_CLIENT_ID"))
    oidc_client_secret: str = field(default_factory=lambda: _env("OIDC_CLIENT_SECRET"))
    oidc_scopes: str = field(default_factory=lambda: _env("OIDC_SCOPES", "openid profile email"))
    # Optional: only members of these groups (from the "groups" claim) may log in.
    oidc_allowed_groups: list[str] = field(
        default_factory=lambda: _list(_env("OIDC_ALLOWED_GROUPS")))
    session_days: int = field(default_factory=lambda: int(_env("SESSION_DAYS", "90")))

    # ---- Telegram ----
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_users: set[int] = field(
        default_factory=lambda: _ids(_env("TELEGRAM_ALLOWED_USERS")))
    summary_time: str = field(default_factory=lambda: _env("SUMMARY_TIME", "08:00"))

    # ---- AI ----
    openrouter_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    # Free models to try in order. Empty = pick free models from OpenRouter's list automatically.
    openrouter_free_models: list[str] = field(
        default_factory=lambda: _list(_env("OPENROUTER_FREE_MODELS")))
    # Cheap paid model used when free ones fail or are rate limited. Empty = never pay.
    openrouter_paid_model: str = field(default_factory=lambda: _env("OPENROUTER_PAID_MODEL"))
    # Any OpenAI-compatible endpoint, e.g. Ollama on the gaming PC: http://pc:11434/v1
    local_ai_url: str = field(default_factory=lambda: _env("LOCAL_AI_URL").rstrip("/"))
    local_ai_model: str = field(default_factory=lambda: _env("LOCAL_AI_MODEL"))
    # Order to try providers in: free, local, paid
    ai_order: list[str] = field(
        default_factory=lambda: _list(_env("AI_ORDER", "free,local,paid")))

    # ---- voice ----
    # OpenAI-compatible speech-to-text, e.g. Speaches on the GPU box: http://192.168.1.30:8000/v1
    whisper_url: str = field(default_factory=lambda: _env("WHISPER_URL").rstrip("/"))
    whisper_model: str = field(
        default_factory=lambda: _env("WHISPER_MODEL", "Systran/faster-whisper-small"))
    whisper_key: str = field(default_factory=lambda: _env("WHISPER_API_KEY"))
    whisper_language: str = field(default_factory=lambda: _env("WHISPER_LANGUAGE"))

    # ---- push notifications ----
    # Contact for push services; defaults to BASE_URL
    vapid_subject: str = field(default_factory=lambda: _env("VAPID_SUBJECT"))

    # ---- Alexa (optional) ----
    # Skill ID from the Alexa developer console (amzn1.ask.skill....). Empty = /alexa disabled.
    alexa_skill_id: str = field(default_factory=lambda: _env("ALEXA_SKILL_ID"))
    # Map Alexa accounts to Ticklight users: "amzn1.ask.account.XXX:2,…". Unmapped = first user.
    alexa_users: list[str] = field(default_factory=lambda: _list(_env("ALEXA_USERS")))
    # Only turn off for local testing; Amazon requires signature checks.
    alexa_verify: bool = field(default_factory=lambda: _env("ALEXA_VERIFY", "true").lower() != "false")

    # ---- behaviour ----
    upcoming_days: int = field(default_factory=lambda: int(_env("UPCOMING_DAYS", "7")))
    # One-off tasks this many days past due move to Someday
    stale_overdue_days: int = field(default_factory=lambda: int(_env("STALE_OVERDUE_DAYS", "7")))
    # Undated tasks untouched this long move to Someday
    stale_undated_days: int = field(default_factory=lambda: int(_env("STALE_UNDATED_DAYS", "21")))
    # Default energy by time of day, "HH:level" pairs (level: 1 low, 2 medium, 3 high)
    energy_schedule: str = field(
        default_factory=lambda: _env("ENERGY_SCHEDULE", "00:1,09:3,12:2,15:1,18:2,21:1"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "ticklight.db")

    @property
    def oidc_enabled(self) -> bool:
        return self.auth_mode == "oidc"


settings = Settings()
