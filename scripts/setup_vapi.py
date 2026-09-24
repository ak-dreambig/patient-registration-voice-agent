"""Configure Vapi (tools, assistant, phone number) via its REST API. Idempotent.

Reads VAPI_API_KEY and VAPI_SECRET from .env (never printed). Existing tools and
the assistant are matched by name and updated in place instead of duplicated.

Run with: python -m scripts.setup_vapi
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

API = "https://api.vapi.ai"
BASE_URL = "https://patient-registration-voice-agent-production-c406.up.railway.app"
WEBHOOK_URL = f"{BASE_URL}/vapi/webhook"
PHONE_NUMBER = "+19108309031"
ASSISTANT_NAME = "Ava - Patient Intake"
ROOT = Path(__file__).resolve().parent.parent

FIRST_MESSAGE = (
    "Hi, thanks for calling Riverside Family Clinic. This is Ava, I can get you registered "
    "as a new patient. Could I start with your first and last name?"
)
END_CALL_MESSAGE = "Thanks for calling Riverside Family Clinic. Take care, goodbye!"


def load_system_prompt() -> str:
    """Prompt text after the BEGIN PROMPT marker, with HTML comments removed."""
    text = (ROOT / "agent" / "system_prompt.md").read_text(encoding="utf-8")
    # The marker is its own comment line; the header comment also mentions "BEGIN PROMPT".
    marker = re.search(r"^<!--[ =]*BEGIN PROMPT[ =]*-->[ \t]*$", text, flags=re.M)
    if marker is None:
        sys.exit("agent/system_prompt.md has no 'BEGIN PROMPT' marker line.")
    body = text[marker.end():]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()


def load_tools(secret: str) -> list[dict[str, Any]]:
    """Tool definitions from agent/tools.json, pointed at this deployment with the secret header."""
    domain = urlparse(BASE_URL).netloc
    raw = (ROOT / "agent" / "tools.json").read_text(encoding="utf-8").replace("YOUR-RAILWAY-DOMAIN", domain)
    tools = json.loads(raw)
    for tool in tools:
        tool["server"]["url"] = WEBHOOK_URL
        tool["server"]["headers"] = {"x-vapi-secret": secret}
    return tools


def assistant_body(tool_ids: list[str], secret: str) -> dict[str, Any]:
    """Full assistant configuration (used for both create and update)."""
    return {
        "name": ASSISTANT_NAME,
        "firstMessage": FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.3,
            "messages": [{"role": "system", "content": load_system_prompt()}],
            "toolIds": tool_ids,
            "tools": [{"type": "endCall"}],
        },
        # Female ElevenLabs voice on a multilingual model (English + Spanish).
        "voice": {"provider": "11labs", "voiceId": "sarah", "model": "eleven_turbo_v2_5"},
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
        "endCallMessage": END_CALL_MESSAGE,
        "maxDurationSeconds": 600,
        # Vapi replaced silenceTimeoutSeconds with speech-timeout hooks: nudge once, then hang up.
        "hooks": [
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 10, "triggerMaxCount": 1, "triggerResetMode": "onUserSpeech"},
                "do": [{"type": "say", "exact": "Are you still there?"}],
            },
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 20, "triggerMaxCount": 1},
                "do": [
                    {"type": "say", "exact": "I haven't heard anything, so I'll end the call now. Please call back anytime. Goodbye."},
                    {"type": "tool", "tool": {"type": "endCall"}},
                ],
            },
        ],
        "server": {"url": WEBHOOK_URL, "headers": {"x-vapi-secret": secret}, "timeoutSeconds": 20},
        # No "tool-calls": each tool has its own server, so tool calls must not also hit the assistant server.
        "serverMessages": ["end-of-call-report", "status-update"],
    }


class Vapi:
    """Minimal Vapi REST client."""

    def __init__(self, api_key: str, redact: str) -> None:
        self.redact = redact
        self.http = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        resp = self.http.request(method, path, json=body, params={"limit": 1000} if method == "GET" else None)
        if resp.status_code >= 400:
            detail = resp.text[:1000].replace(self.redact, "***")
            sys.exit(f"{method} {path} failed with {resp.status_code}: {detail}")
        return resp.json()


def upsert_tools(api: Vapi, tools: list[dict[str, Any]]) -> dict[str, str]:
    """Create or update each function tool, matched by function name. Returns name → id."""
    existing = {
        t["function"]["name"]: t["id"]
        for t in api.request("GET", "/tool")
        if t.get("type") == "function" and t.get("function", {}).get("name")
    }
    ids = {}
    for tool in tools:
        name = tool["function"]["name"]
        if name in existing:
            result, action = api.request("PATCH", f"/tool/{existing[name]}", tool), "updated"
        else:
            result, action = api.request("POST", "/tool", tool), "created"
        ids[name] = result["id"]
        print(f"tool {name}: {action} {result['id']}")
    return ids


def upsert_assistant(api: Vapi, body: dict[str, Any]) -> str:
    """Create or update the assistant, matched by name."""
    match = next((a for a in api.request("GET", "/assistant") if a.get("name") == ASSISTANT_NAME), None)
    if match:
        result, action = api.request("PATCH", f"/assistant/{match['id']}", body), "updated"
    else:
        result, action = api.request("POST", "/assistant", body), "created"
    print(f"assistant {ASSISTANT_NAME!r}: {action} {result['id']}")
    return result["id"]


def attach_phone_number(api: Vapi, assistant_id: str) -> str:
    """Point inbound calls on PHONE_NUMBER at the assistant."""
    number = next((p for p in api.request("GET", "/phone-number") if p.get("number") == PHONE_NUMBER), None)
    if number is None:
        sys.exit(f"Phone number {PHONE_NUMBER} not found in this Vapi account.")
    body = {"provider": number["provider"], "assistantId": assistant_id}
    api.request("PATCH", f"/phone-number/{number['id']}", body)
    print(f"phone {PHONE_NUMBER} ({number['provider']}): {number['id']} -> assistant {assistant_id}")
    return number["id"]


def main() -> None:
    """Configure tools, assistant and phone number."""
    load_dotenv(ROOT / ".env")
    api_key, secret = os.getenv("VAPI_API_KEY"), os.getenv("VAPI_SECRET")
    if not api_key or not secret:
        sys.exit("VAPI_API_KEY and VAPI_SECRET must be set in .env")
    api = Vapi(api_key, redact=secret)
    tool_ids = upsert_tools(api, load_tools(secret))
    assistant_id = upsert_assistant(api, assistant_body(list(tool_ids.values()), secret))
    attach_phone_number(api, assistant_id)


if __name__ == "__main__":
    main()
