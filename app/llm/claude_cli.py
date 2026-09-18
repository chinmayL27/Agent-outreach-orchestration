"""Claude Code CLI provider.

Uses the locally installed `claude` binary in non-interactive mode, which draws
on a Claude Pro/Max subscription rather than separate API credits.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider


class ClaudeCLIProvider(LLMProvider):
    name = "claude_cli"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.binary = self.settings.claude_cli_bin

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def complete(self, prompt: str) -> str:
        if not self.available():
            raise LLMError(
                f"`{self.binary}` not found on PATH. Install Claude Code or set "
                "LLM_PROVIDER=template."
            )
        command = [self.binary, "-p", prompt, "--output-format", "json"]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.settings.claude_cli_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.settings.claude_cli_timeout}s") from exc
        if completed.returncode != 0:
            raise LLMError(f"claude CLI exited {completed.returncode}: {completed.stderr.strip()[:400]}")
        return self._unwrap(completed.stdout)

    @staticmethod
    def _unwrap(stdout: str) -> str:
        """`--output-format json` wraps the answer in an envelope."""
        stdout = stdout.strip()
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
        if isinstance(payload, dict):
            if payload.get("is_error"):
                raise LLMError(f"claude CLI reported an error: {str(payload.get('result'))[:300]}")
            result = payload.get("result")
            if isinstance(result, str):
                return result
        return stdout
