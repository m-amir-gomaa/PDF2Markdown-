"""Owner-only recovery for missed payment/refund events. Credentials via environment.
Never grants credit from a browser flag or prints order/customer/secret payloads.
"""

import asyncio
import modal
from web.config import Settings
from web.paddle import credit_provider
from web.payments import process_paddle_events, reconcile_paddle


async def main():
    settings = Settings.from_env()
    provider = credit_provider(settings)
    provider.ready()
    if not settings.billing_ready:
        raise SystemExit("Live payment reconciliation is disabled.")
    ledger = modal.Function.from_name("mineru-pdf-extractor", "book_store")

    async def store(operation, arguments):
        return await ledger.remote.aio(operation, arguments)

    if settings.billing_provider == "paddle":
        await process_paddle_events(provider, store)
    rows = await ledger.remote.aio("purchase_list", {})
    checked = changed = 0
    for row in rows:
        order_id = row["order_id"].rsplit(":", 1)[-1]
        if settings.billing_provider == "paddle":
            if not row["order_id"].startswith(provider.prefix):
                continue
            await reconcile_paddle(provider, store, order_id)
            checked += 1
            continue
        if not order_id.isdigit():
            continue
        purchase = await provider.order(order_id)
        checked += 1
        if purchase["revoked"] > row["revoked"]:
            await ledger.remote.aio(
                "reverse", {"order_id": row["order_id"], "units": purchase["revoked"]}
            )
            changed += 1
    print("Orders checked:", checked, "refund balances reconciled:", changed)


if __name__ == "__main__":
    asyncio.run(main())
