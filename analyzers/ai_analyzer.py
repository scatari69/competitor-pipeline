"""
Аналізатор на локальній LLM (Ollama, за замовчуванням gemma3:4b).

Замість одного виклику з JSON-шаблоном на 60 полів аналіз розбитий на 5 вузьких
кроків. Причини:
  * маленька модель тримає структуру на 4-6 полів, але не на 60;
  * кожен крок отримує лише релевантний зріз даних, тож влазить у num_ctx;
  * падіння одного кроку не втрачає весь аналіз — інші блоки лишаються.

Арифметика (мін/макс/середня ціна, сума підписників, зведені бали) рахується в
Python, а не моделлю: 4B-моделі стабільно помиляються в числах.

Підсумковий словник повторює структуру, яку читають server.py і
templates/index.html — формат назовні не змінився.
"""
import json
import logging
import statistics
import sys
from typing import Callable, Optional

try:
    from .llm_client import LLMError, OllamaClient, get_client
    from .schemas import (
        COMPARISON_SCHEMA,
        CTA_STRENGTH,
        FREQUENCY,
        MARKET_POSITIONS,
        PRICE_TIERS,
        PRICING_SCHEMA,
        PRIORITIES,
        PROFILE_SCHEMA,
        QUALITY,
        REPUTATION_SCHEMA,
        SEGMENTS,
        SENTIMENTS,
        SOCIAL_SCHEMA,
        SYNTHESIS_SCHEMA,
        THREAT_LEVELS,
        TRANSPARENCY,
        UNKNOWN,
    )
except ImportError:  # прямий запуск: python analyzers/ai_analyzer.py
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from analyzers.llm_client import LLMError, OllamaClient, get_client
    from analyzers.schemas import (
        COMPARISON_SCHEMA,
        CTA_STRENGTH,
        FREQUENCY,
        MARKET_POSITIONS,
        PRICE_TIERS,
        PRICING_SCHEMA,
        PRIORITIES,
        PROFILE_SCHEMA,
        QUALITY,
        REPUTATION_SCHEMA,
        SEGMENTS,
        SENTIMENTS,
        SOCIAL_SCHEMA,
        SYNTHESIS_SCHEMA,
        THREAT_LEVELS,
        TRANSPARENCY,
        UNKNOWN,
    )

log = logging.getLogger(__name__)

# Бюджети на вхід одного кроку (символи, не токени) — з запасом під num_ctx=8192
MAX_TEXT_SAMPLE = 1500
MAX_STEP_INPUT = 4000

SYSTEM_ANALYST = (
    "Ти — бізнес-аналітик українського ринку інтернет-провайдерів. "
    "Відповідаєш стисло, українською мовою, тільки у форматі JSON за заданою схемою. "
    "Не вигадуй фактів: якщо даних недостатньо — обирай найобережнішу оцінку."
)


# ── Утиліти ─────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _dump(data, limit: int = MAX_STEP_INPUT) -> str:
    return _truncate(json.dumps(data, ensure_ascii=False, indent=2), limit)


def _one_of(value, allowed: list, fallback: str) -> str:
    """Приводить значення до дозволеного enum (schema інколи не тримає слабкі моделі)."""
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    return fallback


