import json
from pathlib import Path

import pytest

from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import Intent
from Isabella.Memory.database import MemoryDatabase
from Isabella.Memory.manager import MemoryError, MemoryManager, SecretMemoryError
from Isabella.Memory.models import MemoryType
from Isabella.Memory.retrieval import parse_remember
from Isabella.Skills.base import SkillResult


def config(path: Path, working_limit=30):
    return {
        "enabled": True,
        "database_path": str(path),
        "working_memory_max_messages": working_limit,
        "max_retrieval_results": 5,
        "auto_save_preferences": False,
        "auto_save_facts": False,
        "auto_save_projects": False,
        "semantic_search_enabled": False,
    }


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, message):
        self.prompts.append(message)
        return "Resposta básica."

    def close(self):
        return None


class CapturingRegistry:
    def __init__(self):
        self.calls = []

    def execute(self, skill, arguments, confirmed=False):
        self.calls.append((skill, arguments))
        return SkillResult(True, skill, f"{arguments.get('name', skill)} aberto.")


def manager(tmp_path, working_limit=30):
    return MemoryManager(config(tmp_path / "memory.db", working_limit))


def test_database_initializes_schema_and_crud(tmp_path):
    database = MemoryDatabase(tmp_path / "nested" / "memory.db")
    record = database.upsert(MemoryType.FACT, "city", "Recife", "user_explicit", 1.0, ("place",), {})
    assert record.id > 0
    assert database.get("city")[0].value == "Recife"
    assert database.search(("Recife",), 5)[0].key == "city"
    assert database.forget("city") == [record.id]
    assert database.get("city") == []
    database.close()


def test_remember_recall_search_and_forget(tmp_path):
    memory = manager(tmp_path)
    saved = memory.remember(MemoryType.PREFERENCE, "preferred_browser", "Chrome", tags=("browser",))
    assert memory.recall("preferred_browser")[0].id == saved.id
    assert memory.search("navegador browser")[0].value == "Chrome"
    assert memory.forget("preferred_browser") == 1
    assert memory.recall("preferred_browser") == []
    memory.close()


def test_duplicate_key_updates_without_duplicate_row(tmp_path):
    memory = manager(tmp_path)
    first = memory.remember("PREFERENCE", "preferred_browser", "Chrome")
    second = memory.remember("PREFERENCE", "preferred_browser", "Firefox")
    assert second.id == first.id
    assert memory.recall("preferred_browser")[0].value == "Firefox"
    assert len(memory.list_memories()) == 1
    memory.close()


def test_persistence_across_instances(tmp_path):
    settings = config(tmp_path / "persistent.db")
    first = MemoryManager(settings)
    first.remember(MemoryType.PROJECT, "current_project_name", "ISABELLA")
    first.close()
    second = MemoryManager(settings)
    assert second.recall("current_project_name")[0].value == "ISABELLA"
    second.close()


def test_working_memory_is_bounded_and_never_persisted(tmp_path):
    memory = manager(tmp_path, working_limit=3)
    for index in range(5):
        memory.add_working_message("user", str(index))
    assert [item.text for item in memory.working_memory] == ["2", "3", "4"]
    assert memory.list_memories() == []
    memory.clear_working_memory()
    assert not memory.working_memory
    memory.close()


def test_invalid_type_and_secret_are_rejected(tmp_path):
    memory = manager(tmp_path)
    with pytest.raises(MemoryError, match="Invalid memory type"):
        memory.remember("UNKNOWN", "item", "value")
    with pytest.raises(SecretMemoryError):
        memory.remember(MemoryType.FACT, "api_token", "abc123")
    assert memory.list_memories() == []
    memory.close()


def test_corrupt_database_degrades_without_breaking_conversation(tmp_path):
    database_path = tmp_path / "corrupt.db"
    database_path.write_bytes(b"not a sqlite database")
    config_path = tmp_path / "memory.json"
    config_path.write_text(json.dumps(config(database_path)), encoding="utf-8")
    memory = MemoryManager.from_config(config_path)
    assert memory.status == "ERROR"
    response = Brain(FakeLLM(), memory=memory).process("Olá")
    assert response.message == "Resposta básica."


