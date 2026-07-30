"""
A stand-in for the Go High Level API, for verification runs only.

The live system talks to GHL for two things: looking a contact up, and sending
an SMS. Pointing GHL_API_BASE_URL at this server lets the whole inbound path be
exercised for real — webhook in, template chosen, SMS out, conversation
recorded — without texting a single real business.

Every "sent" message is kept in memory and can be read back from /_sent, which
is what proves the reply genuinely left the application.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SENT_MESSAGES = []
CONTACTS = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the verification output clean

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/_sent"):
            return self._json(200, {"messages": SENT_MESSAGES})

        if self.path.startswith("/contacts/"):
            contact_id = self.path.split("/contacts/", 1)[1].split("?")[0]
            contact = CONTACTS.get(contact_id)
            if contact:
                return self._json(200, {"contact": contact})
            return self._json(404, {"error": "not found"})

        if self.path.startswith("/conversations/search"):
            return self._json(200, {"conversations": [{"id": "stub-conversation"}]})

        return self._json(200, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            data = {}

        if self.path.startswith("/_reset"):
            SENT_MESSAGES.clear()
            return self._json(200, {"ok": True})

        if self.path.startswith("/_contact"):
            CONTACTS[data["id"]] = data
            return self._json(200, {"ok": True})

        if self.path.startswith("/conversations/messages"):
            SENT_MESSAGES.append(data)
            return self._json(201, {"messageId": f"stub-msg-{len(SENT_MESSAGES)}",
                                    "conversationId": data.get("conversationId", "stub-conversation")})

        if self.path.startswith("/conversations"):
            return self._json(201, {"conversation": {"id": "stub-conversation"}})

        return self._json(200, {"ok": True})

    def do_DELETE(self):
        return self._json(200, {"ok": True})


class StubGHL:
    def __init__(self, port=5099):
        self.port = port
        self._server = HTTPServer(("127.0.0.1", port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def register_contact(self, contact_id, phone, name=""):
        CONTACTS[contact_id] = {"id": contact_id, "phone": phone, "firstName": name}

    @property
    def sent(self):
        return list(SENT_MESSAGES)

    def reset(self):
        SENT_MESSAGES.clear()


if __name__ == "__main__":
    stub = StubGHL().start()
    print(f"Stub GHL listening on {stub.base_url}")
    threading.Event().wait()
