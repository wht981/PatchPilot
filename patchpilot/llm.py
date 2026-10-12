"""Small model interface and a dependency-free OpenAI-compatible adapter."""

from __future__ import annotations

import json
import http.client
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol
from urllib import error, parse, request

from patchpilot.security import SecurityPolicy


class LLMError(Exception):
    """A model request failed without changing the workspace."""


@dataclass(frozen=True)
class LLMConfig:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)
    api_key_env: str = "OPENAI_API_KEY"
    timeout: float = 60.0
    max_tokens: int = 8192
    token_parameter: str = "max_completion_tokens"
    json_mode: bool = True
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Set PATCHPILOT_MODEL or pass --model for the LLM engine.")
        url = parse.urlsplit(self.base_url)
        try:
            url.port
        except ValueError:
            raise ValueError("LLM base URL has an invalid port.") from None
        local = url.hostname in ("localhost", "127.0.0.1", "::1")
        if (url.scheme not in ("http", "https") or not url.hostname
                or (url.scheme == "http" and not local)
                or any(c.isspace() for c in self.base_url)
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("LLM base URL must use HTTPS (HTTP is allowed on localhost only), without credentials, query or fragment.")
        if not local and not self.api_key:
            raise ValueError(f"Set {self.api_key_env} for the configured LLM endpoint.")
        if any(c in self.api_key for c in "\r\n"):
            raise ValueError("API key must not contain newlines.")
        if not math.isfinite(self.timeout) or self.timeout <= 0 or self.max_tokens <= 0:
            raise ValueError("LLM timeout and max tokens must be positive and finite.")
        if self.token_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("LLM token parameter must be max_tokens or max_completion_tokens.")
        if not 0 <= self.max_retries <= 3:
            raise ValueError("LLM max retries must be between 0 and 3.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise ValueError("API key environment variable must be a valid identifier.")

    @classmethod
    def from_env(cls, *, model: Optional[str] = None,
                 base_url: Optional[str] = None, api_key_env: Optional[str] = None,
                 timeout: Optional[float] = None, max_tokens: Optional[int] = None,
                 token_parameter: Optional[str] = None,
                 json_mode: Optional[bool] = None) -> "LLMConfig":
        key_env = api_key_env or os.environ.get("PATCHPILOT_API_KEY_ENV", "OPENAI_API_KEY")
        return cls(
            model=model if model is not None else os.environ.get("PATCHPILOT_MODEL", ""),
            base_url=base_url if base_url is not None else os.environ.get("PATCHPILOT_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ.get(key_env, ""), api_key_env=key_env,
            timeout=timeout if timeout is not None else float(os.environ.get("PATCHPILOT_LLM_TIMEOUT", "60")),
            max_tokens=max_tokens if max_tokens is not None else int(os.environ.get("PATCHPILOT_MAX_TOKENS", "8192")),
            token_parameter=token_parameter or os.environ.get("PATCHPILOT_TOKEN_PARAMETER", "max_completion_tokens"),
            json_mode=json_mode if json_mode is not None else os.environ.get("PATCHPILOT_JSON_MODE", "1") != "0",
        )


@dataclass(frozen=True)
class ModelReply:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


class ModelClient(Protocol):
    """The agent depends on this interface; tests use a scripted adapter."""

    model: str

    def complete(self, messages: List[Dict[str, str]]) -> ModelReply: ...


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward authorization to another endpoint.


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig,
                 policy: Optional[SecurityPolicy] = None) -> None:
        self.config = config
        self.model = config.model
        self.policy = policy or SecurityPolicy(known_secrets=[config.api_key])
        self._opener = request.build_opener(_NoRedirect())

    def complete(self, messages: List[Dict[str, str]]) -> ModelReply:
        payload = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": self.policy.mask_secrets(m["content"])} for m in messages],
            self.config.token_parameter: self.config.max_tokens,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "User-Agent": "PatchPilot/0.4"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        req = request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
        )
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._opener.open(req, timeout=self.config.timeout) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise LLMError("Model response exceeded the 2 MB limit.")
                return self._parse_reply(raw)
            except error.HTTPError as exc:
                exc.close()
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.config.max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                # Provider bodies can echo credentials, prompts or HTML; don't log them.
                raise LLMError(f"LLM endpoint returned HTTP {exc.code}; check endpoint, model and credentials.") from None
            except (error.URLError, TimeoutError, OSError, http.client.HTTPException):
                if attempt < self.config.max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise LLMError("LLM endpoint could not be reached within the configured request limits.") from None
        raise LLMError("LLM request retries exhausted.")

    def _parse_reply(self, raw: bytes) -> ModelReply:
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice["message"]
            if not isinstance(choice, dict) or not isinstance(message, dict):
                raise ValueError("invalid message")
            if message.get("refusal") or choice.get("finish_reason") != "stop":
                raise LLMError("Model refused or returned an incomplete response; no patch was applied.")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError("Model returned no text response.")
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                raise ValueError("invalid usage")
            counts = [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)]
            if any(type(n) is not int or n < 0 for n in counts):
                raise ValueError("invalid token usage")
            return ModelReply(content, counts[0], counts[1])
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
            raise LLMError("LLM endpoint returned an invalid Chat Completions response.") from None
