"""Operator-only Telegram/Brevo transport; never import application or bot startup."""

import json
import re
import urllib.request


class AlertError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AlertError("alert_redirect_blocked")


def post(url, body, headers):
    request = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
        if response.status not in (200, 201):
            raise AlertError("alert_http_failed")
        raw = response.read(16385)
        if len(raw) > 16384:
            raise AlertError("alert_response_too_large")
        return json.loads(raw)


class OperatorAlerts:
    def __init__(self, recipients, credentials, *, send=post):
        self.recipients, self.credentials, self.send = recipients, credentials, send
        if not re.fullmatch(r"-?[1-9][0-9]*", recipients["telegram_chat_id"]):
            raise AlertError("operator_chat_invalid")
        for name in ("email", "from_email"):
            if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", recipients[name]):
                raise AlertError("operator_email_invalid")

    def notify(self, level, stage, run_id):
        # Only fixed internal identifiers reach either provider, never exception text.
        if level not in {"TEST", "FAILURE", "WARNING", "CRITICAL", "RECOVERY"} or not all(
            re.fullmatch(r"[A-Z0-9_-]{1,64}", item) for item in (stage, run_id)
        ):
            raise AlertError("alert_message_invalid")
        text = f"Rov.E Recovery: {level}; Phase={stage}; Referenz={run_id}. Operator-Pruefung erforderlich."
        delivered = {}
        for channel in ("telegram", "email"):
            try:
                if channel == "telegram":
                    token = self.credentials("TELEGRAM_TOKEN")
                    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
                        raise AlertError("telegram_token_invalid")
                    result = self.send(f"https://api.telegram.org/bot{token}/sendMessage",
                        {"chat_id": self.recipients["telegram_chat_id"], "text": text}, {})
                    message = result.get("result", {})
                    accepted = (result.get("ok") is True and isinstance(message, dict)
                                and type(message.get("message_id")) is int and message["message_id"] > 0
                                and str(message.get("chat", {}).get("id")) == self.recipients["telegram_chat_id"])
                else:
                    result = self.send("https://api.brevo.com/v3/smtp/email",
                        {"sender": {"email": self.recipients["from_email"], "name": "Rov.E Operator"},
                         "to": [{"email": self.recipients["email"]}],
                         "subject": f"Rov.E Recovery {level}", "textContent": text},
                        {"api-key": self.credentials("BREVO_API_KEY")})
                    accepted = isinstance(result.get("messageId"), str) and bool(result["messageId"])
                delivered[channel] = accepted
            except Exception:
                # Provider exceptions may embed token-bearing URLs or request headers.
                delivered[channel] = False
        return delivered
