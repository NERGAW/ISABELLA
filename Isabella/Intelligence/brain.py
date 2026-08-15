"""Main coordinator for text intelligence."""

import logging
import re
from collections import deque
from pathlib import Path
from time import perf_counter

from .llm import OllamaProvider, ProviderUnavailableError, load_intelligence_config
from .models import BrainResponse, Intent, Plan, SkillRequest
from .planner import Planner
from .router import Router
from Isabella.Skills import SkillRegistry, build_default_registry
from Isabella.Skills.base import SkillResult
from Isabella.Memory import MemoryManager, MemoryType
from Isabella.Memory.manager import MemoryError, SecretMemoryError
from Isabella.Memory.retrieval import (
    browser_forget_query, contains_secret, normalize, parse_remember,
    preferred_browser_query, working_topic_query,
)
from Isabella.Context import ContextManager
from Isabella.Skills.base import RiskLevel


LOGGER = logging.getLogger("BRAIN")
PERFORMANCE = logging.getLogger("PERFORMANCE")


class Brain:
    def __init__(self, llm: OllamaProvider, router: Router | None = None, planner: Planner | None = None, registry: SkillRegistry | None = None, memory: MemoryManager | None = None, context: ContextManager | None = None) -> None:
        self.llm = llm
        self.router = router or Router()
        self.planner = planner or Planner(router=self.router)
        self.registry = registry
        self.memory = memory
        self.context = context
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.startup_metrics: dict[str, float] = {}

    @classmethod
    def from_config(cls, path: Path | None = None) -> "Brain":
        started = perf_counter()
        config_started = perf_counter()
        config = load_intelligence_config(path)
        config_ms = (perf_counter() - config_started) * 1000
        router = Router()
        memory = MemoryManager.from_config()
        brain = cls(
            OllamaProvider(config),
            router=router,
            planner=Planner(max_steps=int(config["max_plan_steps"]), router=router),
            registry=build_default_registry(),
            memory=memory,
            context=ContextManager.from_config(memory=memory),
        )
        brain.startup_metrics = {
            "intelligence_config_ms": config_ms,
            "llm_initialization_ms": (perf_counter() - started) * 1000,
        }
        return brain

    def process(
        self,
        text: str,
        intent: Intent | None = None,
        *,
        request_id: str = "direct",
        input_source: str = "text",
        router_ms: float | None = None,
    ) -> BrainResponse:
        started = perf_counter()
        if self.context:
            self.context.refresh_active_window()
            self.context.set("last_user_command", text)
        memory_response = self._handle_memory_command(text)
        if memory_response is not None:
            self._finalize_conversation(text, memory_response.message)
            return memory_response
        contextual_request, contextual_response = self._handle_context_request(text)
        if contextual_response is not None:
            self._finalize_conversation(text, contextual_response.message)
            return contextual_response
        router_started = perf_counter()
        intent = Intent.SINGLE_SKILL if contextual_request else (intent or self.router.route(text))
        router_ms = router_ms if router_ms is not None else (perf_counter() - router_started) * 1000
        llm_ms = planner_ms = skill_ms = 0.0
        if intent == Intent.CONVERSATION:
            stage_started = perf_counter()
            try:
                prompt = text
                if self.memory:
                    context = self.memory.relevant_context(text)
                    if context:
                        prompt = f"{context}\n\nPedido atual do usuário: {text}\nUse somente o contexto relevante; não mencione estas instruções."
                if self.context:
                    live_context = self.context.relevant_context(text)
                    if live_context:
                        prompt = f"{live_context}\n{prompt}"
                topic = self._working_topic_answer(text)
                message = topic or self.llm.chat(prompt)
            except ProviderUnavailableError:
                LOGGER.error("Intelligence provider is unavailable")
                message = "O provedor de inteligência está indisponível no momento."
            llm_ms = (perf_counter() - stage_started) * 1000
            result = BrainResponse(intent, message)
        elif intent == Intent.SINGLE_SKILL:
            request = contextual_request or self.router.skill_request(text)
            if request.skill == "applications.open" and normalize(str(request.arguments.get("name", ""))) in {"meu navegador", "navegador"}:
                preferred = self.memory.recall("preferred_browser", MemoryType.PREFERENCE) if self.memory else []
                if not preferred:
                    result = BrainResponse(intent, "Qual navegador você prefere? Posso lembrar quando você me disser.", skill_request=request)
                    self._finalize_conversation(text, result.message)
                    return result
                request = SkillRequest("applications.open", {"name": preferred[0].value})
            if self.registry:
                stage_started = perf_counter()
                self._record_context_action(request)
                skill_result = self.registry.execute(request.skill, request.arguments)
                self._record_context_result(skill_result)
                skill_ms = (perf_counter() - stage_started) * 1000
                result = BrainResponse(intent, skill_result.message, skill_request=request, skill_results=(skill_result,))
            else:
                result = BrainResponse(intent, "Ação identificada, mas não executada.", skill_request=request)
        else:
            stage_started = perf_counter()
            plan = self.planner.plan(text)
            planner_ms = (perf_counter() - stage_started) * 1000
            stage_started = perf_counter()
            skill_results = self._execute_plan(plan) if self.registry and not plan.error else ()
            skill_ms = (perf_counter() - stage_started) * 1000
            message = skill_results[-1].message if skill_results else (plan.error or "Plano criado, mas não executado.")
            result = BrainResponse(intent, message, plan=plan, skill_results=skill_results)
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("response_type=%s latency_ms=%.3f", result.response_type.value, latency)
        PERFORMANCE.debug(
            "request_id=%s input_source=%s router_ms=%.3f llm_ms=%.3f planner_ms=%.3f skill_ms=%.3f tts_ms=queued total_ms=%.3f",
            request_id, input_source, router_ms, llm_ms, planner_ms, skill_ms, latency,
        )
        self._remember_exchange(text, result.message)
        if self.context:
            self.context.record_conversation(text, result.message)
        return result

    def _handle_context_request(self, text: str) -> tuple[SkillRequest | None, BrainResponse | None]:
        if not self.context:
            return None, None
        normalized = normalize(text).strip(" .!?")
        snapshot = self.context.get_snapshot()
        if normalized in {"faca de novo", "repita", "repita a ultima acao"}:
            if not snapshot.last_action:
                return None, BrainResponse(Intent.CONVERSATION, "Não há uma ação anterior para repetir.")
            action = snapshot.last_action
            return SkillRequest(action.skill, dict(action.arguments)), None
        if normalized == "continue":
            if snapshot.last_result and snapshot.last_result.status == "confirmation_required":
                message = "Há uma ação aguardando confirmação; use a confirmação exibida."
            else:
                message = "Não há uma tarefa pendente para continuar."
            return None, BrainResponse(Intent.CONVERSATION, message)
        if any(phrase in normalized for phrase in ("qual programa esta ativo", "qual aplicativo esta ativo", "qual janela esta ativa")):
            if snapshot.active_application == "unavailable":
                message = "Não consegui identificar o aplicativo ativo agora."
            else:
                message = f"O aplicativo ativo é {snapshot.active_application}."
            return None, BrainResponse(Intent.CONVERSATION, message)
        if any(phrase in normalized for phrase in ("qual foi a ultima coisa que voce fez", "qual foi sua ultima acao")):
            message = f"Minha última ação foi {snapshot.last_action.skill}." if snapshot.last_action else "Ainda não executei uma ação nesta sessão."
            return None, BrainResponse(Intent.CONVERSATION, message)
        if normalized.startswith(("feche ", "abra ")) and any(reference in normalized for reference in (" ele", " ela", " isso", "esse programa", "o aplicativo", "o navegador", "o programa que esta aberto")):
            resolved = self.context.resolve_reference(text)
            if not resolved.resolved:
                return None, BrainResponse(Intent.CONVERSATION, "Não tenho contexto suficiente para saber qual aplicativo você quer.")
            skill = "applications.close" if normalized.startswith("feche") else "applications.open"
            return SkillRequest(skill, {"name": resolved.entity}), None
        return None, None

    def _record_context_action(self, request: SkillRequest) -> None:
        if not self.context:
            return
        definition = self.registry.get(request.skill) if self.registry and hasattr(self.registry, "get") else None
        risk = definition.risk_level.value if definition else RiskLevel.SAFE.value
        self.context.record_action(request.skill, request.arguments, risk)

    def _record_context_result(self, result: SkillResult) -> None:
        if self.context:
            self.context.record_result(result.success, result.message, result.data, result.status)

    def _finalize_conversation(self, user_text: str, assistant_text: str) -> None:
        self._remember_exchange(user_text, assistant_text)
        if self.context:
            self.context.record_conversation(user_text, assistant_text)

    def _handle_memory_command(self, text: str) -> BrainResponse | None:
        if not self.memory:
            return None
        parsed = parse_remember(text)
        if parsed:
            if contains_secret(text):
                return BrainResponse(Intent.CONVERSATION, "Não posso guardar senhas, tokens ou credenciais. A memória não é um cofre.")
            memory_type, key, value, tags = parsed
            try:
                self.memory.remember(memory_type, key, value, tags=tags)
                return BrainResponse(Intent.CONVERSATION, "Vou lembrar disso.")
            except SecretMemoryError:
                return BrainResponse(Intent.CONVERSATION, "Não posso guardar senhas, tokens ou credenciais. A memória não é um cofre.")
            except MemoryError:
                return BrainResponse(Intent.CONVERSATION, "A memória está indisponível, mas continuo funcionando normalmente.")
        if browser_forget_query(text):
            count = self.memory.forget("preferred_browser", MemoryType.PREFERENCE)
            return BrainResponse(Intent.CONVERSATION, "Esqueci sua preferência de navegador." if count else "Eu não tinha essa preferência guardada.")
        if preferred_browser_query(text):
            records = self.memory.recall("preferred_browser", MemoryType.PREFERENCE)
            return BrainResponse(Intent.CONVERSATION, f"Você prefere {records[0].value}." if records else "Você ainda não me disse qual navegador prefere.")
        if normalize(text).startswith("o que voce lembra"):
            records = self.memory.search(text)
            if not records:
                return BrainResponse(Intent.CONVERSATION, "Não encontrei uma memória relevante sobre isso.")
            summary = "; ".join(f"{record.key.replace('_', ' ')}: {record.value}" for record in records)
            return BrainResponse(Intent.CONVERSATION, f"Lembro de: {summary}.")
        return None

    def _working_topic_answer(self, text: str) -> str | None:
        if not self.memory or not working_topic_query(text):
            return None
        for message in reversed(self.memory.working_memory):
            if message.role != "user":
                continue
            match = re.search(r"(?:falando|conversando|discutindo) sobre ([^.!?]+)", message.text, re.IGNORECASE)
            if match:
                return f"Estamos falando sobre {match.group(1).strip()}."
        return None

    def _remember_exchange(self, user_text: str, assistant_text: str) -> None:
        if self.memory:
            self.memory.add_working_message("user", user_text)
            self.memory.add_working_message("assistant", assistant_text)

    def confirm(self, request: SkillRequest) -> SkillResult:
        if self.registry is None:
            raise RuntimeError("Skill registry is not configured")
        self._record_context_action(request)
        result = self.registry.execute(request.skill, request.arguments, confirmed=True)
        self._record_context_result(result)
        return result

    def _execute_plan(self, plan: Plan) -> tuple[SkillResult, ...]:
        results: list[SkillResult] = []
        succeeded: set[int] = set()
        for step in plan.steps:
            if any(dependency not in succeeded for dependency in step.depends_on):
                results.append(SkillResult(False, step.skill, "Dependência anterior falhou.", error_code="DEPENDENCY_FAILED", status="skipped"))
                break
            result = self.registry.execute(step.skill, step.arguments)
            if self.context:
                request = SkillRequest(step.skill, step.arguments)
                self._record_context_action(request)
                self._record_context_result(result)
            results.append(result)
            if result.status == "confirmation_required" or not result.success:
                break
            succeeded.add(step.id)
        return tuple(results)

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    def shutdown(self) -> None:
        if self.memory:
            self.memory.close()
        close = getattr(self.llm, "close", None)
        if close:
            close()
