"""Claude Code CLI provider.

Uses the locally installed `claude` binary in non-interactive mode, which draws
on a Claude Pro/Max subscription rather than separate API credits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider

#: Environment variables that must never reach the model subprocess.
SECRET_ENV_PREFIXES = ("SMTP_", "AWS_", "GITHUB_", "GH_", "OUTREACH_DB")
SECRET_ENV_NAMES = {"ANTHROPIC_API_KEY", "SENDER_EMAIL", "UNSUBSCRIBE_MAILTO"}

#: Flags that disable tool use, applied only when the installed CLI has them.
TOOL_DISABLING_FLAGS = (
    ("--disallowedTools", "Bash,Edit,Write,Read,WebFetch,WebSearch"),
    ("--allowedTools", ""),
)


@lru_cache(maxsize=4)
def cli_help(binary: str) -> str:
    """The installed CLI's own help text - flag names are checked, not assumed."""
    try:
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{result.stdout}\n{result.stderr}"


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
        # Personalization must not be able to touch the filesystem, the shell
        # or the network, so tool execution is switched off where the installed
        # CLI supports it (TDD s12/s24).
        help_text = cli_help(self.binary)
        for flag, value in TOOL_DISABLING_FLAGS:
            if flag in help_text:
                command += [flag, value]
                break

        try:
            # Argument array (no shell), scrubbed environment, throwaway working
            # directory: the subprocess sees none of the app's credentials and
            # no repository to wander into.
            with tempfile.TemporaryDirectory(prefix="outreach-llm-") as workdir:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.claude_cli_timeout,
                    check=False,
                    shell=False,
                    cwd=workdir,
                    env=self._safe_env(),
                )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.settings.claude_cli_timeout}s") from exc
        if completed.returncode != 0:
            raise LLMError(f"claude CLI exited {completed.returncode}: {completed.stderr.strip()[:400]}")
        return self._unwrap(completed.stdout)

    @staticmethod
    def _safe_env() -> dict[str, str]:
        """Drop application credentials before handing the environment over."""
        return {
            key: value
            for key, value in os.environ.items()
            if key not in SECRET_ENV_NAMES and not key.startswith(SECRET_ENV_PREFIXES)
        }

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
