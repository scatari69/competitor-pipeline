import pytest

from analyzers.llm_client import LLMError, OllamaClient, strip_fences


def test_strip_fences_removes_markdown_wrapper():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_health_reports_missing_model(make_client):
    client, _ = make_client(models=("llama3:8b",))
    health = client.health()
    assert health["reachable"] is True
    assert health["model_available"] is False
    assert "ollama pull" in health["error"]


def test_health_accepts_latest_tag(make_client):
    client, _ = make_client(models=("gemma3:latest",))
    client.model = "gemma3"
    assert client.health()["model_available"] is True


def test_health_handles_unreachable_server(make_client):
    client, _ = make_client(reachable=False)
    health = client.health()
    assert health["reachable"] is False
    assert health["error"]


def test_complete_json_sends_schema_and_options(make_client):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    client, session = make_client(replies=[{"a": 1}])
    result = client.complete_json("система", "запит", schema, max_tokens=123)

    assert result == {"a": 1}
    sent = session.chat_calls[0]
    assert sent["format"] == schema
    assert sent["stream"] is False
    assert sent["options"]["num_predict"] == 123
    assert sent["options"]["num_ctx"] == client.num_ctx


def test_gemma_gets_system_folded_into_user_turn(make_client):
    client, session = make_client(replies=[{"a": 1}])
    client.complete_json("СИСТЕМА", "ЗАПИТ", {"type": "object"})

    messages = session.chat_calls[0]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "СИСТЕМА" in messages[0]["content"]
    assert "ЗАПИТ" in messages[0]["content"]


def test_non_gemma_keeps_system_role(make_client):
    client, session = make_client(replies=[{"a": 1}])
    client.model = "qwen3:4b"
    client.complete_json("СИСТЕМА", "ЗАПИТ", {"type": "object"})

    messages = session.chat_calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]


def test_retries_after_invalid_json(make_client, monkeypatch):
    monkeypatch.setattr("analyzers.llm_client.time.sleep", lambda s: None)
    client, session = make_client(replies=["не json взагалі", {"a": 2}], retries=1)
    assert client.complete_json("s", "p", {"type": "object"}) == {"a": 2}
    assert len(session.chat_calls) == 2


def test_raises_llm_error_after_exhausting_retries(make_client, monkeypatch):
    monkeypatch.setattr("analyzers.llm_client.time.sleep", lambda s: None)
    client, _ = make_client(replies=["зламано", "теж зламано"], retries=1)
    with pytest.raises(LLMError):
        client.complete_json("s", "p", {"type": "object"})


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434/")
    monkeypatch.setenv("LLM_MODEL", "gemma3:12b")
    monkeypatch.setenv("LLM_NUM_CTX", "16384")
    client = OllamaClient(session=object())
    assert client.base_url == "http://gpu-box:11434"
    assert client.model == "gemma3:12b"
    assert client.num_ctx == 16384


def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_NUM_CTX", "багато")
    client = OllamaClient(session=object())
    assert client.num_ctx == 8192
