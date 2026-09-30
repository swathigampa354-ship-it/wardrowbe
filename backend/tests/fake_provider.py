"""A stand-in OpenAI-compatible provider so the trial's AI code paths can be
tested end-to-end with no API key, no network, and no quota.

Serves ``POST /v1/chat/completions`` and ``GET /v1/models``, and can simulate
the failure modes the spec asks us to verify: 401, 429, 500, truncation,
malformed JSON, and slow responses.

Run:  python -m tests.fake_provider --port 8991
Env:  FAKE_MODE=ok|unauthorized|ratelimit|server_error|garbage|truncated|slow
"""

import argparse
import base64
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAGS = {
    "type": "shirt",
    "subtype": "oxford",
    "primary_color": "light-blue",
    "colors": ["light-blue", "white"],
    "pattern": "solid",
    "material": "cotton",
    "formality": "smart-casual",
    "style": ["classic"],
    "season": ["spring", "fall", "all-season"],
    "fit": "regular",
}

OUTFITS = {
    "outfits": [
        {
            "items": [1, 2, 3],
            "headline": "Crisp Blue Everyday",
            "highlights": [
                "Light blue shirt against navy trousers keeps the palette tight",
                "Cotton and wool textures contrast without clashing",
            ],
            "styling_tip": "Half-tuck the shirt to break the line at the waist",
        },
        {
            "items": [4, 5, 3],
            "headline": "Monochrome Neutral",
            "highlights": ["Beige and cream sit in one family", "Sneakers keep it casual"],
            "styling_tip": "Add a belt that matches the shoe leather",
        },
        {
            "items": [6, 2, 7],
            "headline": "Olive Contrast",
            "highlights": ["Olive and denim are analogous enough to harmonize"],
            "styling_tip": "Roll the sleeve once, not twice",
        },
    ]
}


def mode() -> str:
    return os.environ.get("FAKE_MODE", "ok")


