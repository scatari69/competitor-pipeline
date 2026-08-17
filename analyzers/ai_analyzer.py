"""
Groq аналізатор — безкоштовно, без карток, працює в Україні
Модель: llama-3.3-70b-versatile (безкоштовний tier: 14400 запитів/день)
"""
import json
import logging
import os
import re
from groq import Groq

log = logging.getLogger(__name__)

client = Groq(api_key=os.environ["GROQ_API_KEY"])
GROQ_MODEL = "llama-3.3-70b-versatile"


ANALYSIS_SYSTEM = """Ти — старший бізнес-аналітик, спеціаліст з телеком і ISP ринку України.
Отримуєш сирі дані про конкурента (сайт, тарифи, соцмережі, відгуки).
Повертаєш ВИКЛЮЧНО валідний JSON без жодного додаткового тексту, markdown, або пояснень.
Мова відповіді: українська."""

ANALYSIS_PROMPT = """Проаналізуй наступні дані про конкурента і поверни JSON у точно такій структурі:

{{
  "company_name": "офіційна назва",
  "website": "URL",
  "segment": "B2B | B2C | обидва",
  "market_position": "лідер | гравець | нішевий | невідомо",

  "pricing": {{
    "tier": "бюджетний | середній | преміум | невідомо",
    "min_price_uah": число або null,
    "max_price_uah": число або null,
    "avg_price_uah": число або null,
    "price_transparency": "висока | середня | низька",
    "notable_offers": ["опис офферу 1", "офер 2"]
  }},

  "website_quality": {{
    "score": число від 1 до 10,
    "ux_notes": "короткий коментар про UX/UI",
    "cta_strength": "сильний | середній | слабкий",
    "seo_signals": ["сигнал 1", "сигнал 2"]
  }},

  "social_presence": {{
    "overall_score": число від 1 до 10,
    "most_active_platform": "назва або null",
    "total_followers": число або null,
    "content_quality": "висока | середня | низька | невідомо",
    "posting_frequency": "щодня | декілька разів на тиждень | рідко | невідомо",
    "notes": "коментар"
  }},

  "reputation": {{
    "sentiment": "позитивний | нейтральний | негативний | змішаний",
    "main_complaints": ["скарга 1", "скарга 2"],
    "main_praises": ["похвала 1", "похвала 2"],
    "trust_score": число від 1 до 10
  }},

  "strengths": ["сила 1", "сила 2", "сила 3"],
  "weaknesses": ["слабкість 1", "слабкість 2", "слабкість 3"],

  "key_differentiator": "одне речення — головна унікальна перевага",
  "threat_level": "низький | середній | високий | критичний",
  "threat_reasoning": "чому саме такий рівень загрози",

  "opportunities_for_us": [
    "конкретна можливість 1",
    "конкретна можливість 2",
    "конкретна можливість 3"
  ],

  "recommended_actions": [
    {{"priority": "висока", "action": "дія 1", "rationale": "обґрунтування"}},
    {{"priority": "середня", "action": "дія 2", "rationale": "обґрунтування"}}
  ],

  "summary": "3-4 речення стратегічного висновку для менеджменту"
}}

Дані для аналізу:
{data}"""


def _call_groq(system: str, prompt: str) -> str:
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    return response.choices[0].message.content.strip()


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return json.loads(cleaned)


def analyze_competitor(scraped_data: dict, social_data: dict, review_snippets: list) -> dict:
    analysis_input = {
        "website": {
            "url": scraped_data.get("url"),
            "company_name": scraped_data.get("company_name"),
            "title": scraped_data.get("title"),
            "description": scraped_data.get("description"),
            "nav_items": scraped_data.get("nav_items", []),
            "technologies": scraped_data.get("technologies", []),
            "prices_found": scraped_data.get("prices", [])[:15],
            "text_sample": scraped_data.get("full_text", "")[:3000],
        },
        "social_media": social_data,
        "review_snippets": review_snippets[:10],
    }

    data_str = json.dumps(analysis_input, ensure_ascii=False, indent=2)
    log.info("Sending data to Groq for analysis...")
    raw = ""
    try:
        raw = _call_groq(ANALYSIS_SYSTEM, ANALYSIS_PROMPT.format(data=data_str))
        log.info(f"Groq response received ({len(raw)} chars)")
        result = _parse_json(raw)
        log.info("Analysis parsed successfully")
        return result
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}")
        return {"error": f"JSON parse failed: {e}", "raw": raw[:500]}
    except Exception as e:
        log.error(f"Groq API error: {e}")
        return {"error": str(e)}


def generate_comparison_matrix(analyses: list[dict]) -> dict:
    if not analyses:
        return {}

    system = "Ти аналітик. Повертаєш виключно валідний JSON без додаткового тексту."
    prompt = f"""На основі аналізів {len(analyses)} конкурентів, створи порівняльну матрицю.

Поверни JSON:
{{
  "matrix": [
    {{
      "company": "назва",
      "price_score": 1-10,
      "web_score": 1-10,
      "social_score": 1-10,
      "reputation_score": 1-10,
      "threat_score": 1-10,
      "overall_score": 1-10
    }}
  ],
  "market_leader": "назва компанії-лідера",
  "biggest_threat": "назва найбільшої загрози",
  "market_gaps": ["незайнята ніша 1", "ніша 2"],
  "strategic_summary": "2-3 речення про ситуацію на ринку"
}}

Аналізи конкурентів:
{json.dumps(analyses, ensure_ascii=False, indent=2)[:6000]}"""

    try:
        raw = _call_groq(system, prompt)
        return _parse_json(raw)
    except Exception as e:
        log.error(f"Comparison matrix error: {e}")
        return {"error": str(e)}
