"""Service registration and deterministic dependency ordering."""

from __future__ import annotations

from .service import Service


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Service] = {}

    def register(self, service: Service) -> None:
        if service.name in self._services:
            raise ValueError(f"Service already registered: {service.name}")
        self._services[service.name] = service

    def get(self, name: str) -> Service | None:
        return self._services.get(name)

    def list(self) -> list[Service]:
        return list(self._services.values())

    def startup_order(self, enabled: set[str] | None = None) -> list[Service]:
        selected = {
            name: service for name, service in self._services.items()
            if enabled is None or name in enabled or service.required
        }
        for service in selected.values():
            missing = [name for name in service.dependencies if name not in selected]
            if missing:
                raise ValueError(f"Missing dependencies for {service.name}: {', '.join(missing)}")
        result: list[Service] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(name: str) -> None:
            if name in permanent:
                return
            if name in temporary:
                raise ValueError("Cyclic service dependency")
            temporary.add(name)
            for dependency in selected[name].dependencies:
                visit(dependency)
            temporary.remove(name)
            permanent.add(name)
            result.append(selected[name])

        for name in selected:
            visit(name)
        return result

