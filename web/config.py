from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    session_key: str
    checkout_url: str = ''
    store_id: str = ''
    variant_id: str = ''
    lemon_api_key: str = ''
    billing_mode: str = 'live'
    commercial_model: str = 'monthly_access_metered_processing'
    trial_pages: int = 1
    trial_bytes: int = 1024 * 1024
    paid_pages: int = 50
    paid_bytes: int = 10 * 1024 * 1024
    paid_jobs: int = 2
    trial_seconds: int = 180
    paid_seconds: int = 300
    trial_monthly_global: int = 20
    trial_daily_global: int = 3
    retention_seconds: int = 48 * 3600

    @property
    def billing_ready(self):
        return self.commercial_model == 'subscription_with_included_compute' and bool(self.checkout_url.startswith('https://') and self.store_id and self.variant_id and self.lemon_api_key)

    @classmethod
    def from_env(cls):
        return cls(session_key=os.environ.get('DOCUMENT_STUDIO_SESSION_KEY',''),
                   checkout_url=os.environ.get('DOCUMENT_STUDIO_CHECKOUT_URL',''),
                   store_id=os.environ.get('LEMON_STORE_ID',''), variant_id=os.environ.get('LEMON_VARIANT_ID',''),
                   lemon_api_key=os.environ.get('LEMON_API_KEY',''), billing_mode=os.environ.get('BILLING_MODE','live'))
