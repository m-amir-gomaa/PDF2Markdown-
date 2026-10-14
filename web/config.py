from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    session_key: str
    checkout_url: str = ""
    store_id: str = ""
    variant_id: str = ""
    lemon_api_key: str = ""
    webhook_secret: str = ""
    variant5: str = ""
    variant10: str = ""
    billing_mode: str = "live"
    commercial_model: str = "prepaid_conversion"
    trial_pages: int = 1
    trial_bytes: int = 1024 * 1024
    paid_pages: int = 1000
    paid_bytes: int = 64 * 1024 * 1024
    paid_jobs: int = 2
    trial_seconds: int = 180
    paid_seconds: int = 300
    trial_monthly_global: int = 20
    trial_daily_global: int = 3
    retention_seconds: int = 48 * 3600
    chunk_pages: int = 20
    base_units: int = 5000
    page_units: int = 50
    text_base_units: int = 2500
    text_page_units: int = 10
    repair_base_units: int = 2000
    gpu_daily_pages: int = 3000
    gpu_owner_daily_pages: int = 2000
    text_daily_pages: int = 30000
    text_owner_daily_pages: int = 5000

    @property
    def payment_configured(self):
        return bool(
            self.store_id
            and self.variant5
            and self.variant10
            and self.lemon_api_key
            and self.webhook_secret
        )

    @property
    def billing_ready(self):
        return self.billing_mode == "live" and self.payment_configured

    @classmethod
    def from_env(cls):
        return cls(
            session_key=os.environ.get("DOCUMENT_STUDIO_SESSION_KEY", ""),
            store_id=os.environ.get("LEMON_STORE_ID", ""),
            lemon_api_key=os.environ.get("LEMON_API_KEY", ""),
            webhook_secret=os.environ.get("LEMON_WEBHOOK_SECRET", ""),
            variant5=os.environ.get("LEMON_VARIANT_5", ""),
            variant10=os.environ.get("LEMON_VARIANT_10", ""),
            billing_mode=os.environ.get("BILLING_MODE", "live"),
        )