def _clamp_score(value, fallback: int = 5) -> int:
    try:
        return max(1, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


def _str_list(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= limit:
            break
    return out


# ── Детерміновані обчислення ────────────────────────────────────────────


def price_stats(prices: list[dict]) -> dict:
    """Мін/макс/середня ціна зі скрейпнутих значень — рахуємо самі, не моделлю."""
    values = []
    for p in prices or []:
        try:
            values.append(int(p["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return {"min_price_uah": None, "max_price_uah": None, "avg_price_uah": None}
    return {
        "min_price_uah": min(values),
        "max_price_uah": max(values),
        "avg_price_uah": int(round(statistics.mean(values))),
    }


def total_followers(social_data: dict) -> Optional[int]:
    total = 0
    found = False
    for data in (social_data or {}).values():
        if not isinstance(data, dict):
            continue
        followers = data.get("followers")
        if isinstance(followers, int) and followers > 0:
            total += followers
            found = True
    return total if found else None


def most_active_platform(social_data: dict) -> Optional[str]:
    best, best_followers = None, -1
    for platform, data in (social_data or {}).items():
        if not isinstance(data, dict) or data.get("error"):
            continue
        followers = data.get("followers") or 0
        if followers > best_followers:
            best, best_followers = platform, followers
    return best


# ── Кроки аналізу ───────────────────────────────────────────────────────


def _step_profile(client: OllamaClient, scraped: dict) -> dict:
    payload = {
        "title": _truncate(scraped.get("title", ""), 200),
        "description": _truncate(scraped.get("description", ""), 300),
        "nav_items": (scraped.get("nav_items") or [])[:12],
        "technologies": (scraped.get("technologies") or [])[:10],
        "text_sample": _truncate(scraped.get("full_text", ""), MAX_TEXT_SAMPLE),
    }
    prompt = (
        "Оціни сайт інтернет-провайдера за наведеними даними.\n"
        "Визнач сегмент (B2B/B2C/обидва), позицію на ринку та якість сайту.\n\n"
        f"Дані:\n{_dump(payload)}"
    )
    raw = client.complete_json(SYSTEM_ANALYST, prompt, PROFILE_SCHEMA, max_tokens=500)
    wq = raw.get("website_quality") or {}
    return {
        "segment": _one_of(raw.get("segment"), SEGMENTS, UNKNOWN),
        "market_position": _one_of(raw.get("market_position"), MARKET_POSITIONS, UNKNOWN),
        "website_quality": {
            "score": _clamp_score(wq.get("score")),
            "ux_notes": _truncate(wq.get("ux_notes", ""), 400),
            "cta_strength": _one_of(wq.get("cta_strength"), CTA_STRENGTH, "середній"),
            "seo_signals": _str_list(wq.get("seo_signals"), 4),
        },
    }


def _step_pricing(client: OllamaClient, scraped: dict) -> dict:
    prices = (scraped.get("prices") or [])[:20]
    payload = {
        "знайдені_ціни": [
            {"грн": p.get("value"), "контекст": _truncate(p.get("context", ""), 80)}
            for p in prices
        ],
        "кількість_сторінок_з_тарифами": len(scraped.get("price_pages") or []),
    }
    prompt = (
        "Оціни цінову політику провайдера за знайденими цінами (гривні на місяць).\n"
        "tier: бюджетний — переважно до 250 грн, середній — 250-500, преміум — понад 500.\n"
        "price_transparency: висока, якщо ціни явно вказані на сайті.\n"
        "Не рахуй середні значення — лише якісна оцінка.\n\n"
        f"Дані:\n{_dump(payload)}"
    )
    raw = client.complete_json(SYSTEM_ANALYST, prompt, PRICING_SCHEMA, max_tokens=400)
    return {
        "tier": _one_of(raw.get("tier"), PRICE_TIERS, UNKNOWN),
        "price_transparency": _one_of(raw.get("price_transparency"), TRANSPARENCY, "низька"),
        "notable_offers": _str_list(raw.get("notable_offers"), 4),
    }


def _step_social(client: OllamaClient, social_data: dict) -> dict:
    payload = {}
    for platform, data in (social_data or {}).items():
        if not isinstance(data, dict) or data.get("error"):
            continue
        payload[platform] = {
            "followers": data.get("followers"),
            "posts_count": data.get("posts_count"),
            "bio": _truncate(data.get("bio", ""), 150),
            "recent_posts": [_truncate(p, 120) for p in (data.get("recent_posts") or [])[:3]],
        }
    prompt = (
        "Оціни присутність компанії в соцмережах за зібраними публічними даними.\n"
        "Не рахуй суму підписників — лише якісна оцінка активності та контенту.\n\n"
        f"Дані:\n{_dump(payload)}"
    )
    raw = client.complete_json(SYSTEM_ANALYST, prompt, SOCIAL_SCHEMA, max_tokens=400)
    return {
        "overall_score": _clamp_score(raw.get("overall_score")),
        "content_quality": _one_of(raw.get("content_quality"), QUALITY, UNKNOWN),
        "posting_frequency": _one_of(raw.get("posting_frequency"), FREQUENCY, UNKNOWN),
        "notes": _truncate(raw.get("notes", ""), 400),
    }


def _step_reputation(client: OllamaClient, review_snippets: list) -> dict:
    payload = [_truncate(s.get("text", ""), 200) for s in (review_snippets or [])[:8]]
    prompt = (
        "Оціни репутацію провайдера за фрагментами відгуків із пошуку.\n"
        "Виділи головні скарги і головні похвали. Якщо фрагмент не про компанію — ігноруй.\n\n"
        f"Фрагменти:\n{_dump(payload)}"
    )
    raw = client.complete_json(SYSTEM_ANALYST, prompt, REPUTATION_SCHEMA, max_tokens=500)
    return {
        "sentiment": _one_of(raw.get("sentiment"), SENTIMENTS, "нейтральний"),
        "main_complaints": _str_list(raw.get("main_complaints"), 4),
        "main_praises": _str_list(raw.get("main_praises"), 4),
        "trust_score": _clamp_score(raw.get("trust_score")),
    }


def _step_synthesis(client: OllamaClient, company_name: str, blocks: dict) -> dict:
    payload = {
        "компанія": company_name,
        "сегмент": blocks.get("segment"),
        "позиція": blocks.get("market_position"),
        "ціни": blocks.get("pricing"),
        "сайт": blocks.get("website_quality"),
        "соцмережі": blocks.get("social_presence"),
        "репутація": blocks.get("reputation"),
    }
    prompt = (
        "Ти аналізуєш конкурента для нашої компанії — інтернет-провайдера.\n"
        "На основі готових блоків аналізу сформулюй сильні та слабкі сторони, "
        "рівень загрози для нас, можливості та конкретні дії.\n"
        "Спирайся лише на наведені дані.\n\n"
        f"Блоки аналізу:\n{_dump(payload)}"
    )
    raw = client.complete_json(SYSTEM_ANALYST, prompt, SYNTHESIS_SCHEMA, max_tokens=1200)

    actions = []
    for item in raw.get("recommended_actions") or []:
        if not isinstance(item, dict):
            continue
        action = _truncate(item.get("action", ""), 200)
        if not action:
            continue
        actions.append({
            "priority": _one_of(item.get("priority"), PRIORITIES, "середня"),
            "action": action,
            "rationale": _truncate(item.get("rationale", ""), 300),
        })
        if len(actions) >= 4:
            break

    return {
        "strengths": _str_list(raw.get("strengths"), 4),
        "weaknesses": _str_list(raw.get("weaknesses"), 4),
        "key_differentiator": _truncate(raw.get("key_differentiator", ""), 300),
        "threat_level": _one_of(raw.get("threat_level"), THREAT_LEVELS, "середній"),
        "threat_reasoning": _truncate(raw.get("threat_reasoning", ""), 400),
        "opportunities_for_us": _str_list(raw.get("opportunities_for_us"), 4),
        "recommended_actions": actions,
        "summary": _truncate(raw.get("summary", ""), 800),
    }


# ── Дефолтні блоки, коли даних немає (LLM не викликаємо) ────────────────


def _empty_pricing() -> dict:
    return {"tier": UNKNOWN, "price_transparency": "низька", "notable_offers": []}


def _empty_social() -> dict:
    return {
        "overall_score": 1,
        "content_quality": UNKNOWN,
        "posting_frequency": UNKNOWN,
        "notes": "Публічних даних про соцмережі не знайдено.",
    }


def _empty_reputation() -> dict:
    return {
        "sentiment": "нейтральний",
        "main_complaints": [],
        "main_praises": [],
        "trust_score": 5,
    }


def _empty_profile() -> dict:
    return {
        "segment": UNKNOWN,
        "market_position": UNKNOWN,
        "website_quality": {
            "score": 5,
            "ux_notes": "",
            "cta_strength": "середній",
            "seo_signals": [],
        },
    }


def _empty_synthesis() -> dict:
    return {
        "strengths": [],
        "weaknesses": [],
        "key_differentiator": "",
        "threat_level": "середній",
        "threat_reasoning": "",
        "opportunities_for_us": [],
        "recommended_actions": [],
        "summary": "",
    }


# ── Основний вхід ───────────────────────────────────────────────────────


def analyze_competitor(
    scraped_data: dict,
    social_data: dict,
    review_snippets: list,
    on_step: Optional[Callable[[str], None]] = None,
    client: Optional[OllamaClient] = None,
) -> dict:
    """
    Повний аналіз конкурента за 5 кроків.

    Args:
        scraped_data: ScrapeResult.to_dict()
        social_data: результат scrape_social_presence()
        review_snippets: фрагменти відгуків
        on_step: callback(msg) — прогрес по кроках (для SSE)
        client: підміна LLM-клієнта (тести)

    Returns:
        dict у форматі, який очікують server.py та index.html.
        При недоступній моделі — {"error": ...}.
    """
    llm = client or get_client()

    def step(msg: str):
        log.info(msg)
        if on_step:
            on_step(msg)

    health = llm.health()
    if not health["reachable"]:
        return {"error": f"Ollama недоступна на {health['base_url']}: {health['error']}"}
    if not health["model_available"]:
        return {"error": health["error"]}

    scraped_data = scraped_data or {}
    social_data = social_data or {}
    review_snippets = review_snippets or []

    failed_steps: list[str] = []
    skipped_steps: list[str] = []

    def run_step(name: str, message: str, fn, fallback):
        step(message)
        try:
            return fn()
        except LLMError as e:
            log.warning(f"Крок '{name}' провалився: {e}")
            failed_steps.append(name)
            return fallback()
        except Exception as e:
            log.warning(f"Крок '{name}' — неочікувана помилка: {e}")
            failed_steps.append(name)
            return fallback()

    # 1/5 профіль і сайт
    profile = run_step(
        "profile",
        "Аналіз сайту та позиціонування (1/5)...",
        lambda: _step_profile(llm, scraped_data),
        _empty_profile,
    )

    # 2/5 ціни
    if scraped_data.get("prices"):
        pricing = run_step(
            "pricing",
            "Аналіз тарифів (2/5)...",
            lambda: _step_pricing(llm, scraped_data),
            _empty_pricing,
        )
    else:
        step("Цін не знайдено — крок тарифів пропущено (2/5)")
        skipped_steps.append("pricing")
        pricing = _empty_pricing()
    pricing.update(price_stats(scraped_data.get("prices")))

    # 3/5 соцмережі
    active_social = {
        p: d for p, d in social_data.items() if isinstance(d, dict) and not d.get("error")
    }
    if active_social:
        social_presence = run_step(
            "social",
            "Аналіз соцмереж (3/5)...",
            lambda: _step_social(llm, active_social),
            _empty_social,
        )
    else:
        step("Даних соцмереж немає — крок пропущено (3/5)")
        skipped_steps.append("social")
        social_presence = _empty_social()
    social_presence["total_followers"] = total_followers(social_data)
    social_presence["most_active_platform"] = most_active_platform(social_data)

    # 4/5 репутація
    if review_snippets:
        reputation = run_step(
            "reputation",
            "Аналіз відгуків (4/5)...",
            lambda: _step_reputation(llm, review_snippets),
            _empty_reputation,
        )
    else:
        step("Відгуків не знайдено — крок пропущено (4/5)")
        skipped_steps.append("reputation")
        reputation = _empty_reputation()

    # 5/5 підсумок
    company_name = scraped_data.get("company_name") or scraped_data.get("url") or "конкурент"
    blocks = {
        "segment": profile["segment"],
        "market_position": profile["market_position"],
        "pricing": pricing,
        "website_quality": profile["website_quality"],
        "social_presence": social_presence,
        "reputation": reputation,
    }
    synthesis = run_step(
        "synthesis",
        "Формуємо стратегічний висновок (5/5)...",
        lambda: _step_synthesis(llm, company_name, blocks),
        _empty_synthesis,
    )

    result = {
        "company_name": company_name,
        "website": scraped_data.get("url", ""),
        "segment": profile["segment"],
        "market_position": profile["market_position"],
        "pricing": pricing,
        "website_quality": profile["website_quality"],
        "social_presence": social_presence,
        "reputation": reputation,
        **synthesis,
        "_llm": {
            "provider": "ollama",
            "model": llm.model,
            "failed_steps": failed_steps,
            "skipped_steps": skipped_steps,
        },
    }

    if len(failed_steps) >= 4:
        result["error"] = f"Більшість кроків аналізу провалились: {', '.join(failed_steps)}"
    return result


# ── Порівняльна матриця ─────────────────────────────────────────────────

_TIER_SCORE = {"бюджетний": 8, "середній": 6, "преміум": 4, UNKNOWN: 5}
_TRANSPARENCY_BONUS = {"висока": 1, "середня": 0, "низька": -1}
_THREAT_SCORE = {"низький": 3, "середній": 5, "високий": 8, "критичний": 10}


def score_analysis(analysis: dict) -> dict:
    """
    Числові бали для порівняльної матриці — рахуються детерміновано,
    щоб два запуски на тих самих даних давали однакову таблицю.

    price_score — цінова привабливість для клієнта (дешевше = вище).
    """
    pricing = analysis.get("pricing") or {}
    website = analysis.get("website_quality") or {}
    social = analysis.get("social_presence") or {}
    reputation = analysis.get("reputation") or {}

    price_score = _TIER_SCORE.get(pricing.get("tier"), 5)
    price_score += _TRANSPARENCY_BONUS.get(pricing.get("price_transparency"), 0)
    price_score = max(1, min(10, price_score))

    web_score = _clamp_score(website.get("score"))
    social_score = _clamp_score(social.get("overall_score"), 1)
    reputation_score = _clamp_score(reputation.get("trust_score"))
    threat_score = _THREAT_SCORE.get(analysis.get("threat_level"), 5)

    overall = (
        price_score * 0.25 + web_score * 0.25 + social_score * 0.15 + reputation_score * 0.35
    )

    return {
        "company": analysis.get("company_name") or "?",
        "price_score": price_score,
        "web_score": web_score,
        "social_score": social_score,
        "reputation_score": reputation_score,
        "threat_score": threat_score,
        "overall_score": max(1, min(10, int(round(overall)))),
    }


def generate_comparison_matrix(
    analyses: list[dict],
    client: Optional[OllamaClient] = None,
) -> dict:
    """Порівняльна матриця: числа — детерміновано, ніші та висновок — від моделі."""
    if not analyses:
        return {}

    matrix = [score_analysis(a) for a in analyses]
    leader = max(matrix, key=lambda r: r["overall_score"])
    threat = max(matrix, key=lambda r: r["threat_score"])

    result = {
        "matrix": matrix,
        "market_leader": leader["company"],
        "biggest_threat": threat["company"],
        "market_gaps": [],
        "strategic_summary": "",
    }

    llm = client or get_client()
    payload = [
        {
            "компанія": a.get("company_name"),
            "сегмент": a.get("segment"),
            "ціни": (a.get("pricing") or {}).get("tier"),
            "сильні": (a.get("strengths") or [])[:3],
            "слабкі": (a.get("weaknesses") or [])[:3],
            "загроза": a.get("threat_level"),
        }
        for a in analyses
    ]
    prompt = (
        f"Порівняй {len(analyses)} конкурентів на ринку інтернет-провайдерів.\n"
        "Визнач незайняті ніші (де жоден із них не сильний) і дай стислий висновок.\n\n"
        f"Дані:\n{_dump(payload, 5000)}"
    )
    try:
        raw = llm.complete_json(SYSTEM_ANALYST, prompt, COMPARISON_SCHEMA, max_tokens=700)
        result["market_gaps"] = _str_list(raw.get("market_gaps"), 5)
        result["strategic_summary"] = _truncate(raw.get("strategic_summary", ""), 600)
    except LLMError as e:
        log.warning(f"Якісна частина порівняння недоступна: {e}")
        result["strategic_summary"] = f"Числові оцінки розраховано. Текстовий висновок недоступний: {e}"

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    llm = get_client()
    health = llm.health()
    print(f"Ollama: {health['base_url']}")
    print(f"Модель: {health['model']} — {'✓ готова' if health['model_available'] else '✗ ' + health['error']}")
    if health["models"]:
        print(f"Доступні моделі: {', '.join(health['models'])}")
    if not health["reachable"]:
        sys.exit(1)

    demo_scrape = {
        "url": "https://example-isp.ua",
        "company_name": "Example ISP",
        "title": "Example ISP — швидкий інтернет у Києві",
        "description": "Домашній інтернет від 200 грн на місяць, підключення за 1 день.",
        "nav_items": ["Тарифи", "Підключення", "Оплата", "Контакти"],
        "technologies": ["WordPress", "Google Analytics"],
        "full_text": "Швидкий домашній інтернет у Києві. Тарифи від 200 грн. Підтримка 24/7.",
        "prices": [
            {"value": 200, "currency": "UAH", "context": "Базовий 100 Мбіт — 200 грн/міс"},
            {"value": 350, "currency": "UAH", "context": "Оптимальний 500 Мбіт — 350 грн/міс"},
        ],
        "price_pages": ["https://example-isp.ua/tariffs"],
    }
    result = analyze_competitor(demo_scrape, {}, [], on_step=lambda m: print(f"  → {m}"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
