"""Main coordinator for text intelligence."""

import logging
from pathlib import Path
from time import perf_counter

from .llm import OllamaProvider, ProviderUnavailableError, load_intelligence_config
from .models import BrainResponse, Intent
from .planner import Planner
from .router import Router


LOGGER = logging.getLogger("BRAIN")


class Brain:
    def __init__(self, llm: OllamaProvider, router: Router | None = None, planner: Planner | None = None) -> None:
        self.llm = llm
        self.router = router or Router()
        self.planner = planner or Planner(router=self.router)
        self.latencies_ms: list[float] = []

    @classmethod
    def from_config(cls, path: Path | None = None) -> "Brain":
        config = load_intelligence_config(path)
        router = Router()
        return cls(
            OllamaProvider(config),
            router=router,
            planner=Planner(max_steps=int(config["max_plan_steps"]), router=router),
        )

    def process(self, text: str) -> BrainResponse:
        started = perf_counter()
        intent = self.router.route(text)
        if intent == Intent.CONVERSATION:
            try:
                message = self.llm.chat(text)
            except ProviderUnavailableError:
                LOGGER.error("Intelligence provider is unavailable")
                message = "O provedor de inteligência está indisponível no momento."
            result = BrainResponse(intent, message)
        elif intent == Intent.SINGLE_SKILL:
            request = self.router.skill_request(text)
            result = BrainResponse(intent, "Ação identificada, mas não executada.", skill_request=request)
        else:
            plan = self.planner.plan(text)
            result = BrainResponse(intent, "Plano criado, mas não executado.", plan=plan)
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("response_type=%s latency_ms=%.3f", result.response_type.value, latency)
        return result

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
