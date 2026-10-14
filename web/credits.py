"""One-time processing packs. Only provider-confirmed, scoped orders grant credit."""

import hashlib, hmac, json, math
import httpx
from web.billing import BillingError


class CreditProvider:
    def __init__(self, settings, transport=None):
        self.s, self.transport = settings, transport

    def ready(self):
        if not self.s.billing_ready:
            raise BillingError(
                "Paid conversion opens after the seller account and processing packs are activated."
            )

    def headers(self):
        return {
            "Authorization": "Bearer " + self.s.lemon_api_key,
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }

    def units(self, variant):
        if str(variant) == self.s.variant5:
            return 50000
        if str(variant) == self.s.variant10:
            return 100000
        raise BillingError("This purchase belongs to another product.")

    async def order(self, order_id):
        self.ready()
        if not str(order_id).isdigit():
            raise BillingError("Invalid order.")
        try:
            async with httpx.AsyncClient(
                timeout=15, transport=self.transport, follow_redirects=False
            ) as c:
                r = await c.get(
                    "https://api.lemonsqueezy.com/v1/orders/" + str(order_id),
                    headers=self.headers(),
                )
                r.raise_for_status()
                v = r.json()["data"]
                a = v["attributes"]
                item = a["first_order_item"]
                units = self.units(item["variant_id"])
                if (
                    str(a["store_id"]) != self.s.store_id
                    or a["test_mode"] is not (self.s.billing_mode == "test")
                    or str(item["variant_id"])
                    not in (self.s.variant5, self.s.variant10)
                    or a["currency"] != "USD"
                    or a["status"]
                    not in ("paid", "refunded", "partial_refund", "fraudulent")
                    or a["subtotal"] - a.get("discount_total", 0) < units // 100
                ):
                    raise BillingError(
                        "This payment does not match the processing pack."
                    )
                if a.get("refunded") or a["status"] in ("refunded", "fraudulent"):
                    revoked = units
                elif a["status"] == "partial_refund":
                    refunded = a.get("refunded_amount_usd", a.get("refunded_amount"))
                    total = a.get("total_usd", a.get("total"))
                    if (
                        type(refunded) is not int
                        or type(total) is not int
                        or total <= 0
                        or not 0 <= refunded <= total
                    ):
                        raise BillingError("Refund details need review.")
                    revoked = min(units, math.ceil(units * refunded / total))
                else:
                    revoked = 0
                return {
                    "order_id": self.s.store_id + ":" + str(v["id"]),
                    "units": units,
                    "revoked": revoked,
                }
        except BillingError:
            raise
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise BillingError(
                "Payment verification is unavailable. No processing credits were added."
            ) from None

    async def key(self, key):
        self.ready()
        if not isinstance(key, str) or not 10 <= len(key) <= 200:
            raise BillingError("Enter the purchase key from your receipt.")
        try:
            async with httpx.AsyncClient(
                timeout=15, transport=self.transport, follow_redirects=False
            ) as c:
                r = await c.post(
                    "https://api.lemonsqueezy.com/v1/licenses/validate",
                    data={"license_key": key},
                )
                r.raise_for_status()
                v = r.json()
                meta = v["meta"]
                lic = v["license_key"]
                if (
                    v.get("valid") is not True
                    or lic["status"] not in ("active", "inactive")
                    or str(meta["store_id"]) != self.s.store_id
                    or lic.get("expires_at") is not None
                ):
                    raise BillingError("This processing-pack key is not valid.")
                units = self.units(meta["variant_id"])
                order = await self.order(meta["order_id"])
                if units != order["units"]:
                    raise BillingError("Purchase details do not match.")
                return order
        except BillingError:
            raise
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise BillingError("Purchase verification is unavailable.") from None

    async def checkout(self, owner, pack):
        self.ready()
        if pack not in (5, 10):
            raise BillingError("Choose a $5 or $10 processing pack.")
        variant = self.s.variant5 if pack == 5 else self.s.variant10
        body = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "checkout_data": {"custom": {"studio_owner": owner}},
                    "test_mode": self.s.billing_mode == "test",
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": self.s.store_id}},
                    "variant": {"data": {"type": "variants", "id": variant}},
                },
            }
        }
        try:
            async with httpx.AsyncClient(
                timeout=15, transport=self.transport, follow_redirects=False
            ) as c:
                r = await c.post(
                    "https://api.lemonsqueezy.com/v1/checkouts",
                    headers=self.headers(),
                    json=body,
                )
                r.raise_for_status()
                url = r.json()["data"]["attributes"]["url"]
                from urllib.parse import urlparse

                p = urlparse(url)
                if p.scheme != "https" or not (p.hostname or "").endswith(
                    ".lemonsqueezy.com"
                ):
                    raise BillingError("Checkout URL could not be verified.")
                return url
        except BillingError:
            raise
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise BillingError("Checkout is temporarily unavailable.") from None

    def webhook(self, body, signature):
        self.ready()
        expected = hmac.new(
            self.s.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            raise BillingError("Invalid payment signature.")
        try:
            v = json.loads(body)
            if v["meta"]["event_name"] not in ("order_created", "order_refunded"):
                return None
            if v["data"]["type"] != "orders":
                raise ValueError()
            return {
                "order_id": v["data"]["id"],
                "owner": v["meta"].get("custom_data", {}).get("studio_owner"),
            }
        except (KeyError, ValueError, TypeError):
            raise BillingError("Invalid payment event.") from None
