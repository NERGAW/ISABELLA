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
from Isabella.Skills import SkillRegistry, build_default_registry, create_automation_skills, create_diagnostics_skill, create_scheduler_skills, create_mode_skill
from Isabella.Skills.base import SkillResult
from Isabella.Memory import MemoryManager, MemoryType
from Isabella.Memory.manager import MemoryError, SecretMemoryError
from Isabella.Memory.retrieval import (
    browser_forget_query, contains_secret, normalize, parse_remember,
    preferred_browser_query, working_topic_query,
)
from Isabella.Context import ContextManager
from Isabella.Skills.base import RiskLevel
from Isabella.Vision import VisionManager
from Isabella.Events import EventPriority, EventType, reset_correlation_id, set_correlation_id
from Isabella.Security import ConfirmationRequest, SecurityPolicyEngine
from Isabella.Diagnostics import DiagnosticsManager


LOGGER = logging.getLogger("BRAIN")
PERFORMANCE = logging.getLogger("PERFORMANCE")


class Brain:
    def __init__(self, llm: OllamaProvider, router: Router | None = None, planner: Planner | None = None, registry: SkillRegistry | None = None, memory: MemoryManager | None = None, context: ContextManager | None = None, vision: VisionManager | None = None, event_bus=None, security=None, diagnostics=None, mcp=None, research=None, skillforge=None, automations=None, scheduler=None, api=None, nodes=None, transport=None, sessions=None, notifications=None, home=None, modes=None, orchestrator=None) -> None:
        self.llm = llm
        self.router = router or Router()
        self.event_bus = event_bus
        self.security = security or getattr(registry, "policy_engine", None)
        self.diagnostics = diagnostics
        self.mcp = mcp
        self.research = research
        self.skillforge = skillforge
        self.automations = automations
        self.scheduler = scheduler
        self.api = api
        self.nodes = nodes
        self.transport = transport
        self.sessions = sessions
        self.notifications = notifications
        self.home = home
        self.modes = modes
        self.orchestrator = orchestrator
        self.planner = planner or Planner(router=self.router, event_bus=event_bus)
        self.registry = registry
        self.memory = memory
        self.context = context
        self.vision = vision
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.startup_metrics: dict[str, float] = {}

    @classmethod
    def from_config(cls, path: Path | None = None, event_bus=None) -> "Brain":
        started = perf_counter()
        config_started = perf_counter()
        config = load_intelligence_config(path)
        config_ms = (perf_counter() - config_started) * 1000
        router = Router()
        memory = MemoryManager.from_config(event_bus=event_bus)
        context = ContextManager.from_config(memory=memory, event_bus=event_bus)
        vision = VisionManager.from_config(context=context, event_bus=event_bus)
        security = SecurityPolicyEngine.from_config(event_bus=event_bus)
        registry = build_default_registry(vision, event_bus=event_bus, policy_engine=security)
        brain = cls(
            OllamaProvider(config),
            router=router,
            planner=Planner(max_steps=int(config["max_plan_steps"]), router=router, event_bus=event_bus),
            registry=registry,
            memory=memory,
            context=context,
            vision=vision,
            event_bus=event_bus,
            security=security,
        )
        from Isabella.Modes import ModeManager
        brain.modes = ModeManager.from_config(event_bus=event_bus, context=context)
        registry.register(create_mode_skill(brain.modes))
        from Isabella.Agents import AgentOrchestrator
        brain.orchestrator = AgentOrchestrator(event_bus=event_bus, max_agent_hops=3)
        from Isabella.MCP import MCPManager
        brain.mcp = MCPManager.from_config(skill_registry=registry, event_bus=event_bus)
        from Isabella.Research import ResearchManager
        brain.research = ResearchManager.from_config(llm=brain.llm, event_bus=event_bus)
        from Isabella.SkillForge import SkillForgeManager
        brain.skillforge = SkillForgeManager.from_config(registry=registry, event_bus=event_bus)
        from Isabella.Automations import AutomationManager
        brain.automations = AutomationManager.from_config(registry=registry, event_bus=event_bus)
        for definition in create_automation_skills(brain.automations):
            registry.register(definition)
        from Isabella.Scheduler import SchedulerManager
        brain.scheduler = SchedulerManager.from_config(registry=registry, event_bus=event_bus)
        for definition in create_scheduler_skills(brain.scheduler):
            registry.register(definition)
        diagnostics = DiagnosticsManager.from_config(brain=brain, event_bus=event_bus)
        brain.diagnostics = diagnostics
        registry.register(create_diagnostics_skill(diagnostics))
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
        token = set_correlation_id(request_id)
        if self.event_bus:
            self.event_bus.emit(EventType.BRAIN_STARTED, "brain", {"input_source": input_source})
        try:
            response = self._process_internal(text, intent, request_id=request_id, input_source=input_source, router_ms=router_ms)
            if self.event_bus:
                self.event_bus.emit(
                    EventType.BRAIN_COMPLETED, "brain",
                    {"response_type": response.response_type.value, "status": "completed"},
                )
            return response
        except Exception as exc:
            if self.event_bus:
                self.event_bus.emit(
                    EventType.BRAIN_FAILED, "brain", {"error": type(exc).__name__},
                    priority=EventPriority.HIGH,
                )
            raise
        finally:
            reset_correlation_id(token)

    def _process_internal(
        self, text: str, intent: Intent | None = None, *, request_id: str = "direct",
        input_source: str = "text", router_ms: float | None = None,
    ) -> BrainResponse:
        if not self.orchestrator:
            return self._process_without_agents(text, intent, request_id=request_id, input_source=input_source, router_ms=router_ms)
        mode = self.modes.apply_policy(input_source=input_source).mode_id if self.modes else "NORMAL"
        selected = self.orchestrator.select(text, intent=intent, mode=mode)
        if not selected:
            return self._process_without_agents(text, intent, request_id=request_id, input_source=input_source, router_ms=router_ms)
        snapshot = self.context.get_snapshot() if self.context else None
        shared = vars(snapshot) if snapshot else {}
        if selected == ("VISION_AGENT", "RESEARCH_AGENT"):
            return self._process_visual_research(text, selected, shared)
        outputs, failure = self.orchestrator.execute(
            selected, text,
            lambda _agent, _task: self._process_without_agents(text, intent, request_id=request_id, input_source=input_source, router_ms=router_ms),
            context=shared,
        )
        if failure:
            return BrainResponse(Intent.CONVERSATION, "A especialização interna falhou, mas o Core continua disponível.")
        return outputs[-1]

    def _process_visual_research(self, text: str, selected, shared) -> BrainResponse:
        state = {}
        def handle(agent, _task):
            if agent.id == "VISION_AGENT":
                result = self.vision.analyze_screen(text, active_window=True)
                if not result.success: raise RuntimeError(result.error_code or "VISION_FAILED")
                state["vision"] = result.message
                return result.message
            policy = self.modes.apply_policy() if self.modes else None
            if policy and not policy.research_allowed: raise PermissionError("RESEARCH_DISABLED_BY_MODE")
            result = self.research.search(f"{text}\nContexto visual observado: {state['vision']}")
            state["research"] = result
            return result.answer
        outputs, failure = self.orchestrator.execute(selected, text, handle, context=shared)
        if failure:
            message = state.get("vision") or "Não foi possível concluir a análise visual e a pesquisa."
            return BrainResponse(Intent.VISION, message)
        research = state["research"]
        return BrainResponse(Intent.RESEARCH, f"{state['vision']}\n\n{research.answer}", sources=research.sources)

    def _process_without_agents(
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
        schedule_response = self._handle_schedule_command(text, request_id)
        if schedule_response is not None:
            self._finalize_conversation(text, schedule_response.message)
            return schedule_response
        memory_response = self._handle_memory_command(text)
        if memory_response is not None:
            self._finalize_conversation(text, memory_response.message)
            return memory_response
        contextual_request, contextual_response = self._handle_context_request(text)
        if contextual_response is not None:
            self._finalize_conversation(text, contextual_response.message)
            return contextual_response
        vision_request, vision_response = self._handle_vision_request(text)
        if vision_response is not None:
            self._finalize_conversation(text, vision_response.message)
            return vision_response
        contextual_request = contextual_request or vision_request
        router_started = perf_counter()
        intent = Intent.SINGLE_SKILL if contextual_request else (intent or self.router.route(text))
        mode_policy = self.modes.apply_policy(input_source=input_source) if self.modes else None
        if intent == Intent.CONVERSATION and self.research and (not mode_policy or mode_policy.research_allowed) and self.research.should_search(text):
            intent = Intent.RESEARCH
        if intent == Intent.RESEARCH and mode_policy and not mode_policy.research_allowed:
            result = BrainResponse(Intent.CONVERSATION, f"Pesquisa externa está desabilitada no modo {mode_policy.mode_id}.")
            self._finalize_conversation(text, result.message)
            return result
        router_ms = router_ms if router_ms is not None else (perf_counter() - router_started) * 1000
        llm_ms = planner_ms = skill_ms = research_ms = 0.0
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
        elif intent == Intent.RESEARCH:
            stage_started = perf_counter()
            if self.research:
                research_result = self.research.search(text)
                result = BrainResponse(intent, research_result.answer, sources=research_result.sources)
            else:
                result = BrainResponse(intent, "A pesquisa web está indisponível no momento.")
            research_ms = (perf_counter() - stage_started) * 1000
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
                skill_result = self._execute_skill(request.skill, request.arguments, request_id)
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
            skill_results = self._execute_plan(plan, request_id) if self.registry and not plan.error else ()
            skill_ms = (perf_counter() - stage_started) * 1000
            message = skill_results[-1].message if skill_results else (plan.error or "Plano criado, mas não executado.")
            result = BrainResponse(intent, message, plan=plan, skill_results=skill_results)
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("response_type=%s latency_ms=%.3f", result.response_type.value, latency)
        PERFORMANCE.debug(
            "request_id=%s input_source=%s router_ms=%.3f llm_ms=%.3f research_ms=%.3f planner_ms=%.3f skill_ms=%.3f tts_ms=queued total_ms=%.3f",
            request_id, input_source, router_ms, llm_ms, research_ms, planner_ms, skill_ms, latency,
        )
        self._remember_exchange(text, result.message)
        if self.context:
            self.context.record_conversation(text, result.message)
        return result

    def _handle_schedule_command(self, text: str, request_id: str) -> BrainResponse | None:
        if not self.scheduler:
            return None
        normalized = normalize(text)
        temporal = any(
            re.search(pattern, normalized) for pattern in (
                r"\bdaqui a \d+ (?:minuto|hora)s?\b", r"\bamanha\b",
                r"\btod(?:o|os) (?:os )?dias?\b", r"\bas \d{1,2}(?::\d{2})?(?: horas?)?\b",
            )
        )
        if not temporal:
            return None
        try:
            schedule = self.scheduler.parse_natural_schedule(text)
        except ValueError as exc:
            return BrainResponse(Intent.CONVERSATION, str(exc))

        skill = None
        arguments: dict[str, object] = {}
        name = "Tarefa agendada"
        if "lembre" in normalized:
            reminder = re.sub(r"^(?:isabella[,.]?\s*)?(?:me\s+)?lembre(?:-me)?(?:\s+de)?\s*", "", text.strip(), flags=re.IGNORECASE)
            reminder = re.split(r"\b(?:daqui\s+a|amanh[ãa]|(?:à|a)s?\s+\d{1,2}|todo(?:s)?\s+(?:os\s+)?dias?)\b", reminder, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,.")
            if not reminder:
                return BrainResponse(Intent.CONVERSATION, "O que você quer que eu lembre?")
            skill, arguments, name = "scheduler.reminder", {"text": reminder}, f"Lembrete: {reminder}"
        elif "deslig" in normalized:
            skill, name = "system.shutdown", "Desligar o computador"
        elif "reinici" in normalized:
            skill, name = "system.restart", "Reiniciar o computador"
        elif any(item in normalized for item in ("abra", "abre", "abrir", "inicie")):
            request = self.router.skill_request(text)
            skill, arguments, name = request.skill, request.arguments, f"Executar {request.skill}"
        elif "ambiente de desenvolvimento" in normalized and self.registry.exists("custom.prepare_work"):
            skill, name = "custom.prepare_work", "Ambiente de desenvolvimento"
        if not skill:
            return BrainResponse(Intent.CONVERSATION, "Entendi o horário, mas preciso que você especifique uma ação autorizada.")
        specification = {
            **schedule, "name": name, "skill": skill, "arguments": arguments,
            "enabled": True,
        }
        if skill == "scheduler.reminder":
            specification["reminder_text"] = arguments["text"]
        request = SkillRequest("scheduler.create", {"specification": specification})
        result = self._execute_skill(request.skill, request.arguments, request_id)
        return BrainResponse(Intent.SINGLE_SKILL, result.message, skill_request=request, skill_results=(result,))

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

    def _handle_vision_request(self, text: str) -> tuple[SkillRequest | None, BrainResponse | None]:
        normalized = normalize(text)
        asks_about_screen = any(
            phrase in normalized for phrase in (
                "o que esta aparecendo", "o que aparece", "o que tem na tela", "o que tem em minha tela",
                "olhe minha tela", "olhe a minha tela", "analise minha tela", "descreva a tela", "descreva minha tela",
                "que erro e esse", "que erro eh esse", "o que significa esse erro", "o que significa essa mensagem",
                "resuma o que esta aberto",
            )
        )
        if not asks_about_screen:
            return None, None
        policy = self.modes.apply_policy() if self.modes else None
        if policy and policy.network_policy == "local_only" and not bool(getattr(self.vision, "config", {}).get("provider_local", False)):
            return None, BrainResponse(Intent.VISION, f"Vision cloud está desabilitada no modo {policy.mode_id}.")
        if not self.vision:
            return None, BrainResponse(Intent.VISION, "Vision está indisponível no momento.")
        follow_up = any(reference in normalized for reference in ("esse erro", "essa mensagem", "isso"))
        recent = self.vision.recent_analysis() if follow_up else None
        if recent:
            message = recent.to_message()
            if recent.errors:
                message = f"Na análise recente, identifiquei: {recent.errors[0]}. {message}"
            return None, BrainResponse(Intent.VISION, message)
        active_window = "janela" in normalized or "erro" in normalized or "mensagem" in normalized
        result = self.vision.analyze_screen(text, active_window=active_window)
        return None, BrainResponse(Intent.VISION, result.message)

    def _record_context_action(self, request: SkillRequest) -> None:
        if not self.context:
            return
        definition = self.registry.get(request.skill) if self.registry and hasattr(self.registry, "get") else None
        risk = definition.risk_level.value if definition else RiskLevel.SAFE.value
        self.context.record_action(request.skill, request.arguments, risk)

    def _record_context_result(self, result: SkillResult) -> None:
        if self.context:
            self.context.record_result(result.success, result.message, result.data, result.status)

    def _execute_skill(self, skill_id: str, arguments: dict, source_request_id: str) -> SkillResult:
        policy = self.modes.apply_policy() if self.modes else None
        if policy and not policy.allows_skill(skill_id):
            return SkillResult(False, skill_id, f"A Skill está desabilitada no modo {policy.mode_id}.", error_code="MODE_POLICY_DENIED", status="denied")
        try:
            return self.registry.execute(
                skill_id, arguments, source_request_id=source_request_id,
            )
        except TypeError as exc:
            if "source_request_id" not in str(exc):
                raise
            return self.registry.execute(skill_id, arguments)

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

    def pending_confirmation(self, confirmation_id: str) -> ConfirmationRequest | None:
        return self.security.get_pending(confirmation_id) if self.security else None

    def cancel_confirmation(self, confirmation_id: str) -> bool:
        return self.security.cancel(confirmation_id) if self.security else False

    def confirm(self, request: ConfirmationRequest, source: str = "hud") -> SkillResult:
        if self.registry is None:
            raise RuntimeError("Skill registry is not configured")
        skill_request = SkillRequest(request.skill_id, request.arguments)
        self._record_context_action(skill_request)
        result = self.registry.execute(
            request.skill_id, request.arguments,
            source_request_id=request.source_request_id,
            confirmation_id=request.id,
            confirmation_source=source,
        )
        self._record_context_result(result)
        return result

    def _execute_plan(self, plan: Plan, source_request_id: str = "direct") -> tuple[SkillResult, ...]:
        results: list[SkillResult] = []
        succeeded: set[int] = set()
        for step in plan.steps:
            if any(dependency not in succeeded for dependency in step.depends_on):
                results.append(SkillResult(False, step.skill, "Dependência anterior falhou.", error_code="DEPENDENCY_FAILED", status="skipped"))
                break
            result = self._execute_skill(
                step.skill, step.arguments, f"{source_request_id}:step-{step.id}",
            )
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
        if self.scheduler:
            self.scheduler.shutdown()
        if self.automations:
            self.automations.shutdown()
        if self.skillforge:
            self.skillforge.shutdown()
        if self.research:
            self.research.shutdown()
        if self.mcp:
            self.mcp.shutdown()
        if self.diagnostics:
            self.diagnostics.shutdown()
        if self.vision:
            self.vision.shutdown()
        if self.context:
            self.context.shutdown()
        if self.memory:
            self.memory.close()
        close = getattr(self.llm, "close", None)
        if close:
            close()
