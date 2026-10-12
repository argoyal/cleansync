"""A pretend OpenAI-style model for development and the browser test. Never used in production.

    DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 python -m app.devtools.fake_model [--port 11999] [--host 127.0.0.1]

Like the pretend accounts, it refuses to start without DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1.

Point a connection at http://127.0.0.1:11999/v1 with the model "fake-sorter". It answers the
three questions CleanSync asks, without any real AI:
- sorting (and the Settings Test button): the first rule whose name is in the mail's subject,
  else the first rule with a word of its description in the subject, else 0;
- a sentence to a rule: the sentence as the description, and Archive as the action;
- picked mails to a name and description: from their first subject and their senders.
"""

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.rules import chooser, drafting

MODEL = "fake-sorter"
RULE_LINE = re.compile(r"^(\d+)\. ([^:\n]+): ?(.*)$", re.MULTILINE)
SUBJECT = re.compile(r"^Subject: (.*)$", re.MULTILINE)
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
FROM_ADDRESS = re.compile(r"^From: .*?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.MULTILINE)
SHORT_WORDS = {"from", "with", "that", "this", "mail", "email", "they", "your", "have"}


def _stems(text: str) -> set[str]:
    """Lower-case words of four letters or more, with a plural "s" dropped."""
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        if len(w) >= 4 and w not in SHORT_WORDS:
            out.add(w)
    return out


def _choose(user: str) -> dict:
    rules_part, _, email_part = user.partition("<<<EMAIL")
    m = SUBJECT.search(email_part)
    subject = _stems(m.group(1) if m else "")
    rules = [(int(n), name.strip(), desc) for n, name, desc in RULE_LINE.findall(rules_part)]
    rules = [r for r in rules if r[0] != 0]
    for n, name, _desc in rules:
        words = _stems(name)
        if words and words <= subject:
            return {"rule": n, "why": f"subject mentions {name}"}
    for n, name, desc in rules:
        if _stems(desc) & subject:
            return {"rule": n, "why": f"subject fits {name}"}
    return {"rule": 0, "why": "nothing matched"}


def _draft(user: str) -> dict:
    sentence = user.removeprefix("Sentence: ").strip()
    words = sentence.split()
    name = " ".join(words[:4]) if len(words) >= 2 else f"{sentence or 'New'} mail"
    lists = bool(re.search(r"newsletter|mailing list", sentence, re.IGNORECASE))
    return {
        "name": name,
        "description": sentence,
        "actions": ["archive"],
        "label": None,
        "senders_in": ADDRESS.findall(sentence),
        "senders_not_in": [],
        "subject_contains": [],
        "list_unsubscribe": True if lists else None,
    }


def _describe(user: str) -> dict:
    m = SUBJECT.search(user)
    subject = (m.group(1) if m else "").strip() or "these mails"
    senders = list(dict.fromkeys(FROM_ADDRESS.findall(user)))
    name = " ".join(subject.split()[:3]) or "Picked mail"
    if len(name.split()) < 2:
        name = f"{name} mail"
    who = f" from {', '.join(senders)}" if senders else ""
    return {"name": name, "description": f'Mail like "{subject}"{who}.'}


def answer(system: str, user: str) -> dict:
    if system == drafting.DRAFT_SYSTEM:
        return _draft(user)
    if system == drafting.DESCRIBE_SYSTEM:
        return _describe(user)
    if system == chooser.SYSTEM:
        return _choose(user)
    return {"rule": 0, "why": "unknown question"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: dict, status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send({"data": [{"id": MODEL}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send({"error": "not JSON"}, 400)
            return
        msgs = body.get("messages") or [{}]
        system = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
        user = msgs[-1].get("content", "")
        content = json.dumps(answer(system, user))
        self._send({"model": MODEL, "choices": [{"message": {"content": content}}]})

    def log_message(self, *args):
        pass


def make_server(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=11999)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    allowed = os.environ.get("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "").strip().lower()
    if allowed not in ("1", "true", "yes", "on"):
        print(
            "Refusing: the pretend model is for development and tests only. "
            "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to start it.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    make_server(args.port, args.host).serve_forever()


if __name__ == "__main__":
    main()
