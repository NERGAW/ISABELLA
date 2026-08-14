import json

import pytest
import requests

from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.llm import (
    InvalidStructuredResponseError,
    OllamaProvider,
    ProviderUnavailableError,
    load_intelligence_config,
)
from Isabella.Intelligence.models import Intent
from Isabella.Intelligence.planner import Planner
from Isabella.Intelligence.router import Router


CONFIG = {
    "provider": "ollama",
    "model": "qwen3:1.7b",
    "base_url": "http://localhost:11434",
    "temperature": 0.3,
    "timeout_seconds": 1,
    "max_retries": 1,
    "max_plan_steps": 8,
}


class FakeLLM:
    def chat(self, message):
        return f"Resposta para: {message}"


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def test_llm_config(tmp_path):
    path = tmp_path / "intelligence.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")

    assert load_intelligence_config(path)["model"] == "qwen3:1.7b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Quem é você?", Intent.CONVERSATION),
        ("Abra o Chrome.", Intent.SINGLE_SKILL),
        ("Abra o Chrome e depois abra o YouTube.", Intent.MULTI_STEP),
    ],
)
def test_router_intents(text, expected):
    assert Router().route(text) == expected


def test_planner_two_steps():
    plan = Planner().plan("Abra o Chrome e depois abra o YouTube.")

    assert [step.skill for step in plan.steps] == [
        "applications.open",
        "browser.open_url",
    ]
    assert plan.steps[1].depends_on == [1]


def test_planner_max_steps():
    request = " e depois ".join(["abra o Chrome"] * 9)
    plan = Planner(max_steps=8).plan(request)

    assert plan.steps == []
    assert plan.error == "plan exceeds maximum of 8 steps"


def test_brain_conversation():
    response = Brain(FakeLLM()).process("Explique inteligência artificial.")

    assert response.response_type == Intent.CONVERSATION
    assert response.message.startswith("Resposta para:")


def test_brain_single_skill():
    response = Brain(FakeLLM()).process("Pode iniciar o Discord para mim?")

    assert response.response_type == Intent.SINGLE_SKILL
    assert response.skill_request.skill == "applications.open"
    assert response.skill_request.arguments == {"name": "discord"}


def test_brain_multi_step():
    response = Brain(FakeLLM()).process("Abra o Discord e tire uma captura da tela.")

    assert response.response_type == Intent.MULTI_STEP
    assert [step.skill for step in response.plan.steps] == [
        "applications.open",
        "system.screenshot",
    ]


def test_ollama_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "request", fail)
    provider = OllamaProvider(CONFIG)

    assert provider.health_check() is False
    with pytest.raises(ProviderUnavailableError):
        provider.chat("Olá")


def test_brain_survives_ollama_unavailable(monkeypatch):
    provider = OllamaProvider(CONFIG)

    def unavailable(message):
        raise ProviderUnavailableError("offline")

    monkeypatch.setattr(provider, "chat", unavailable)
    response = Brain(provider).process("Quem é você?")

    assert response.response_type == Intent.CONVERSATION
    assert "indisponível" in response.message


def test_invalid_structured_response(monkeypatch):
    provider = OllamaProvider(CONFIG)
    monkeypatch.setattr(
        requests,
        "request",
        lambda *args, **kwargs: FakeResponse({"message": {"content": "not-json"}}),
    )

    with pytest.raises(InvalidStructuredResponseError):
        provider.structured_chat("route", {"type": "object"})
