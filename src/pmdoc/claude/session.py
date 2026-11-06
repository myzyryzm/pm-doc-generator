"""
session.py — run one headless Claude Code session and capture its final message.

Every agent in this tool is a `claude -p` subprocess: a mapping pass, a story
trace, the synthesis, the prompt resolver. `--output-format json` returns an
envelope with the final assistant message plus the session id, and the session
id is what makes the cheap repair path possible (`claude --resume <id>` with a
"reprint that properly" nudge, instead of re-running the investigation).

Two facts shape everything downstream:

  * Only the FINAL message is captured. Anything the agent printed earlier is
    gone, so the caller must validate what comes back (see claude.repair).
  * The child is tracked, so terminate_sessions() can stop every live session on
    Ctrl-C instead of orphaning one that keeps running (and billing).
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from pmdoc.claude.envelope import parse_envelope
from pmdoc.config import (
    AGENT_TOOLS, ALLOWED_TOOLS, CLAUDE_TIMEOUT_S, DEFAULT_EFFORT, REPAIR_TIMEOUT_S,
)


@dataclass
class ClaudeRun:
    """One headless session's outcome, parsed from the json envelope."""
    ok: bool
    text: str = ""
    session_id: str | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    stop_reason: str | None = None
    subtype: str | None = None
    is_error: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "session_id": self.session_id, "num_turns": self.num_turns,
            "cost_usd": self.cost_usd, "duration_ms": self.duration_ms,
            "stop_reason": self.stop_reason, "subtype": self.subtype,
            "is_error": self.is_error, "error": self.error,
            "result_chars": len(self.text),
        }


# ── Process tracking ───────────────────────────────────────────────────────

_live_sessions: set[subprocess.Popen] = set()
_live_sessions_lock = threading.Lock()


def _run_tracked(cmd: list[str], *, cwd, timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True, text=True, timeout=...) — but the child
    is registered so terminate_sessions() can stop it. Same exceptions as run()."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    with _live_sessions_lock:
        _live_sessions.add(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        with _live_sessions_lock:
            _live_sessions.discard(proc)


def terminate_sessions() -> int:
    """Terminate every `claude` session this process has running. Returns how many."""
    with _live_sessions_lock:
        procs = list(_live_sessions)
    for proc in procs:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    return len(procs)


# ── Invocation ─────────────────────────────────────────────────────────────

def _tool_flags(tools: str | None, allowed_tools: str | None) -> list[str]:
    flags = ["--tools", tools if tools is not None else ""]
    if allowed_tools:
        flags += ["--allowedTools", allowed_tools]
    # The user's MCP servers have nothing to offer a read-only code trace, and
    # each one costs startup time and context in every headless session.
    flags.append("--strict-mcp-config")
    return flags


def invoke(prompt_text: str, *, cwd: Path | str, log_path: Path, model: str, max_turns: int,
           tools: str | None = AGENT_TOOLS, allowed_tools: str | None = ALLOWED_TOOLS,
           effort: str | None = DEFAULT_EFFORT, timeout: float = CLAUDE_TIMEOUT_S,
           log_mode: str = "w") -> ClaudeRun:
    """Run one headless session (read-only by default) and parse the json envelope.

    `tools=""` gives the session no tools at all — used by the resolver, which
    only reasons over the action index in its prompt."""
    cmd = ["claude", "-p", prompt_text, *_tool_flags(tools, allowed_tools),
           "--model", model, "--max-turns", str(max_turns), "--output-format", "json"]
    if effort:
        cmd += ["--effort", effort]

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, log_mode, encoding="utf-8") as logf:
        logf.write(f"$ (cwd={cwd})\nclaude -p [prompt omitted] {' '.join(cmd[3:])}\n\n")
        logf.flush()
        try:
            result = _run_tracked(cmd, cwd=cwd, timeout=timeout)
        except FileNotFoundError:
            msg = "`claude` CLI not found on PATH. Install Claude Code."
            logf.write(f"ERROR: {msg}\n")
            return ClaudeRun(False, error=msg)
        except subprocess.TimeoutExpired:
            msg = f"timed out after {timeout}s"
            logf.write(f"ERROR: {msg} — no session id, cannot resume.\n")
            return ClaudeRun(False, error=msg)

        if result.stderr:
            logf.write("----- STDERR -----\n" + result.stderr + "\n")
        if result.returncode != 0:
            logf.write(f"ERROR: claude exited {result.returncode}\n")
            if result.stdout:
                logf.write(result.stdout[:4000] + "\n")
            return ClaudeRun(False, error=f"claude exited {result.returncode}")

        run = parse_envelope(result.stdout or "", ClaudeRun, logf)
        _log_run_meta(logf, run, cwd=cwd, model=model, max_turns=max_turns,
                      effort=effort, timeout=timeout)
        logf.write("\n----- RESULT -----\n" + run.text + "\n")

        if not run.text.strip():
            logf.write("\nERROR: empty output from agent.\n")
            return ClaudeRun(False, session_id=run.session_id, error="empty output from agent")
        return run


