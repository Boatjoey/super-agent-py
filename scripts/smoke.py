#!/usr/bin/env python3
"""End-to-end acceptance smoke for the installed console script.

This drives the real `super-agent` process inside a pseudo-terminal, against a
local HTTP server that speaks the OpenAI chat-completions streaming protocol. That
combination matters: the terminal is real (raw mode, window size, Ctrl+C as a
byte), the process boundary is real (argparse-free flag parsing, exit codes,
`.env` loading), and the only thing faked is the model provider — so nothing here
touches the network.

Run it with:

    uv run python scripts/smoke.py

Exit code 0 means every step passed; anything else prints the failing step.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pty
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The reply the fake provider streams back. Word-split so the transcript shows a
#: streaming message rather than one chunk.
REPLY = "smoke test reply"

#: How long any single interactive step may take before it is a failure.
STEP_TIMEOUT_SECONDS = 20.0


class FakeProvider(BaseHTTPRequestHandler):
    """A minimal OpenAI-compatible chat-completions endpoint that streams."""

    protocol_version = "HTTP/1.1"

    #: How long a request mentioning "slow" is held before any bytes are sent, so a
    #: turn can be cancelled while it is genuinely in flight.
    SLOW_SECONDS = 6.0

    #: Every prompt the provider was asked about, for diagnostics on failure.
    seen: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        prompts = json.dumps(body.get("messages", []))
        type(self).seen.append(prompts)
        if "slow" in prompts:
            time.sleep(self.SLOW_SECONDS)
        wants_stream = bool(body.get("stream"))
        if not wants_stream:
            self._send_json(
                {
                    "id": "chatcmpl-smoke",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model", "smoke"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": REPLY},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
                }
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index, word in enumerate(REPLY.split(" ")):
            piece = word if index == 0 else " " + word
            self._send_event(
                {
                    "id": "chatcmpl-smoke",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model", "smoke"),
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
            )
        self._send_event(
            {
                "id": "chatcmpl-smoke",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body.get("model", "smoke"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        self._send_event(
            {
                "id": "chatcmpl-smoke",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body.get("model", "smoke"),
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            }
        )
        self._write(b"data: [DONE]\n\n")
        self._write(b"")

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_event(self, payload: dict[str, object]) -> None:
        self._write(f"data: {json.dumps(payload)}\n\n".encode())

    def _write(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_home(root: Path, port: int) -> Path:
    """A HOME whose settings point at the fake provider and disable the sandbox."""
    home = root / "home"
    config = home / ".superagent"
    config.mkdir(parents=True)
    (config / "settings.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "providers": {
                    "deepseek": {
                        "base_url": f"http://127.0.0.1:{port}/v1",
                        "api_key": "sk-smoke",
                        "model": "smoke-model",
                    }
                },
                # The default is `strict`, which is Linux-only. Off is the
                # documented opt-out and is what the manual acceptance procedure
                # uses too.
                "sandbox": {"mode": "off"},
                "permissions": {"mode": "ask"},
                "telemetry": {"log_path": ""},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return home


class Terminal:
    """A `super-agent` process attached to a pseudo-terminal."""

    def __init__(self, home: Path, workspace: Path, columns: int = 100, rows: int = 30) -> None:
        self.master, slave = pty.openpty()
        set_window_size(slave, rows, columns)
        environment = {
            **os.environ,
            "HOME": str(home),
            "TERM": "xterm-256color",
            "COLUMNS": str(columns),
            "LINES": str(rows),
            "PYTHONUNBUFFERED": "1",
        }
        environment.pop("YOLO", None)
        environment.pop("NO_TOOLS", None)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "super_agent"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=workspace,
            env=environment,
            start_new_session=True,
        )
        os.close(slave)
        self.output = bytearray()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.output.extend(chunk)

    def text(self) -> str:
        return bytes(self.output).decode("utf-8", errors="replace")

    def send(self, payload: str | bytes) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        os.write(self.master, data)

    def resize(self, columns: int, rows: int) -> None:
        set_window_size(self.master, rows, columns)
        os.kill(self.process.pid, signal.SIGWINCH)

    def wait_for(self, needle: str, timeout: float = STEP_TIMEOUT_SECONDS) -> str:
        """Wait until ``needle`` appears, returning everything read so far."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return self.text()
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {needle!r}\n--- output ---\n{self.text()}")

    def wait_until_idle(self, timeout: float = STEP_TIMEOUT_SECONDS) -> None:
        """Wait until no turn is running.

        The interface stops advertising a cancel key once the turn has finished,
        which is the externally visible signal that Enter will submit rather than
        steer. A timeout is not fatal here: the caller's assertion describes what
        actually happened.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "esc/ctrl+u" in self.text()[-4000:]:
                return
            time.sleep(0.1)
        time.sleep(0.5)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        with contextlib.suppress(OSError):
            os.close(self.master)


def set_window_size(fd: int, rows: int, columns: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def visible_lines(text: str) -> list[str]:
    """The rendered lines, with ANSI escapes removed."""
    import re

    stripped = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    stripped = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stripped)
    return [line.rstrip() for line in stripped.splitlines()]


def cell_length(line: str) -> int:
    """Display width, via the same measurement the TUI uses."""
    from rich.cells import cell_len

    return cell_len(line)


def run_cli_checks(binary: list[str], workspace: Path, home: Path) -> None:
    """The non-interactive acceptance steps: usage, exit codes, error text."""
    environment = {**os.environ, "HOME": str(home), "COLUMNS": "100", "LINES": "30"}
    environment.pop("YOLO", None)
    environment.pop("NO_TOOLS", None)

    usage = subprocess.run([*binary, "-h"], cwd=workspace, env=environment, capture_output=True, text=True)
    assert usage.returncode == 0, f"-h exit code {usage.returncode}, want 0"
    assert "approval-mode" in usage.stderr, f"-h usage missing flags: {usage.stderr!r}"
    assert "(default true)" not in usage.stderr, f"usage claims a false flag defaults true: {usage.stderr!r}"

    unknown = subprocess.run([*binary, "--nope"], cwd=workspace, env=environment, capture_output=True, text=True)
    assert unknown.returncode == 2, f"unknown flag exit code {unknown.returncode}, want 2"
    assert "flag provided but not defined: -nope" in unknown.stderr, unknown.stderr

    bad_mode = subprocess.run(
        [*binary, "--approval-mode", "bogus"], cwd=workspace, env=environment, capture_output=True, text=True
    )
    assert bad_mode.returncode == 1, f"bad mode exit code {bad_mode.returncode}, want 1"
    assert "invalid permission mode: bogus" in bad_mode.stderr, bad_mode.stderr

    conflicting = subprocess.run(
        [*binary, "--yolo", "--approval-mode", "plan"],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert conflicting.returncode == 1, f"conflicting flags exit code {conflicting.returncode}, want 1"
    assert "mutually exclusive" in conflicting.stderr, conflicting.stderr

    no_key = tempfile.mkdtemp()
    try:
        missing = subprocess.run(
            binary, cwd=workspace, env={**environment, "HOME": no_key}, capture_output=True, text=True
        )
        assert missing.returncode == 1, f"missing credential exit code {missing.returncode}, want 1"
        assert "has no api_key" in missing.stderr, missing.stderr
    finally:
        shutil.rmtree(no_key, ignore_errors=True)


def run_interactive_checks(home: Path, workspace: Path) -> None:
    """The interactive steps: prompt, reply, resize, cancel, quit."""
    terminal = Terminal(home, workspace)
    try:
        terminal.wait_for("Super Agent")

        terminal.send("hello there\r")
        terminal.wait_for(REPLY)

        # A resize mid-session must not produce a line wider than the terminal.
        #
        # Only the output written *after* the resize is examined. The stream
        # contains every earlier frame, and those were legitimately rendered at the
        # old width; scanning the whole history would fail on correct output. The
        # TUI's own tests assert the invariant on a full render, which is the
        # stronger check — this one is here to prove a live SIGWINCH is handled at
        # all, and that what it draws next fits.
        mark = len(terminal.output)
        terminal.resize(60, 20)
        time.sleep(1.5)
        redrawn = bytes(terminal.output[mark:]).decode("utf-8", errors="replace")
        over = [line for line in visible_lines(redrawn) if cell_length(line) > 60]
        assert not over, "resize produced an over-wide line:\n" + "\n".join(over)
        assert "smoke-model" in redrawn, "resize produced no redraw; the width check proved nothing"

        # Ctrl+C while a turn is running cancels the turn; it only quits when
        # nothing is in flight, which is why the slow prompt comes first.
        terminal.send("slow please\r")
        time.sleep(2.0)
        terminal.send("\x03")
        time.sleep(1.0)
        assert terminal.process.poll() is None, (
            f"Ctrl+C exited the process instead of cancelling (returncode {terminal.process.returncode})"
            f"\n--- prompts seen ---\n{FakeProvider.seen!r}"
            "\n--- last output ---\n" + terminal.text()[-2000:]
        )

        # The loop must still be usable after a cancel.
        terminal.send("hello again\r")
        terminal.wait_for(REPLY, timeout=STEP_TIMEOUT_SECONDS)
        assert terminal.process.poll() is None, "the process died after cancel and resume"

        # Let the turn finish before submitting a command: while a turn is still
        # running, Enter steers it instead of submitting, so a command would be
        # sent to the model as a follow-up prompt rather than run as one.
        terminal.wait_until_idle()

        # The conversation-management commands must run against a live session and
        # leave it usable. Their transcript surgery is asserted precisely by the
        # session tests; what this adds is that the command path itself works end
        # to end through the real interface.
        for command in ("/clear", "/compact", "/undo"):
            mark = len(terminal.output)
            terminal.send(command + "\r")
            terminal.wait_until_idle()
            assert terminal.process.poll() is None, f"{command} terminated the process"
            assert terminal.output[mark:], f"{command} produced no output at all"

        terminal.send("/quit\r")
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and terminal.process.poll() is None:
            time.sleep(0.05)
        assert terminal.process.poll() is not None, (
            "process did not exit after /quit\n--- last output ---\n" + terminal.text()[-2000:]
        )
    finally:
        terminal.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--columns", type=int, default=100)
    parser.add_argument("--rows", type=int, default=30)
    arguments = parser.parse_args()
    del arguments  # kept for symmetry with the TUI's own options

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    root = Path(tempfile.mkdtemp(prefix="super-agent-smoke-"))
    workspace = root / "workspace"
    workspace.mkdir()
    try:
        home = write_home(root, port)
        binary = [sys.executable, "-m", "super_agent"]
        run_cli_checks(binary, workspace, home)
        print("ok   flags, exit codes, and error text")
        run_interactive_checks(home, workspace)
        print("ok   prompt, streaming reply, resize, mid-turn cancel, /clear, /compact, /undo, /quit")
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print("smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
