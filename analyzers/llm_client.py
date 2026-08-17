"""
Клієнт локальної LLM через Ollama.

Використовує structured output (`format` = JSON Schema), тому модель фізично
не може повернути невалідний JSON або значення поза enum — це критично для
маленьких моделей на кшталт gemma3:4b, які часто ламають формат.

Налаштування через змінні оточення:
    OLLAMA_BASE_URL  — адреса сервера Ollama (default http://localhost:11434)
    LLM_MODEL        — назва моделі (default gemma3:4b)
    LLM_NUM_CTX      — розмір контексту в токенах (default 8192)
    LLM_TIMEOUT      — таймаут одного запиту в секундах (default 180)
    LLM_TEMPERATURE  — температура (default 0.2)
    LLM_RETRIES      — кількість повторів після помилки (default 2)
"""
import json
import logging
import os
import re
import time
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_NUM_CTX = 8192
DEFAULT_TIMEOUT = 180
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 1024
DEFAULT_RETRIES = 2

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMError(RuntimeError):
    """Помилка виклику локальної моделі."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning(f"{name}={raw!r} не число, використовую {default}")
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning(f"{name}={raw!r} не число, використовую {default}")
        return default


def strip_fences(raw: str) -> str:
    """Прибирає ```json обгортку, якщо модель її додала попри schema."""
    cleaned = raw.strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()


class OllamaClient:
    """
    Тонка обгортка над /api/chat. Мережевий шар інжектується через `session`,
    щоб тести могли підставити фейковий транспорт без запущеного Ollama.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        num_ctx: Optional[int] = None,
        timeout: Optional[int] = None,
        temperature: Optional[float] = None,
        retries: Optional[int] = None,
        session: Any = None,
    ):
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.num_ctx = num_ctx if num_ctx is not None else _env_int("LLM_NUM_CTX", DEFAULT_NUM_CTX)
        self.timeout = timeout if timeout is not None else _env_int("LLM_TIMEOUT", DEFAULT_TIMEOUT)
        self.temperature = (
            temperature if temperature is not None else _env_float("LLM_TEMPERATURE", DEFAULT_TEMPERATURE)
        )
        self.retries = retries if retries is not None else _env_int("LLM_RETRIES", DEFAULT_RETRIES)
        self.session = session or requests.Session()

    # ── Готовність ──────────────────────────────────────────────────────

    def health(self) -> dict:
        """Перевіряє доступність Ollama і наявність потрібної моделі."""
        info = {
            "reachable": False,
            "model_available": False,
            "model": self.model,
            "base_url": self.base_url,
            "models": [],
            "error": "",
        }
        try:
            resp = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            info["error"] = str(e)
            return info

        info["reachable"] = True
        names = [m.get("name", "") for m in data.get("models", [])]
        info["models"] = names
        # Ollama повертає теги як "gemma3:4b"; "gemma3" без тега means "gemma3:latest"
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        info["model_available"] = any(n == self.model or n == wanted for n in names)
        if not info["model_available"]:
            info["error"] = f"модель {self.model} не завантажена (ollama pull {self.model})"
        return info

    def is_ready(self) -> bool:
        h = self.health()
        return h["reachable"] and h["model_available"]

    # ── Виклики ─────────────────────────────────────────────────────────

    def _supports_system_role(self) -> bool:
        """Gemma-шаблони не мають окремої system-ролі — складаємо в один turn."""
        return not self.model.lower().startswith("gemma")

    def _build_messages(self, system: str, prompt: str) -> list[dict]:
        if system and self._supports_system_role():
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        content = f"{system}\n\n{prompt}" if system else prompt
        return [{"role": "user", "content": content}]

    def complete_json(
        self,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        """
        Один виклик моделі з примусовою JSON-схемою. Повертає розпарсений dict.
        Кидає LLMError, якщо всі спроби провалились.
        """
        payload = {
            "model": self.model,
            "messages": self._build_messages(system, prompt),
            "stream": False,
            "format": schema,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens,
            },
        }

        last_error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                delay = 2 ** attempt
                log.warning(f"Повтор виклику LLM через {delay}s (спроба {attempt + 1})")
                time.sleep(delay)
            try:
                started = time.monotonic()
                resp = self.session.post(
                    f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                body = resp.json()
                content = (body.get("message") or {}).get("content", "")
                elapsed = time.monotonic() - started
                log.info(f"LLM відповідь за {elapsed:.1f}s ({len(content)} символів)")
                if not content.strip():
                    last_error = "порожня відповідь моделі"
                    continue
                return json.loads(strip_fences(content))
            except json.JSONDecodeError as e:
                last_error = f"невалідний JSON: {e}"
                log.warning(last_error)
            except requests.RequestException as e:
                last_error = f"мережева помилка: {e}"
                log.warning(last_error)
            except Exception as e:
                last_error = str(e)
                log.warning(f"помилка виклику LLM: {e}")

        raise LLMError(last_error or "невідома помилка LLM")


_default_client: Optional[OllamaClient] = None


def get_client() -> OllamaClient:
    """Лінивий синглтон — щоб імпорт модуля не потребував запущеного Ollama."""
    global _default_client
    if _default_client is None:
        _default_client = OllamaClient()
    return _default_client


def set_client(client: Optional[OllamaClient]) -> None:
    """Підміна клієнта (тести, альтернативний бекенд)."""
    global _default_client
    _default_client = client