def resume(session_id: str, nudge: str, *, cwd: Path | str, log_path: Path, model: str,
           tools: str | None = AGENT_TOOLS, allowed_tools: str | None = ALLOWED_TOOLS,
           timeout: float = REPAIR_TIMEOUT_S) -> ClaudeRun:
    """Ask an existing session to reprint its deliverable.

    It must run from the same cwd (sessions are scoped per project directory),
    and `--max-turns` is deliberately tiny so a repair can never silently
    relaunch a full investigation. No `--effort` either — re-rendering finished
    work needs no extra reasoning budget."""
    cmd = ["claude", "--resume", session_id, "-p", nudge, *_tool_flags(tools, allowed_tools),
           "--model", model, "--max-turns", "5", "--output-format", "json"]

    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n----- REPAIR: claude --resume {session_id} -----\n{nudge}\n\n")
        logf.flush()
        try:
            result = _run_tracked(cmd, cwd=cwd, timeout=timeout)
        except FileNotFoundError:
            return ClaudeRun(False, error="`claude` CLI not found on PATH")
        except subprocess.TimeoutExpired:
            msg = f"repair timed out after {timeout}s"
            logf.write(f"ERROR: {msg}\n")
            return ClaudeRun(False, session_id=session_id, error=msg)

        if result.stderr:
            logf.write("----- STDERR -----\n" + result.stderr + "\n")
        if result.returncode != 0:
            return ClaudeRun(False, session_id=session_id,
                             error=f"resume exited {result.returncode}")

        run = parse_envelope(result.stdout or "", ClaudeRun, logf)
        if not run.session_id:  # keep the id so a later repair still works
            run.session_id = session_id
        logf.write("\n----- REPAIR RESULT -----\n" + run.text + "\n")
        if not run.text.strip():
            return ClaudeRun(False, session_id=run.session_id, error="empty repair output")
        return run


def _log_run_meta(logf, run: ClaudeRun, *, cwd, model, max_turns, effort, timeout) -> None:
    duration = f"{run.duration_ms / 1000:.1f}s" if run.duration_ms else "?"
    cost = f"{run.cost_usd:.2f}" if run.cost_usd is not None else "?"
    logf.write("\n----- RUN META -----\n")
    logf.write(f"cwd:          {cwd}\n")
    logf.write(f"session_id:   {run.session_id or '(none)'}")
    if run.session_id:
        logf.write(f"   <- inspect with: cd {cwd} && claude --resume {run.session_id}")
    logf.write("\n")
    logf.write(f"model:        {model}   effort: {effort or '-'}   max_turns: {max_turns}\n")
    logf.write(f"num_turns:    {run.num_turns if run.num_turns is not None else '?'}\n")
    logf.write(f"duration:     {duration}   (timeout {timeout}s)\n")
    logf.write(f"cost_usd:     {cost}\n")
    logf.write(f"stop_reason:  {run.stop_reason or '?'}   subtype: {run.subtype or '?'}   "
               f"is_error: {run.is_error}\n")
    logf.write(f"result:       {len(run.text)} chars / {len(run.text.splitlines())} lines\n")
