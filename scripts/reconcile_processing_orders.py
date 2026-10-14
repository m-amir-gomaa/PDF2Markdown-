"""Owner-only recovery for missed payment/refund events. Credentials via environment.
Never grants credit from a browser flag or prints order/customer/secret payloads.
"""

import asyncio
import modal
from web.config import Settings
from web.credits import CreditProvider


async def main():
    settings = Settings.from_env()
    provider = CreditProvider(settings)
    provider.ready()
    ledger = modal.Function.from_name("mineru-pdf-extractor", "book_store")
    rows = await ledger.remote.aio("purchase_list", {})
    checked = changed = 0
    for row in rows:
        order_id = row["order_id"].rsplit(":", 1)[-1]
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
