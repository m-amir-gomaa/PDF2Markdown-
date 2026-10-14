"""Fresh provider-authoritative entitlement checks; no browser flags/webhook replay ledger."""
from datetime import datetime, timezone
import httpx

class BillingError(Exception):
    pass


def future(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp.tzinfo is not None and stamp > datetime.now(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return False


class LemonVerifier:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    async def verify(self, key):
        s = self.settings
        if not s.billing_ready:
            raise BillingError('Subscriptions are opening soon.')
        if not isinstance(key,str) or not 10 <= len(key) <= 200:
            raise BillingError('Enter the purchase key from your receipt.')
        try:
            async with httpx.AsyncClient(timeout=12, transport=self.transport, follow_redirects=False) as client:
                response = await client.post('https://api.lemonsqueezy.com/v1/licenses/validate', data={'license_key':key})
                response.raise_for_status(); value = response.json()
                meta, license = value.get('meta',{}), value.get('license_key',{})
                if value.get('valid') is not True or license.get('status') not in ('active','inactive'):
                    raise BillingError('This purchase key is not active.')
                if str(meta.get('store_id')) != s.store_id or str(meta.get('variant_id')) != s.variant_id:
                    raise BillingError('This key belongs to a different product.')
                if license.get('expires_at') is not None and not future(license['expires_at']):
                    raise BillingError('This subscription has expired.')
                headers={'Authorization':'Bearer '+s.lemon_api_key,'Accept':'application/vnd.api+json'}
                order_response=await client.get('https://api.lemonsqueezy.com/v1/orders/'+str(meta['order_id']),headers=headers)
                order_response.raise_for_status();order=order_response.json()['data']['attributes']
                if order.get('test_mode') is not (s.billing_mode=='test') or order.get('status')!='paid':
                    raise BillingError('Payment is not valid for this service.')
                subscriptions=await client.get('https://api.lemonsqueezy.com/v1/subscriptions',headers=headers,
                    params={'filter[order_item_id]':meta['order_item_id'],'filter[store_id]':s.store_id,'filter[variant_id]':s.variant_id})
                subscriptions.raise_for_status();data=subscriptions.json()['data']
                if len(data)!=1:
                    raise BillingError('Subscription verification needs attention.')
                sub=data[0]['attributes'];period=sub.get('renews_at') or sub.get('ends_at')
                if (str(sub.get('store_id'))!=s.store_id or str(sub.get('variant_id'))!=s.variant_id
                        or sub.get('test_mode') is not (s.billing_mode=='test')
                        or sub.get('status') not in ('active','cancelled') or not future(period)):
                    raise BillingError('Renew or restore your subscription before starting a conversion.')
                # Identity/cycle come from the provider, never customer fields.
                return {'license_id':str(license['id']), 'period':period,'plan':'paid'}
        except BillingError:
            raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError,AttributeError):
            raise BillingError('Payment verification is temporarily unavailable. No conversion was started.') from None
