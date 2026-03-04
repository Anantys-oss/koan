"""Google Chat messaging provider.

Webhook push-only provider for Google Chat spaces.
Supports text messages and Cards v2 format.
"""

import json
import os
import sys
import time
from typing import List, Optional

import requests

from app.messaging.base import MessagingProvider, Update
from app.messaging import register_provider

MAX_PAYLOAD_BYTES = 32000
RATE_LIMIT_SECONDS = 1


@register_provider("googlechat")
class GoogleChatProvider(MessagingProvider):

    def __init__(self):
        self._webhook_url: str = ""
        self._last_send_time: float = 0.0

    def configure(self) -> bool:
        from app.utils import load_dotenv
        load_dotenv()

        self._webhook_url = os.environ.get("KOAN_GCHAT_WEBHOOK_URL", "")
        if not self._webhook_url:
            print("[googlechat] KOAN_GCHAT_WEBHOOK_URL not set.", file=sys.stderr)
            return False
        return True

    def get_provider_name(self) -> str:
        return "googlechat"

    def get_channel_id(self) -> str:
        if len(self._webhook_url) > 60:
            return self._webhook_url[:30] + "..." + self._webhook_url[-20:]
        return self._webhook_url

    def send_message(self, text: str) -> bool:
        self._enforce_rate_limit()
        payload = {"text": text}
        return self._post(payload)

    def send_card(self, card_json: dict) -> bool:
        self._enforce_rate_limit()
        return self._post(card_json)

    def poll_updates(self, offset: Optional[int] = None) -> List[Update]:
        return []

    def chunk_message(self, text: str, max_size: int = MAX_PAYLOAD_BYTES) -> List[str]:
        if len(text.encode("utf-8")) <= max_size:
            return [text]
        chunk_chars = max_size // 4
        return [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)]

    def _enforce_rate_limit(self):
        elapsed = time.time() - self._last_send_time
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_send_time = time.time()

    def _post(self, payload: dict, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                resp = requests.post(self._webhook_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    return True
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = (2 ** attempt) * 2
                    print(f"[googlechat] {resp.status_code}, retry in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[googlechat] Error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
                return False
            except requests.RequestException as e:
                wait = (2 ** attempt) * 2
                print(f"[googlechat] Request error: {e}, retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
        return False
