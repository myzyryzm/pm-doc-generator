"""
envelope.py — read what a headless session printed.

`parse_envelope` turns `claude -p --output-format json` stdout into a ClaudeRun.
`extract_json_block` pulls the deliverable out of a session whose final message
is supposed to be a single fenced JSON block (the mapping and resolve stages).
"""

from __future__ import annotations

import json
import re


class JsonBlockError(ValueError):
    """The text holds no parseable JSON object."""


def parse_envelope(stdout: str, run_cls, logf=None):
    """Parse the json envelope, degrading to raw text if it is not json.

    Degrading is not fatal: the output survives, only the session id (and with
    it the ability to repair) is lost. Better to proceed than discard a trace."""
    raw = stdout.strip()
    if not raw:
        return run_cls(True, text="")
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        if logf:
            logf.write("WARNING: could not parse json envelope — treating stdout as raw text.\n")
        return run_cls(True, text=raw)
    if not isinstance(env, dict):
        return run_cls(True, text=raw)
    return run_cls(
        ok=True,
        text=env.get("result") or "",
        session_id=env.get("session_id"),
        num_turns=env.get("num_turns"),
        cost_usd=env.get("total_cost_usd"),
        duration_ms=env.get("duration_ms"),
        stop_reason=env.get("stop_reason"),
        subtype=env.get("subtype"),
        is_error=bool(env.get("is_error")),
    )


_FENCE_RE = re.compile(r"```(?:json|JSON)?[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)


def extract_json_block(text: str) -> dict:
    """Return the JSON object in an agent's final message.

    Prefers the LAST fenced block that parses to an object (an agent that
    reprinted after a correction put the good one last). Falls back to the whole
    message when it is bare JSON, then to the outermost {...} span."""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)]
    errors = []
    for body in reversed(candidates):
        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            errors.append(f"fenced block: {e}")
            continue
        if isinstance(obj, dict):
            return obj
        errors.append("fenced block is not a JSON object")

    stripped = text.strip()
    for body in (stripped, stripped[stripped.find("{"):stripped.rfind("}") + 1]
                 if "{" in stripped else ""):
        if not body:
            continue
        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            errors.append(f"bare text: {e}")
            continue
        if isinstance(obj, dict):
            return obj
    if not candidates and "{" not in stripped:
        raise JsonBlockError("no ```json block and no JSON object in the message")
    raise JsonBlockError("; ".join(errors[:3]) or "no JSON object found")
