"""
JSON-схеми для structured output локальної моделі.

Кожна схема покриває один вузький крок аналізу. Це навмисно: gemma3:4b
не тримає в голові структуру на 60 полів, але надійно заповнює блок із 4-6.
Значення enum збігаються з тим, що рендерить templates/index.html
(threatColor, priority-* класи) — міняти рядки можна лише разом із фронтендом.
"""

THREAT_LEVELS = ["низький", "середній", "високий", "критичний"]
PRIORITIES = ["висока", "середня", "низька"]
UNKNOWN = "невідомо"

SEGMENTS = ["B2B", "B2C", "обидва", UNKNOWN]
MARKET_POSITIONS = ["лідер", "гравець", "нішевий", UNKNOWN]
PRICE_TIERS = ["бюджетний", "середній", "преміум", UNKNOWN]
TRANSPARENCY = ["висока", "середня", "низька"]
CTA_STRENGTH = ["сильний", "середній", "слабкий"]
QUALITY = ["висока", "середня", "низька", UNKNOWN]
FREQUENCY = ["щодня", "декілька разів на тиждень", "рідко", UNKNOWN]
SENTIMENTS = ["позитивний", "нейтральний", "негативний", "змішаний"]


def _str_array(max_items: int, description: str = "") -> dict:
    schema = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": max_items,
    }
    if description:
        schema["description"] = description
    return schema


def _score(description: str = "Оцінка від 1 до 10") -> dict:
    return {"type": "integer", "minimum": 1, "maximum": 10, "description": description}


# ── Крок 1: профіль компанії та якість сайту ────────────────────────────
PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "segment": {"type": "string", "enum": SEGMENTS},
        "market_position": {"type": "string", "enum": MARKET_POSITIONS},
        "website_quality": {
            "type": "object",
            "properties": {
                "score": _score("Якість сайту від 1 до 10"),
                "ux_notes": {"type": "string", "description": "Одне-два речення про UX/UI"},
                "cta_strength": {"type": "string", "enum": CTA_STRENGTH},
                "seo_signals": _str_array(4, "Помічені SEO/маркетингові сигнали"),
            },
            "required": ["score", "ux_notes", "cta_strength", "seo_signals"],
        },
    },
    "required": ["segment", "market_position", "website_quality"],
}

# ── Крок 2: ціноутворення (числа рахуються в Python, не моделлю) ────────
PRICING_SCHEMA = {
    "type": "object",
    "properties": {
        "tier": {"type": "string", "enum": PRICE_TIERS},
        "price_transparency": {"type": "string", "enum": TRANSPARENCY},
        "notable_offers": _str_array(4, "Помітні пропозиції або акції"),
    },
    "required": ["tier", "price_transparency", "notable_offers"],
}

# ── Крок 3: соцмережі (total_followers рахується в Python) ──────────────
SOCIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": _score("Загальна оцінка присутності в соцмережах"),
        "content_quality": {"type": "string", "enum": QUALITY},
        "posting_frequency": {"type": "string", "enum": FREQUENCY},
        "notes": {"type": "string", "description": "Одне-два речення коментаря"},
    },
    "required": ["overall_score", "content_quality", "posting_frequency", "notes"],
}

# ── Крок 4: репутація ───────────────────────────────────────────────────
REPUTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "main_complaints": _str_array(4, "На що скаржаться клієнти"),
        "main_praises": _str_array(4, "За що хвалять"),
        "trust_score": _score("Рівень довіри від 1 до 10"),
    },
    "required": ["sentiment", "main_complaints", "main_praises", "trust_score"],
}

# ── Крок 5: підсумок і рекомендації ─────────────────────────────────────
SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "strengths": _str_array(4, "Сильні сторони конкурента"),
        "weaknesses": _str_array(4, "Слабкі сторони конкурента"),
        "key_differentiator": {"type": "string", "description": "Одне речення"},
        "threat_level": {"type": "string", "enum": THREAT_LEVELS},
        "threat_reasoning": {"type": "string", "description": "Одне-два речення"},
        "opportunities_for_us": _str_array(4, "Конкретні можливості для нас"),
        "recommended_actions": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "string", "enum": PRIORITIES},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["priority", "action", "rationale"],
            },
        },
        "summary": {"type": "string", "description": "2-4 речення для менеджменту"},
    },
    "required": [
        "strengths",
        "weaknesses",
        "key_differentiator",
        "threat_level",
        "threat_reasoning",
        "opportunities_for_us",
        "recommended_actions",
        "summary",
    ],
}

# ── Порівняння: числові оцінки рахуються в Python, модель дає якісну частину ──
COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "market_gaps": _str_array(5, "Незайняті ніші на ринку"),
        "strategic_summary": {"type": "string", "description": "2-3 речення про ринок"},
    },
    "required": ["market_gaps", "strategic_summary"],
}
