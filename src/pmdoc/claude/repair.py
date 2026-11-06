"""
repair.py — run a session, then keep asking it to reprint until its output validates.

Repair happens via `--resume` on the same session, so the agent still has its
whole investigation in context and only has to re-render it: vastly cheaper
than re-running the trace, and it picks up any corrections the agent made after
it first printed.

Two outcomes, deliberately different:
  * a formatting slip (missing sentinel, preamble, an addendum instead of the
    document, malformed JSON) is repairable — up to MAX_REPAIR_ATTEMPTS;
  * a refusal is not. Nudging an agent that said it lacks the evidence is
    pressure to fabricate, so the loop stops immediately.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pmdoc.claude import session
from pmdoc.claude.session import ClaudeRun
from pmdoc.config import (
    AGENT_TOOLS, ALLOWED_TOOLS, CLAUDE_TIMEOUT_S, DEFAULT_EFFORT, MAX_REPAIR_ATTEMPTS,
    TECHNICAL_SENTINEL,
)
from pmdoc.console import emit
from pmdoc.validate import ValidationResult

GRAY = "\033[90m"; YELLOW = "\033[33m"; RED = "\033[31m"; RESET = "\033[0m"

ValidateFn = Callable[[str], ValidationResult]
NudgeFn = Callable[[list[str], int], str]


def run_with_validation(prompt_text: str, *, cwd, log_path: Path, model: str, max_turns: int,
                        validate_fn: ValidateFn, nudge_fn: NudgeFn,
                        tools: str | None = AGENT_TOOLS,
                        allowed_tools: str | None = ALLOWED_TOOLS,
                        effort: str | None = DEFAULT_EFFORT,
                        timeout: float = CLAUDE_TIMEOUT_S,
                        ) -> tuple[ClaudeRun, str | None, ValidationResult]:
    """Returns (run, valid_text_or_None, last_validation_result)."""
    run = session.invoke(prompt_text, cwd=cwd, log_path=log_path, model=model,
                         max_turns=max_turns, tools=tools, allowed_tools=allowed_tools,
                         effort=effort, timeout=timeout)
    if not run.ok:
        return run, None, ValidationResult(False, [run.error or "session failed"],
                                           repairable=False)

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        result = validate_fn(run.text)
        _log_validation(log_path, attempt, result)

        if result.ok:
            return run, run.text, result

        if not result.repairable:
            emit(f"      {RED}✗ agent declined to produce the deliverable{RESET}")
            for err in result.errors:
                emit(f"        {GRAY}{err}{RESET}")
            return run, None, result

        if attempt >= MAX_REPAIR_ATTEMPTS:
            return run, None, result

        if not run.session_id:
            return run, None, ValidationResult(
                False, result.errors + ["no session id available — cannot repair via --resume"],
                repairable=False)

        emit(f"      {YELLOW}⟳ output invalid — reprinting via --resume "
             f"(attempt {attempt + 1}/{MAX_REPAIR_ATTEMPTS}){RESET}")
        for err in result.errors:
            emit(f"        {GRAY}{err}{RESET}")

        run = session.resume(run.session_id, nudge_fn(result.errors, attempt + 1), cwd=cwd,
                             log_path=log_path, model=model, tools=tools,
                             allowed_tools=allowed_tools)
        if not run.ok:
            return run, None, ValidationResult(False, [run.error or "resume failed"],
                                               repairable=False)

    return run, None, ValidationResult(False, ["exhausted repair attempts"])


# ── Nudges ─────────────────────────────────────────────────────────────────

_FINAL_WARNING = ("This is the final attempt. If this message is not the deliverable, the "
                  "entire investigation is discarded.\n\n")


def build_doc_nudge(errors: list[str], attempt: int, *, expect_sentinel: bool,
                    h1_line: str) -> str:
    """Tell the session exactly what was wrong and ask for a full markdown reprint."""
    problems = "\n".join(f"- {e}" for e in errors)
    if expect_sentinel:
        shape = (f"1. The complete product doc, beginning with the line `{h1_line}`\n"
                 f"2. A line containing exactly `{TECHNICAL_SENTINEL}`\n"
                 "3. The complete Technical Appendix")
    else:
        shape = f"The complete document, beginning with the line `{h1_line}`"
    return (
        f"{_FINAL_WARNING if attempt > 1 else ''}"
        "Your previous message was not accepted as this session's deliverable.\n\n"
        f"Problems detected:\n{problems}\n\n"
        "Reprint the COMPLETE deliverable now. It must be your entire next message:\n\n"
        f"{shape}\n\n"
        "Rules for this message:\n"
        "- Do NOT investigate further. Do NOT use tools. Everything you need is already in "
        "this conversation.\n"
        "- Reprint the document(s) IN FULL, with every correction, retraction and later "
        "finding from this conversation already folded into the prose. Do not emit a diff, "
        "a correction note, an addendum, a changelog, or a summary of what changed.\n"
        "- No preamble and no closing commentary. The first character of your message must "
        "be `#`, and the last line must be the final line of the document."
    )


def build_json_nudge(errors: list[str], attempt: int) -> str:
    """Ask a JSON-producing session (mapping, resolve) for a corrected full reprint."""
    problems = "\n".join(f"- {e}" for e in errors[:25])
    more = f"\n- …and {len(errors) - 25} more" if len(errors) > 25 else ""
    return (
        f"{_FINAL_WARNING if attempt > 1 else ''}"
        "Your previous message was not accepted.\n\n"
        f"Problems detected:\n{problems}{more}\n\n"
        "Reprint the COMPLETE JSON now, with every problem fixed, as a single ```json fenced "
        "block. It must be your entire next message.\n\n"
        "Rules for this message:\n"
        "- Do NOT investigate further. Do NOT use tools.\n"
        "- Include EVERY entry, not just the corrected ones. Do not emit a diff or a note.\n"
        "- No prose before or after the fenced block."
    )


# ── Forensics ──────────────────────────────────────────────────────────────

def reject(log_path: Path, result: ValidationResult, run: ClaudeRun, *, doc_path: Path,
           run_path: Path | None = None) -> None:
    """Preserve rejected output under an .INVALID name — available for debugging,
    never mistakable for (or collectable as) a real document."""
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write("\n----- REJECTED -----\n")
        for err in result.errors:
            logf.write(f"  - {err}\n")
        logf.write(f"  kept for inspection: {doc_path}\n")

    if run.text.strip():
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(run.text, encoding="utf-8")
    if run_path is not None:
        payload = run.as_dict()
        payload["errors"] = result.errors
        payload["repairable"] = result.repairable
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    emit(f"      {RED}✗ output rejected — nothing written under the real name{RESET}")
    emit(f"      {GRAY}  kept for inspection: {doc_path}{RESET}")
    if run.session_id:
        emit(f"      {GRAY}  inspect the session: claude --resume {run.session_id}{RESET}")


def _log_validation(log_path: Path, attempt: int, result: ValidationResult) -> None:
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n----- VALIDATION attempt {attempt + 1} -----\n{result.summary()}\n")
        for err in result.errors:
            logf.write(f"  - {err}\n")
