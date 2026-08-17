from analyzers.ai_analyzer import (
    analyze_competitor,
    generate_comparison_matrix,
    most_active_platform,
    price_stats,
    score_analysis,
    total_followers,
)

SOCIAL = {
    "telegram": {"platform": "telegram", "followers": 5000, "posts_count": 30, "bio": "канал"},
    "facebook": {"platform": "facebook", "error": "blocked or requires login"},
}


# ── Детерміновані обчислення ────────────────────────────────────────────


def test_price_stats_from_scraped_values():
    stats = price_stats([{"value": 200}, {"value": 350}, {"value": 500}])
    assert stats == {"min_price_uah": 200, "max_price_uah": 500, "avg_price_uah": 350}


def test_price_stats_ignores_broken_entries():
    stats = price_stats([{"value": "не число"}, {}, {"value": 300}])
    assert stats["min_price_uah"] == 300


def test_price_stats_without_prices():
    assert price_stats([]) == {
        "min_price_uah": None,
        "max_price_uah": None,
        "avg_price_uah": None,
    }


def test_total_followers_skips_failed_platforms():
    assert total_followers(SOCIAL) == 5000
    assert total_followers({"facebook": {"error": "blocked"}}) is None


def test_most_active_platform():
    assert most_active_platform(SOCIAL) == "telegram"


# ── Повний прохід аналізу ───────────────────────────────────────────────


def test_analyze_competitor_assembles_frontend_contract(make_client, scraped, valid_replies):
    client, session = make_client(replies=valid_replies)
    result = analyze_competitor(scraped, {}, [{"text": "Хороший провайдер, але бувають обриви"}], client=client)

    assert "error" not in result
    # ключі, які читають server.py і index.html
    for key in (
        "company_name", "website", "segment", "market_position", "pricing",
        "website_quality", "social_presence", "reputation", "strengths",
        "weaknesses", "threat_level", "recommended_actions", "summary",
    ):
        assert key in result, f"відсутній ключ {key}"

    assert result["company_name"] == "Example ISP"
    assert result["threat_level"] in ("низький", "середній", "високий", "критичний")
    assert result["recommended_actions"][0]["priority"] in ("висока", "середня", "низька")


def test_prices_come_from_python_not_from_model(make_client, scraped, valid_replies):
    client, _ = make_client(replies=valid_replies)
    result = analyze_competitor(scraped, {}, [{"text": "відгук"}], client=client)
    # модель у відповіді не повертала жодних чисел — вони пораховані з scraped
    assert result["pricing"]["min_price_uah"] == 200
    assert result["pricing"]["max_price_uah"] == 500
    assert result["pricing"]["avg_price_uah"] == 350


def test_steps_without_data_are_skipped_not_called(make_client, scraped, valid_replies):
    """Без соцмереж і відгуків модель викликається лише 3 рази з 5."""
    no_prices = dict(scraped, prices=[])
    client, session = make_client(replies=[valid_replies[0], valid_replies[3]])
    result = analyze_competitor(no_prices, {}, [], client=client)

    assert len(session.chat_calls) == 2
    assert set(result["_llm"]["skipped_steps"]) == {"pricing", "social", "reputation"}
    assert result["pricing"]["tier"] == "невідомо"


def test_failed_step_does_not_lose_the_rest(make_client, scraped, valid_replies):
    """Крок цін ламається — решта аналізу лишається валідною."""
    replies = list(valid_replies)
    replies[1] = "модель видала сміття"
    client, _ = make_client(replies=replies)
    result = analyze_competitor(scraped, {}, [{"text": "відгук"}], client=client)

    assert result["_llm"]["failed_steps"] == ["pricing"]
    assert result["pricing"]["tier"] == "невідомо"
    assert result["pricing"]["min_price_uah"] == 200  # детерміновані числа не втрачені
    assert result["summary"]  # підсумок все одно зроблено


