import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)


class GHLService:
    """
    Go High Level API v2 — Private Integration Token (PIT).
    PIT tokens are scoped to a location — do NOT pass locationId in requests.
    """

    BASE_URL = "https://services.leadconnectorhq.com"

    def __init__(self):
        self.access_token = current_app.config.get("GHL_ACCESS_TOKEN", "")
        self.location_id  = current_app.config.get("GHL_LOCATION_ID", "")
        self.sms_from     = current_app.config.get("SMS_FROM_NUMBER", "")

        if not self.access_token:
            raise ValueError("GHL_ACCESS_TOKEN is not configured.")

        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type":  "application/json",
            "Version":       "2021-07-28",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except requests.exceptions.HTTPError as e:
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            logger.error(f"GHL {method} {url} → {e.response.status_code}: {body}")
            raise RuntimeError(f"GHL API error {e.response.status_code}: {body}")
        except requests.exceptions.RequestException as e:
            logger.error(f"GHL request failed: {e}")
            raise RuntimeError(f"GHL request failed: {e}")

    # ------------------------------------------------------------------ #
    # Contacts
    # ------------------------------------------------------------------ #

    def create_or_update_contact(self, lead: dict) -> dict:
        phone = lead.get("phone", "")
        if not phone:
            raise ValueError(f"Lead '{lead.get('business_name')}' has no phone number.")

        payload = {
            "locationId": self.location_id,
            "phone":      phone,
            "firstName":  lead.get("business_name", ""),
            "tags":       ["outreach-automation", "no-website"],
            "source":     "WebReach Outreach",
        }

        # Try to find existing by phone first to avoid duplicates
        existing = self._find_contact_by_phone(phone)
        if existing:
            contact_id = existing.get("id")
            logger.info(f"Found existing GHL contact {contact_id} for {phone}")
            try:
                data = self._request("PUT", f"/contacts/{contact_id}", json=payload)
                return data.get("contact", data)
            except Exception:
                return existing

        data = self._request("POST", "/contacts/", json=payload)
        logger.info(f"GHL create contact response keys: {list(data.keys())}")
        contact = data.get("contact", data)
        logger.info(f"GHL created contact ID: {contact.get('id')}")
        return contact

    def _find_contact_by_phone(self, phone: str) -> dict | None:
        try:
            data = self._request(
                "GET", "/contacts/search/duplicate",
                params={"locationId": self.location_id, "number": phone}
            )
            return data.get("contact") or None
        except Exception:
            pass
        try:
            data = self._request(
                "GET", "/contacts/",
                params={"locationId": self.location_id, "query": phone, "limit": 1}
            )
            contacts = data.get("contacts", [])
            return contacts[0] if contacts else None
        except Exception:
            return None

    def get_contact(self, contact_id: str) -> dict:
        data = self._request("GET", f"/contacts/{contact_id}")
        return data.get("contact", data)

    def add_note_to_contact(self, contact_id: str, note: str) -> dict:
        return self._request("POST", f"/contacts/{contact_id}/notes/",
                             json={"body": note, "userId": ""})

    def add_tag(self, contact_id: str, tag: str) -> dict:
        return self._request("POST", f"/contacts/{contact_id}/tags/",
                             json={"tags": [tag]})

    def remove_tag(self, contact_id: str, tag: str) -> dict:
        return self._request("DELETE", f"/contacts/{contact_id}/tags/",
                             json={"tags": [tag]})

    # ------------------------------------------------------------------ #
    # Conversations & SMS
    # ------------------------------------------------------------------ #

    def get_or_create_conversation(self, contact_id: str) -> dict:
        try:
            data = self._request("GET", "/conversations/search",
                                 params={"locationId": self.location_id,
                                         "contactId": contact_id})
            convos = data.get("conversations", [])
            if convos:
                return convos[0]
        except Exception:
            pass

        data = self._request("POST", "/conversations/",
                             json={"locationId": self.location_id,
                                   "contactId": contact_id})
        return data.get("conversation", data)

    def send_sms(self, contact_id: str, message: str, conversation_id: str = None) -> dict:
        payload = {
            "type":      "SMS",
            "contactId": contact_id,
            "message":   message,
        }
        if self.sms_from:
            payload["fromNumber"] = self.sms_from
        if conversation_id:
            payload["conversationId"] = conversation_id
        return self._request("POST", "/conversations/messages", json=payload)

    def get_conversation_messages(self, conversation_id: str) -> list:
        data = self._request("GET", f"/conversations/{conversation_id}/messages")
        return data.get("messages", {}).get("messages", [])

    # ------------------------------------------------------------------ #
    # Webhooks
    # ------------------------------------------------------------------ #

    def register_webhook(self, webhook_url: str, events: list = None) -> dict:
        if events is None:
            events = ["InboundMessage"]
        return self._request("POST", "/webhooks/",
                             json={"locationId": self.location_id,
                                   "url": webhook_url,
                                   "events": events})