def test_runtime_database_failure_degrades_and_brain_stays_available(tmp_path, monkeypatch):
    memory = manager(tmp_path)
    monkeypatch.setattr(memory.database, "search", lambda *args: (_ for _ in ()).throw(OSError("disk unavailable")))
    brain = Brain(FakeLLM(), memory=memory)
    assert brain.process("Olá novamente").message == "Resposta básica."
    assert memory.status == "ERROR"
    brain.shutdown()


@pytest.mark.parametrize(
    ("text", "kind", "key", "value"),
    [
        ("Lembre que meu navegador preferido é Chrome.", MemoryType.PREFERENCE, "preferred_browser", "Chrome"),
        ("Lembre que o projeto atual se chama ISABELLA.", MemoryType.PROJECT, "current_project_name", "ISABELLA"),
    ],
)
def test_explicit_memory_parser(text, kind, key, value):
    parsed = parse_remember(text)
    assert parsed[:3] == (kind, key, value)


def test_brain_remember_recall_forget_flow(tmp_path):
    memory = manager(tmp_path)
    brain = Brain(FakeLLM(), memory=memory)
    assert brain.process("Lembre que meu navegador preferido é Chrome.").message == "Vou lembrar disso."
    assert brain.process("Qual navegador eu prefiro?").message == "Você prefere Chrome."
    assert "Esqueci" in brain.process("Esqueça qual é meu navegador preferido.").message
    assert "ainda não" in brain.process("Qual navegador eu prefiro?").message
    brain.shutdown()


def test_safe_recall_question_returns_only_relevant_memory(tmp_path):
    memory = manager(tmp_path)
    memory.remember(MemoryType.PREFERENCE, "preferred_browser", "Chrome", tags=("browser", "preference"))
    memory.remember(MemoryType.FACT, "favorite_color", "Azul", tags=("color",))
    brain = Brain(FakeLLM(), memory=memory)
    response = brain.process("O que você lembra sobre meu navegador?")
    assert "Chrome" in response.message
    assert "Azul" not in response.message
    brain.shutdown()


def test_brain_refuses_explicit_secret(tmp_path):
    memory = manager(tmp_path)
    brain = Brain(FakeLLM(), memory=memory)
    response = brain.process("Lembre que minha senha é abc123.")
    assert "não posso guardar" in response.message.lower()
    assert memory.list_memories() == []
    brain.shutdown()


def test_preferred_browser_is_resolved_before_skill(tmp_path):
    memory = manager(tmp_path)
    memory.remember(MemoryType.PREFERENCE, "preferred_browser", "chrome")
    registry = CapturingRegistry()
    brain = Brain(FakeLLM(), registry=registry, memory=memory)
    response = brain.process("Abra meu navegador.")
    assert response.response_type is Intent.SINGLE_SKILL
    assert registry.calls == [("applications.open", {"name": "chrome"})]
    brain.shutdown()


def test_working_memory_answers_session_topic_without_persistence(tmp_path):
    memory = manager(tmp_path)
    brain = Brain(FakeLLM(), memory=memory)
    brain.process("Estou falando sobre Marte.")
    response = brain.process("Qual planeta estamos discutindo?")
    assert response.message == "Estamos falando sobre Marte."
    assert memory.list_memories() == []
    brain.shutdown()


def test_only_relevant_limited_context_is_sent_to_llm(tmp_path):
    memory = manager(tmp_path)
    for index in range(8):
        memory.remember(MemoryType.FACT, f"topic_{index}", f"valor {index}", tags=("topic",))
    llm = FakeLLM()
    brain = Brain(llm, memory=memory)
    brain.process("Conte sobre topic")
    assert llm.prompts[-1].count("- topic_") == 5
    brain.shutdown()


def test_memory_operation_metrics_are_recorded(tmp_path):
    memory = manager(tmp_path)
    memory.remember(MemoryType.FACT, "language", "Português")
    memory.recall("language")
    memory.search("language")
    assert all(memory.metrics[name] for name in ("write_ms", "recall_ms", "retrieval_ms"))
    memory.close()
