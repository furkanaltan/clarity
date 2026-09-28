"""Brevo adapter for a single explicitly authorized VKS dispatch; never retries."""
import hashlib
import html
import json
import urllib.error
import urllib.parse
import urllib.request


class CancellationTransportError(RuntimeError):
    def __init__(self, code, *, retry_allowed=False):
        super().__init__(code)
        self.retry_allowed = retry_allowed


def send_cancellation_email(plan, *, api_key, sender_name, api_url):
    if not api_key:
        raise CancellationTransportError('transport_not_configured', retry_allowed=True)
    if hashlib.sha256(plan['body'].encode()).hexdigest() != plan['body_sha256']:
        raise CancellationTransportError('transport_payload_mismatch', retry_allowed=True)
    payload = {
        'sender': {'name': sender_name, 'email': plan['sender']},
        'to': [{'email': plan['recipient']}],
        'replyTo': {'email': plan['reply_to']},
        'subject': plan['subject'], 'textContent': plan['body'],
        'htmlContent': '<html><body><pre style="white-space:pre-wrap">' + html.escape(plan['body']) + '</pre></body></html>',
        'tags': ['rove-contract-cancellation'],
        'headers': {'X-Mailin-Custom': 'vks-attempt=' + plan['message_id']},
    }
    request = urllib.request.Request(api_url, data=json.dumps(payload).encode(), method='POST',
        headers={'accept':'application/json','content-type':'application/json','api-key':api_key})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 201:
                raise CancellationTransportError('transport_outcome_unknown')
            result = json.loads(response.read(65537))
            message_id = result.get('messageId') if isinstance(result,dict) else None
            if not isinstance(message_id,str) or not message_id.strip() or len(message_id)>300 or any(ord(char)<32 for char in message_id):
                raise CancellationTransportError('transport_outcome_unknown')
            return message_id
    except urllib.error.HTTPError as exc:
        # A timeout/5xx/409 may hide acceptance: the local receipt must keep retries blocked.
        if exc.code in {400,401,403,404,422,429}:
            code = ('temporary_transport_failure' if exc.code==429 else
                    'permanent_transport_failure' if exc.code in {401,403,404,422} else 'transport_rejected')
            raise CancellationTransportError(code,retry_allowed=True) from exc
        raise CancellationTransportError('transport_outcome_unknown') from exc
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        raise CancellationTransportError('transport_outcome_unknown') from None


def fetch_cancellation_delivery(message, *, api_key, api_url):
    if not api_key:
        raise CancellationTransportError('transport_not_configured')
    query = urllib.parse.urlencode({'messageId':message['provider_message_id'],'email':message['recipient'],
                                    'event':'delivered','limit':10,'days':90})
    url = api_url.rsplit('/email',1)[0] + '/statistics/events?' + query
    request = urllib.request.Request(url, headers={'accept':'application/json','api-key':api_key})
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            if response.status != 200:
                raise ValueError()
            data = json.loads(response.read(65537))
        events = data.get('events') if isinstance(data,dict) else None
        if not isinstance(events,list):
            raise ValueError()
        return next((event for event in events if isinstance(event,dict) and event.get('event')=='delivered'
            and event.get('messageId')==message['provider_message_id'] and event.get('email')==message['recipient']),None)
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        raise CancellationTransportError('delivery_lookup_failed') from None
