"""Main coordinator for text intelligence."""

import logging
from pathlib import Path
from time import perf_counter

from .llm import OllamaProvider, ProviderUnavailableError, load_intelligence_config
from .models import BrainResponse, Intent, Plan, SkillRequest
from .planner import Planner
from .router import Router
from Isabella.Skills import SkillRegistry, build_default_registry
from Isabella.Skills.base import SkillResult


LOGGER = logging.getLogger("BRAIN")


class Brain:
    def __init__(self, llm: OllamaProvider, router: Router | None = None, planner: Planner | None = None, registry: SkillRegistry | None = None) -> None:
        self.llm = llm
        self.router = router or Router()
        self.planner = planner or Planner(router=self.router)
        self.registry = registry
        self.latencies_ms: list[float] = []

    @classmethod
    def from_config(cls, path: Path | None = None) -> "Brain":
        config = load_intelligence_config(path)
        router = Router()
        return cls(
            OllamaProvider(config),
            router=router,
            planner=Planner(max_steps=int(config["max_plan_steps"]), router=router),
            registry=build_default_registry(),
        )

    def process(self, text: str, intent: Intent | None = None) -> BrainResponse:
        started = perf_counter()
        intent = intent or self.router.route(text)
        if intent == Intent.CONVERSATION:
            try:
                message = self.llm.chat(text)
            except ProviderUnavailableError:
                LOGGER.error("Intelligence provider is unavailable")
                message = "O provedor de inteligência está indisponível no momento."
            result = BrainResponse(intent, message)
        elif intent == Intent.SINGLE_SKILL:
            request = self.router.skill_request(text)
            if self.registry:
                skill_result = self.registry.execute(request.skill, request.arguments)
                result = BrainResponse(intent, skill_result.message, skill_request=request, skill_results=(skill_result,))
            else:
                result = BrainResponse(intent, "Ação identificada, mas não executada.", skill_request=request)
        else:
            plan = self.planner.plan(text)
            skill_results = self._execute_plan(plan) if self.registry and not plan.error else ()
            message = skill_results[-1].message if skill_results else (plan.error or "Plano criado, mas não executado.")
            result = BrainResponse(intent, message, plan=plan, skill_results=skill_results)
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("response_type=%s latency_ms=%.3f", result.response_type.value, latency)
        return result

    def confirm(self, request: SkillRequest) -> SkillResult:
        if self.registry is None:
            raise RuntimeError("Skill registry is not configured")
        return self.registry.execute(request.skill, request.arguments, confirmed=True)

    def _execute_plan(self, plan: Plan) -> tuple[SkillResult, ...]:
        results: list[SkillResult] = []
        succeeded: set[int] = set()
        for step in plan.steps:
            if any(dependency not in succeeded for dependency in step.depends_on):
                results.append(SkillResult(False, step.skill, "Dependência anterior falhou.", error_code="DEPENDENCY_FAILED", status="skipped"))
                break
            result = self.registry.execute(step.skill, step.arguments)
            results.append(result)
            if result.status == "confirmation_required" or not result.success:
                break
            succeeded.add(step.id)
        return tuple(results)

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
