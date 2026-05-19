import logging
import requests
from flask import current_app
from datetime import datetime

logger = logging.getLogger(__name__)


class GHLService:
    """
    Go High Level API v2 integration using Private Integration Token (PIT).
    Docs: https://highlevel.stoplight.io/docs/integrations
    """

    BASE_URL = "https://services.leadconnectorhq.com"

    def __init__(self):
        self.access_token = current_app.config.get("GHL_ACCESS_TOKEN", "")
        self.location_id = current_app.config.get("GHL_LOCATION_ID", "")
        self.sms_from = current_app.config.get("SMS_FROM_NUMBER", "")

        if not self.access_token:
            raise ValueError("GHL_ACCESS_TOKEN is not configured. Add it to your .env file.")

        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {}
        except requests.exceptions.HTTPError as e:
            body = ""
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
        """Create a new contact in GHL or update if phone already exists."""
        phone = lead.get("phone", "")
        if not phone:
            raise ValueError(f"Lead '{lead.get('business_name')}' has no phone number.")

        payload = {
            "locationId": self.location_id,
            "phone": phone,
            "firstName": lead.get("business_name", ""),
            "tags": ["outreach-automation", "no-website"],
            "source": "Outreach Automation",
            "customFields": [
                {"key": "google_rating", "field_value": str(lead.get("rating", ""))},
                {"key": "review_count", "field_value": str(lead.get("reviews_count", ""))},
                {"key": "business_category", "field_value": lead.get("category", "")},
                {"key": "google_maps_url", "field_value": lead.get("google_maps_url", "")},
            ],
        }

        # Try to find existing contact by phone first
        existing = self._find_contact_by_phone(phone)
        if existing:
            contact_id = existing.get("id")
            data = self._request("PUT", f"/contacts/{contact_id}", json=payload)
            return data.get("contact", data)

        data = self._request("POST", "/contacts/", json=payload)
        return data.get("contact", data)

    def _find_contact_by_phone(self, phone: str) -> dict | None:
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
        payload = {"body": note, "userId": ""}
        return self._request("POST", f"/contacts/{contact_id}/notes/", json=payload)

    def add_tag(self, contact_id: str, tag: str) -> dict:
        payload = {"tags": [tag]}
        return self._request("POST", f"/contacts/{contact_id}/tags/", json=payload)

    # ------------------------------------------------------------------ #
    # Conversations & SMS
    # ------------------------------------------------------------------ #

    def get_or_create_conversation(self, contact_id: str) -> dict:
        """Get or create a conversation for a contact."""
        try:
            data = self._request(
                "GET", "/conversations/search",
                params={"locationId": self.location_id, "contactId": contact_id}
            )
            convos = data.get("conversations", [])
            if convos:
                return convos[0]
        except Exception:
            pass

        payload = {
            "locationId": self.location_id,
            "contactId": contact_id,
        }
        data = self._request("POST", "/conversations/", json=payload)
        return data.get("conversation", data)

    def send_sms(self, contact_id: str, message: str, conversation_id: str = None) -> dict:
        """Send an outbound SMS to a contact."""
        payload = {
            "type": "SMS",
            "contactId": contact_id,
            "message": message,
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
        payload = {
            "locationId": self.location_id,
            "url": webhook_url,
            "events": events,
        }
        return self._request("POST", "/webhooks/", json=payload)
