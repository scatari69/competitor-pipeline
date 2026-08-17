import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzers.llm_client import OllamaClient  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """
    Транспорт замість requests.Session — щоб тести не потребували Ollama.

    `replies` — список того, що модель «повертає» на послідовні виклики
    /api/chat: або dict (буде серіалізований у JSON), або рядок (як є),
    або Exception (буде піднятий).
    """

    def __init__(self, replies=None, models=("gemma3:4b",), reachable=True):
        self.replies = list(replies or [])
        self.models = list(models)
        self.reachable = reachable
        self.chat_calls = []
        self.tag_calls = 0

    def get(self, url, timeout=None):
        self.tag_calls += 1
        if not self.reachable:
            raise RuntimeError("connection refused")
        return FakeResponse({"models": [{"name": m} for m in self.models]})

    def post(self, url, json=None, timeout=None):
        self.chat_calls.append(json)
        if not self.replies:
            raise AssertionError(f"немає підготовленої відповіді на виклик #{len(self.chat_calls)}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else _dumps(reply)
        return FakeResponse({"message": {"role": "assistant", "content": content}, "done": True})


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture
def make_client():
    def _make(replies=None, models=("gemma3:4b",), reachable=True, **kwargs):
        session = FakeSession(replies=replies, models=models, reachable=reachable)
        client = OllamaClient(
            model="gemma3:4b", session=session, retries=kwargs.pop("retries", 0), **kwargs
        )
        return client, session

    return _make


@pytest.fixture
def scraped():
    return {
        "url": "https://example-isp.ua",
        "company_name": "Example ISP",
        "title": "Example ISP — інтернет у Києві",
        "description": "Домашній інтернет від 200 грн",
        "nav_items": ["Тарифи", "Контакти"],
        "technologies": ["WordPress"],
        "full_text": "Швидкий інтернет. Тарифи від 200 грн.",
        "prices": [
            {"value": 200, "currency": "UAH", "context": "Базовий — 200 грн/міс"},
            {"value": 350, "currency": "UAH", "context": "Оптимальний — 350 грн/міс"},
            {"value": 500, "currency": "UAH", "context": "Максимальний — 500 грн/міс"},
        ],
        "price_pages": ["https://example-isp.ua/tariffs"],
    }


@pytest.fixture
def valid_replies():
    """Коректні відповіді моделі на всі 5 кроків по порядку."""
    return [
        {  # profile
            "segment": "B2C",
            "market_position": "гравець",
            "website_quality": {
                "score": 7,
                "ux_notes": "Зрозуміла навігація",
                "cta_strength": "середній",
                "seo_signals": ["meta description", "структуровані тарифи"],
            },
        },
        {  # pricing
            "tier": "середній",
            "price_transparency": "висока",
            "notable_offers": ["Перший місяць безкоштовно"],
        },
        {  # reputation (соцмереж немає — крок пропускається)
            "sentiment": "змішаний",
            "main_complaints": ["Обриви зв'язку"],
            "main_praises": ["Швидке підключення"],
            "trust_score": 6,
        },
        {  # synthesis
            "strengths": ["Прозорі тарифи"],
            "weaknesses": ["Слабка присутність у соцмережах"],
            "key_differentiator": "Швидке підключення за добу",
            "threat_level": "середній",
            "threat_reasoning": "Схожий сегмент і ціни",
            "opportunities_for_us": ["Зробити акцент на стабільності"],
            "recommended_actions": [
                {"priority": "висока", "action": "Порівняти тарифи", "rationale": "Ціни близькі"}
            ],
            "summary": "Помірний конкурент у середньому ціновому сегменті.",
        },
    ]