TYPE_PRESETS: dict[str, dict] = {
    "pants": {
        "type": "pants",
        "subtype": "trousers",
        "primary_color": "navy",
        "colors": ["navy"],
        "pattern": "solid",
        "material": "wool",
        "formality": "business-casual",
        "style": ["classic"],
        "season": ["fall", "winter", "all-season"],
        "fit": "slim",
    },
    "jeans": {
        "type": "jeans",
        "subtype": None,
        "primary_color": "blue",
        "colors": ["blue"],
        "pattern": "solid",
        "material": "denim",
        "formality": "casual",
        "style": ["casual"],
        "season": ["spring", "summer", "fall"],
        "fit": "regular",
    },
    "shoes": {
        "type": "shoes",
        "subtype": "loafers",
        "primary_color": "black",
        "colors": ["black"],
        "pattern": "solid",
        "material": "leather",
        "formality": "business-casual",
        "style": ["classic"],
        "season": ["all-season"],
        "fit": "regular",
    },
    "t-shirt": {
        "type": "t-shirt",
        "subtype": None,
        "primary_color": "white",
        "colors": ["white"],
        "pattern": "solid",
        "material": "cotton",
        "formality": "very-casual",
        "style": ["casual", "minimalist"],
        "season": ["summer"],
        "fit": "regular",
    },
    "sweater": {
        "type": "sweater",
        "subtype": "crewneck",
        "primary_color": "brown",
        "colors": ["brown", "tan"],
        "pattern": "knit" and "solid",
        "material": "knit",
        "formality": "casual",
        "style": ["classic"],
        "season": ["winter", "fall"],
        "fit": "relaxed",
    },
    "jacket": {
        "type": "jacket",
        "subtype": "bomber",
        "primary_color": "olive",
        "colors": ["olive"],
        "pattern": "solid",
        "material": "nylon",
        "formality": "casual",
        "style": ["streetwear"],
        "season": ["spring", "fall"],
        "fit": "relaxed",
    },
    "boots": {
        "type": "boots",
        "subtype": "chelsea",
        "primary_color": "brown",
        "colors": ["brown"],
        "pattern": "solid",
        "material": "leather",
        "formality": "smart-casual",
        "style": ["rugged"],
        "season": ["fall", "winter"],
        "fit": None,
    },
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence the default stderr noise
        if os.environ.get("FAKE_VERBOSE"):
            super().log_message(fmt, *args)

    def _tagged_payload(self, request: dict, found: str | None = None) -> dict:
        if found is None:
            found = self._image_directives(request)[0]
        if found and found in TYPE_PRESETS:
            return {**TAGS, **TYPE_PRESETS[found]}
        if found:
            return {**TAGS, "type": found}
        return TAGS

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            self._send(
                200, {"object": "list", "data": [{"id": "fake-vision"}, {"id": "fake-text"}]}
            )
        else:
            self._send(404, {"error": {"message": "not found"}})

    @staticmethod
    def _image_directives(request: dict) -> tuple[str | None, str | None]:
        """Test hook. Images under test carry "WARDROBE:<directive>" in their PNG
        text chunk, so a test can control the stub's answer per request without
        process-global env state:

            WARDROBE:tag=shirt;mode=unauthorized

        This is the only channel that reaches the stub, because the AI request is
        built by the app (not the test client) and we do not want to thread test
        headers through production code.
        """
        tag = current = None
        for message in request.get("messages", []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                url = (part.get("image_url") or {}).get("url", "") if isinstance(part, dict) else ""
                if "," not in url:
                    continue
                try:
                    blob = base64.b64decode(url.split(",", 1)[1])
                except Exception:  # noqa: BLE001
                    continue
                for key, pattern in (
                    ("tag", rb"WARDROBE:tag=([a-z_\-]+)"),
                    ("mode", rb"WARDROBE:mode=([a-z_]+)"),
                ):
                    found = re.search(pattern, blob)
                    if found:
                        if key == "tag":
                            tag = found.group(1).decode()
                        else:
                            current = found.group(1).decode()
        return tag, current

    def do_POST(self):  # noqa: N802
        req_tag, req_mode = self._image_directives({}) if False else (None, None)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": {"message": "invalid json body"}})

        req_tag, req_mode = self._image_directives(request)
        if req_mode:
            current = req_mode
        else:
            current = self.headers.get("X-Fake-Mode") or mode()

        if current == "unauthorized":
            return self._send(401, {"error": {"message": "Incorrect API key provided"}})
        if current == "ratelimit":
            return self._send(429, {"error": {"message": "Rate limit exceeded"}})
        if current == "server_error":
            return self._send(500, {"error": {"message": "upstream exploded"}})
        if current == "slow":
            time.sleep(float(os.environ.get("FAKE_SLOW_SECONDS", "3")))
        if current == "reject_params":
            # Emulate providers that refuse logprobs/reasoning_effort once, then work.
            if "logprobs" in request or "reasoning_effort" in request:
                return self._send(400, {"error": {"message": "Unsupported parameter: logprobs"}})
            current = "ok"

        prompt_text = json.dumps(request.get("messages", []))
        if "expert fashion stylist" in prompt_text or "complete outfits" in prompt_text:
            content = "```json\n" + json.dumps(OUTFITS) + "\n```"
        elif "OUTPUT ONLY JSON" in prompt_text:
            if current == "garbage":
                content = "I'm sorry, I cannot analyze images."
            elif current == "truncated":
                content = '{"type": "shirt", "primary'
            elif current == "ood_type":
                content = json.dumps({**TAGS, "type": "tights"})
            else:
                content = json.dumps(self._tagged_payload(request, req_tag))
        else:
            content = '"Light blue cotton oxford shirt with a button-down collar."'

        if current == "garbage":
            return self._send(
                200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
            )

        payload = {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "logprobs": {
                            "content": [{"top_logprobs": [{"logprob": -0.05}, {"logprob": -0.4}]}]
                            * 12
                        },
                    },
                    "finish_reason": "length" if current == "truncated" else "stop",
                }
            ],
            "model": request.get("model", "fake"),
            "usage": {"total_tokens": 120},
        }
        self._send(200, payload)


def serve(port: int) -> None:
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8991)
    args = parser.parse_args()
    print(f"fake provider on http://127.0.0.1:{args.port}/v1 (FAKE_MODE={mode()})", flush=True)
    serve(args.port)