def test_offline_ollama_returns_error(make_client, scraped):
    client, _ = make_client(reachable=False)
    result = analyze_competitor(scraped, {}, [], client=client)
    assert "error" in result
    assert "Ollama недоступна" in result["error"]


def test_missing_model_returns_error(make_client, scraped):
    client, _ = make_client(models=("llama3:8b",))
    result = analyze_competitor(scraped, {}, [], client=client)
    assert "ollama pull" in result["error"]


def test_out_of_enum_values_are_coerced(make_client, scraped, valid_replies):
    replies = list(valid_replies)
    replies[0] = dict(
        valid_replies[0],
        segment="дуже цікавий сегмент",
        market_position="монополіст",
        website_quality=dict(valid_replies[0]["website_quality"], score=42, cta_strength="ідеальний"),
    )
    replies[3] = dict(valid_replies[3], threat_level="жахливий")
    client, _ = make_client(replies=replies)
    result = analyze_competitor(scraped, {}, [{"text": "відгук"}], client=client)

    assert result["segment"] == "невідомо"
    assert result["market_position"] == "невідомо"
    assert result["website_quality"]["score"] == 10       # 42 обрізано до діапазону
    assert result["website_quality"]["cta_strength"] == "середній"
    assert result["threat_level"] == "середній"


def test_social_block_gets_python_computed_followers(make_client, scraped, valid_replies):
    social_reply = {
        "overall_score": 6,
        "content_quality": "середня",
        "posting_frequency": "декілька разів на тиждень",
        "notes": "Активний телеграм",
    }
    replies = [valid_replies[0], valid_replies[1], social_reply, valid_replies[2], valid_replies[3]]
    client, _ = make_client(replies=replies)
    result = analyze_competitor(scraped, SOCIAL, [{"text": "відгук"}], client=client)

    assert result["social_presence"]["total_followers"] == 5000
    assert result["social_presence"]["most_active_platform"] == "telegram"


# ── Порівняльна матриця ─────────────────────────────────────────────────


def _analysis(name, tier, web, social, trust, threat):
    return {
        "company_name": name,
        "pricing": {"tier": tier, "price_transparency": "висока"},
        "website_quality": {"score": web},
        "social_presence": {"overall_score": social},
        "reputation": {"trust_score": trust},
        "threat_level": threat,
    }


def test_score_analysis_is_deterministic():
    a = _analysis("A", "бюджетний", 8, 6, 7, "високий")
    assert score_analysis(a) == score_analysis(a)
    assert score_analysis(a)["threat_score"] == 8
    assert score_analysis(a)["price_score"] == 9  # бюджетний(8) + прозорість(+1)


def test_comparison_matrix_numbers_do_not_depend_on_model(make_client):
    analyses = [
        _analysis("A", "бюджетний", 8, 6, 8, "високий"),
        _analysis("B", "преміум", 5, 3, 4, "низький"),
    ]
    client, _ = make_client(replies=[{"market_gaps": ["ніша"], "strategic_summary": "висновок"}])
    matrix = generate_comparison_matrix(analyses, client=client)

    assert matrix["market_leader"] == "A"
    assert matrix["biggest_threat"] == "A"
    assert [row["company"] for row in matrix["matrix"]] == ["A", "B"]
    assert matrix["market_gaps"] == ["ніша"]


def test_comparison_survives_model_failure(make_client, monkeypatch):
    monkeypatch.setattr("analyzers.llm_client.time.sleep", lambda s: None)
    analyses = [
        _analysis("A", "бюджетний", 8, 6, 8, "високий"),
        _analysis("B", "преміум", 5, 3, 4, "низький"),
    ]
    client, _ = make_client(replies=["сміття"])
    matrix = generate_comparison_matrix(analyses, client=client)

    assert len(matrix["matrix"]) == 2          # числа на місці
    assert matrix["market_leader"] == "A"
    assert "недоступний" in matrix["strategic_summary"]
