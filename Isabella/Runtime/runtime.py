"""Central startup, degraded-mode, restart and shutdown coordination."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .registry import ServiceRegistry
from .service import Service, ServiceState


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "runtime.json"


def load_runtime_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid runtime configuration: {target}") from exc
    required = {"startup_timeout_seconds", "shutdown_timeout_seconds", "restart_attempts", "restart_cooldown_seconds", "enabled_services"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Runtime configuration is missing required fields")
    if not 0.1 <= float(config["startup_timeout_seconds"]) <= 120:
        raise ConfigurationError("Runtime startup timeout is invalid")
    if not 0.1 <= float(config["shutdown_timeout_seconds"]) <= 120:
        raise ConfigurationError("Runtime shutdown timeout is invalid")
    if not 0 <= int(config["restart_attempts"]) <= 5 or not 0 <= float(config["restart_cooldown_seconds"]) <= 60:
        raise ConfigurationError("Runtime restart configuration is invalid")
    if not isinstance(config["enabled_services"], list):
        raise ConfigurationError("Runtime enabled services must be a list")
    return config


class IsabellaRuntime:
    def __init__(self, config: dict[str, Any], *, event_bus=None) -> None:
        self.config = config
        self.event_bus = event_bus
        self.registry = ServiceRegistry()
        self.state = ServiceState.STOPPED
        self.startup_ms = 0.0
        self.shutdown_ms = 0.0
        self._started: list[Service] = []
        self.accepting_commands = False

    @classmethod
    def from_config(cls, path: Path | None = None, **kwargs) -> "IsabellaRuntime":
        return cls(load_runtime_config(path), **kwargs)

    def register(self, service: Service) -> None:
        self.registry.register(service)

    def start(self) -> bool:
        started = perf_counter()
        self.state = ServiceState.STARTING
        enabled = set(self.config["enabled_services"])
        try:
            order = self.registry.startup_order(enabled)
        except ValueError:
            self.state = ServiceState.ERROR
            raise
        for service in order:
            dependencies_ok = all(
                self.registry.get(name).state in {ServiceState.ONLINE, ServiceState.DEGRADED}
                for name in service.dependencies
            )
            if not dependencies_ok:
                service.state = ServiceState.ERROR
                service.last_error = "dependency_unavailable"
                self._emit(EventType.SERVICE_ERROR, service)
                if service.required:
                    self.state = ServiceState.ERROR
                    self._rollback()
                    self.startup_ms = (perf_counter() - started) * 1000
                    return False
                continue
            self._emit(EventType.SERVICE_STARTING, service)
            if service.start(float(self.config["startup_timeout_seconds"])):
                self._started.append(service)
                health_state = service.health_check()
                if health_state is ServiceState.ERROR:
                    self._emit(EventType.SERVICE_ERROR, service)
                    if service.required:
                        self.state = ServiceState.ERROR
                        self._rollback()
                        self.startup_ms = (perf_counter() - started) * 1000
                        return False
                else:
                    self._emit(EventType.SERVICE_ONLINE, service)
            else:
                self._emit(EventType.SERVICE_ERROR, service)
                if service.required:
                    self.state = ServiceState.ERROR
                    self._rollback()
                    self.startup_ms = (perf_counter() - started) * 1000
                    return False
        degraded = any(service.state in {ServiceState.ERROR, ServiceState.DEGRADED} for service in order)
        self.state = ServiceState.DEGRADED if degraded else ServiceState.ONLINE
        self.accepting_commands = True
        self.startup_ms = (perf_counter() - started) * 1000
        self._emit(EventType.RUNTIME_STARTED, payload={"state": self.state.value})
        return True

    def restart_service(self, name: str) -> bool:
        service = self.registry.get(name)
        if service is None:
            return False
        if any(
            self.registry.get(dependency).state not in {ServiceState.ONLINE, ServiceState.DEGRADED}
            for dependency in service.dependencies
        ):
            service.last_error = "dependency_unavailable"
            return False
        self._emit(EventType.SERVICE_RESTARTING, service)
        success = service.restart(
            float(self.config["shutdown_timeout_seconds"]),
            int(self.config["restart_attempts"]),
            float(self.config["restart_cooldown_seconds"]),
        )
        self._emit(EventType.SERVICE_ONLINE if success else EventType.SERVICE_ERROR, service)
        if success and service not in self._started:
            self._started.append(service)
        if not success and not service.required:
            self.state = ServiceState.DEGRADED
        return success

    def shutdown(self) -> bool:
        started = perf_counter()
        self.accepting_commands = False
        self.state = ServiceState.STOPPING
        self._emit(EventType.RUNTIME_STOPPING)
        success = True
        ordered = [service for service in reversed(self._started) if service.name != "Event Bus"]
        event_services = [service for service in reversed(self._started) if service.name == "Event Bus"]
        for service in ordered:
            stopped = service.stop(float(self.config["shutdown_timeout_seconds"]))
            success = stopped and success
            self._emit(EventType.SERVICE_STOPPED if stopped else EventType.SERVICE_ERROR, service)
        self.state = ServiceState.STOPPED if success else ServiceState.ERROR
        self.shutdown_ms = (perf_counter() - started) * 1000
        self._emit(EventType.RUNTIME_STOPPED, payload={"state": self.state.value})
        for service in event_services:
            self._emit(EventType.SERVICE_STOPPED, payload={"service": service.name, "state": ServiceState.STOPPED.value})
            stopped = service.stop(float(self.config["shutdown_timeout_seconds"]))
            success = stopped and success
        self._started.clear()
        self.state = ServiceState.STOPPED if success else ServiceState.ERROR
        return success

    def _rollback(self) -> None:
        for service in reversed(self._started):
            service.stop(float(self.config["shutdown_timeout_seconds"]))
        self._started.clear()

    def report(self) -> dict[str, Any]:
        return {
            "runtime": self.state.value,
            "startup_ms": self.startup_ms,
            "shutdown_ms": self.shutdown_ms,
            "services": {
                service.name: {
                    "state": service.state.value, "required": service.required,
                    "dependencies": list(service.dependencies), "last_error": service.last_error,
                    "startup_ms": service.startup_ms, "shutdown_ms": service.shutdown_ms,
                }
                for service in self.registry.list()
            },
        }

    def _emit(self, event_type, service: Service | None = None, payload=None) -> None:
        bus = self.event_bus
        if bus is None:
            return
        data = dict(payload or {})
        if service:
            data.update({"service": service.name, "state": service.state.value})
        bus.emit(event_type, "runtime", data)


class ApplicationRuntime(IsabellaRuntime):
    """Concrete lifecycle adapters shared by GUI and CLI entry points."""

    def __init__(self, config: dict[str, Any], mode: str = "gui", qt_app=None) -> None:
        if mode not in {"gui", "cli"}:
            raise ValueError("Runtime mode must be gui or cli")
        config = dict(config)
        enabled = set(config["enabled_services"])
        if mode == "cli":
            enabled.discard("HUD")
        config["enabled_services"] = list(enabled)
        super().__init__(config)
        self.mode = mode
        self.qt_app = qt_app
        self.app = None
        self.brain = None
        self.controller = None
        self.window = None
        self.api = None
        self.nodes = None
        self.transport = None
        self.device_security = None
        self.sessions = None
        self.notifications = None
        self.home = None
        self.control_center = None
        self.control_center_controller = None
        self._register_application_services()

    @classmethod
    def from_config(cls, path: Path | None = None, **kwargs) -> "ApplicationRuntime":
        return cls(load_runtime_config(path), **kwargs)

    def _register_application_services(self) -> None:
        self.register(Service("Core", required=True, start_hook=self._start_core, stop_hook=self._stop_core, health_hook=self._health_core))
        self.register(Service("Event Bus", ("Core",), required=True, start_hook=self._start_event_bus, stop_hook=self._stop_event_bus, health_hook=self._health_event_bus))
        self.register(Service("Intelligence", ("Event Bus",), start_hook=self._start_intelligence, stop_hook=self._stop_intelligence, health_hook=self._health_intelligence))
        self.register(Service("Security", ("Intelligence",), start_hook=lambda: bool(self.brain.security), health_hook=lambda: bool(self.brain and self.brain.security)))
        self.register(Service("Memory", ("Intelligence",), start_hook=lambda: bool(self.brain.memory), health_hook=self._health_memory))
        self.register(Service("Context", ("Memory",), start_hook=lambda: bool(self.brain.context), health_hook=self._health_context))
        self.register(Service("Modes", ("Context", "Security"), start_hook=lambda: bool(self.brain and self.brain.modes), health_hook=lambda: bool(self.brain and self.brain.modes)))
        self.register(Service("Agents", ("Modes", "Skills", "Security"), start_hook=lambda: bool(self.brain and self.brain.orchestrator), health_hook=lambda: bool(self.brain and self.brain.orchestrator)))
        self.register(Service("Knowledge", ("Memory", "Context", "Skills"), start_hook=lambda: bool(self.brain and self.brain.knowledge), health_hook=lambda: bool(self.brain and self.brain.knowledge and self.brain.knowledge.storage.health_check())))
        self.register(Service("Skills", ("Security",), start_hook=lambda: bool(self.brain.registry), health_hook=lambda: bool(self.brain and self.brain.registry and self.brain.registry.list())))
        self.register(Service("Skill Forge", ("Skills", "Security"), start_hook=self._start_skillforge, stop_hook=self._stop_skillforge, health_hook=self._health_skillforge))
        self.register(Service("Automations", ("Skills", "Security", "Event Bus"), start_hook=self._start_automations, stop_hook=self._stop_automations, health_hook=self._health_automations))
        self.register(Service("Scheduler", ("Automations", "Skills", "Security", "Event Bus"), start_hook=self._start_scheduler, stop_hook=self._stop_scheduler, health_hook=self._health_scheduler))
        self.register(Service("API", ("Intelligence", "Skills", "Security", "Event Bus"), start_hook=self._start_api, stop_hook=self._stop_api, health_hook=self._health_api))
        self.register(Service("MCP", ("Skills", "Security"), start_hook=self._start_mcp, stop_hook=self._stop_mcp, health_hook=self._health_mcp))
        self.register(Service("Research", ("Intelligence",), start_hook=self._start_research, stop_hook=self._stop_research, health_hook=self._health_research))
        self.register(Service("Vision", ("Context",), start_hook=lambda: bool(self.brain.vision), health_hook=self._health_vision))
        self.register(Service("Diagnostics", ("Intelligence", "Security", "Memory"), start_hook=self._start_diagnostics, stop_hook=self._stop_diagnostics, health_hook=self._health_diagnostics))
        if self.mode == "gui":
            self.register(Service("HUD", ("Core", "Intelligence"), start_hook=self._start_hud, stop_hook=self._stop_hud, health_hook=self._health_hud, bounded=False))
            voice_dependencies = ("Core", "Intelligence", "HUD")
        else:
            voice_dependencies = ("Core", "Intelligence")
        self.register(Service("Voice Input", voice_dependencies, start_hook=self._start_voice, stop_hook=self._stop_voice, health_hook=self._health_voice))
        self.register(Service("Voice Output", ("Core",), start_hook=self._start_tts, stop_hook=self._stop_tts, health_hook=self._health_tts))
        self.register(Service("Nodes", ("Core", "Intelligence", "Context", "Vision"), start_hook=self._start_nodes, stop_hook=self._stop_nodes, health_hook=self._health_nodes))
        self.register(Service("Transport", ("Nodes", "Skills", "Security", "Event Bus"), start_hook=self._start_transport, stop_hook=self._stop_transport, health_hook=self._health_transport))
        self.register(Service("Home", ("Nodes", "Skills", "Security", "Event Bus", "Context"), start_hook=self._start_home, stop_hook=self._stop_home, health_hook=self._health_home))

    def _start_core(self):
        from Isabella.Core.app import IsabellaApp
        self.app = IsabellaApp()
        self.app.start()
        self.event_bus = self.app.event_bus
        return True

    def _stop_core(self):
        return self.app.stop_core() if self.app else True

    def _health_core(self):
        return bool(self.app and getattr(self.app.status, "value", None) == "ONLINE")

    def _start_event_bus(self):
        self.event_bus = getattr(self.app, "event_bus", None)
        return self.event_bus is not None

    def _stop_event_bus(self):
        return self.app.stop_event_bus() if self.app else True

    def _health_event_bus(self):
        return bool(self.event_bus and getattr(self.event_bus, "_accepting", False))

    def _start_intelligence(self):
        from Isabella.Intelligence.brain import Brain
        self.brain = Brain.from_config(event_bus=self.event_bus)
        if self.event_bus:
            self.event_bus.subscribe(EventType.MODE_CHANGED.value, self._on_mode_changed)
        return True

    def _stop_intelligence(self):
        if self.event_bus:
            self.event_bus.unsubscribe(EventType.MODE_CHANGED.value, self._on_mode_changed)
        if self.brain:
            self.brain.shutdown()
            self.brain = None
        return True

    def _health_intelligence(self):
        if not self.brain:
            return False
        return ServiceState.ONLINE if self.brain.llm.health_check() else ServiceState.DEGRADED

    def _on_mode_changed(self, event):
        manager = getattr(self.app, "tts_manager", None)
        mode = event.payload.get("to")
        if manager and hasattr(manager, "set_local_only"):
            manager.set_local_only(mode in {"PRIVACY", "OFFLINE"})

    def _health_memory(self):
        status = getattr(getattr(self.brain, "memory", None), "status", "OFFLINE")
        return ServiceState.ONLINE if status == "ONLINE" else ServiceState.ERROR if status == "ERROR" else ServiceState.DEGRADED

    def _health_context(self):
        status = getattr(getattr(self.brain, "context", None), "status", "OFFLINE")
        return ServiceState.ONLINE if status == "ONLINE" else ServiceState.DEGRADED

    def _health_vision(self):
        vision = getattr(self.brain, "vision", None)
        if not vision:
            return ServiceState.ERROR
        capabilities = vision.health_check(check_camera=False)
        ready = capabilities.get("screen") and (
            not capabilities.get("multimodal_enabled") or capabilities.get("model_available", True)
        )
        return ServiceState.ONLINE if ready else ServiceState.DEGRADED

    def _start_mcp(self):
        return bool(self.brain and self.brain.mcp and self.brain.mcp.start())

    def _start_skillforge(self):
        return bool(self.brain and self.brain.skillforge and self.brain.skillforge.start())

    def _stop_skillforge(self):
        return self.brain.skillforge.shutdown() if self.brain and self.brain.skillforge else True

    def _health_skillforge(self):
        if not self.brain or not self.brain.skillforge:
            return ServiceState.ERROR
        details = self.brain.skillforge.diagnostics()
        return ServiceState.DEGRADED if details["failed_validation"] else ServiceState.ONLINE

    def _start_automations(self):
        return bool(self.brain and self.brain.automations and self.brain.automations.start())

    def _stop_automations(self):
        return self.brain.automations.shutdown() if self.brain and self.brain.automations else True

    def _health_automations(self):
        if not self.brain or not self.brain.automations:
            return ServiceState.ERROR
        details = self.brain.automations.diagnostics()
        return ServiceState.ONLINE if details["storage_accessible"] else ServiceState.ERROR

    def _start_scheduler(self):
        if not self.brain or not self.brain.scheduler:
            return False
        self.brain.scheduler.bind_notifier(self.app.speak if self.app else None)
        return self.brain.scheduler.start()

    def _stop_scheduler(self):
        return self.brain.scheduler.shutdown() if self.brain and self.brain.scheduler else True

    def _health_scheduler(self):
        if not self.brain or not self.brain.scheduler:
            return ServiceState.ERROR
        return ServiceState.ONLINE if self.brain.scheduler.diagnostics()["storage_accessible"] else ServiceState.ERROR

    def _start_api(self):
        from Isabella.API import LocalAPIServer
        self.api = LocalAPIServer.from_config(brain=self.brain, runtime=self, event_bus=self.event_bus)
        self.brain.api = self.api
        return self.api.start()

    def _stop_api(self):
        if not self.api:
            return True
        stopped = self.api.shutdown()
        if self.brain:
            self.brain.api = None
        self.api = None
        return stopped

    def _health_api(self):
        if not self.api:
            return ServiceState.ERROR
        status = self.api.health_check()["status"]
        return ServiceState.ONLINE if status == "ONLINE" else ServiceState.DEGRADED if status == "DISABLED" else ServiceState.ERROR

    def _stop_mcp(self):
        return self.brain.mcp.shutdown() if self.brain and self.brain.mcp else True

    def _health_mcp(self):
        if not self.brain or not self.brain.mcp:
            return ServiceState.ERROR
        details = self.brain.mcp.health_check()
        return ServiceState.DEGRADED if details["unhealthy_servers"] else ServiceState.ONLINE

    def _start_research(self):
        return bool(self.brain and self.brain.research)

    def _stop_research(self):
        return self.brain.research.shutdown() if self.brain and self.brain.research else True

    def _health_research(self):
        if not self.brain or not self.brain.research:
            return ServiceState.ERROR
        return ServiceState.ONLINE if self.brain.research.health_check()["provider_configured"] else ServiceState.DEGRADED

    def _start_diagnostics(self):
        if not self.brain or not self.brain.diagnostics:
            return False
        self.brain.diagnostics.bind(app=self.app, brain=self.brain, event_bus=self.event_bus, runtime=self)
        return True

    def _stop_diagnostics(self):
        diagnostics = getattr(self.brain, "diagnostics", None)
        return diagnostics.shutdown() if diagnostics else True

    def _health_diagnostics(self):
        return bool(self.brain and self.brain.diagnostics)

    def _start_hud(self):
        from PySide6.QtWidgets import QApplication
        from Isabella.Interface.controller import InterfaceController
        from Isabella.Interface.hud import IsabellaHUD
        self.qt_app = self.qt_app or QApplication.instance() or QApplication([])
        self.controller = InterfaceController(self.app, self.brain)
        self.controller.managed_by_runtime = True
        self.controller.control_center_requested.connect(self.open_control_center)
        self.window = IsabellaHUD(self.controller)
        self.window.show()
        self.controller.start_services(start_backends=False, run_health_check=False)
        return True

    def _stop_hud(self):
        if self.control_center:
            self.control_center.close()
        self.control_center = None
        self.control_center_controller = None
        if self.controller:
            self.controller.shutdown()
        if self.window:
            self.window.close()
        self.controller = None
        self.window = None
        return True

    def open_control_center(self):
        """Open one isolated engineering window without affecting the HUD lifecycle."""
        if self.mode != "gui" or not self.brain:
            return False
        if self.control_center:
            self.control_center.show(); self.control_center.raise_(); self.control_center.activateWindow()
            return True
        from Isabella.ControlCenter import ControlCenterController, ControlCenterWindow
        self.control_center_controller = ControlCenterController(self)
        self.control_center = ControlCenterWindow(self.control_center_controller)
        self.control_center.destroyed.connect(self._control_center_destroyed)
        self.control_center.show()
        return True

    def _control_center_destroyed(self):
        self.control_center = None
        self.control_center_controller = None

    def _health_hud(self):
        return bool(self.window and self.window.isVisible())

    def _voice_callback(self, command: str) -> None:
        if not self.accepting_commands:
            return
        if self.controller:
            self.controller.voice_command_received.emit(command)
        elif self.brain:
            self._display_response(self.brain.process(command, input_source="voice"), allow_confirmation=False)

    def _start_voice(self):
        started = bool(self.app and self.app.start_voice(self._voice_callback))
        if self.controller:
            self.controller.update_subsystem("VOICE INPUT", "ONLINE" if started else "ERROR")
        return started

    def _stop_voice(self):
        return self.app.stop_voice() if self.app else True

    def _health_voice(self):
        listener = getattr(self.app, "voice_listener", None)
        return bool(listener and getattr(listener, "is_running", False))

    def _start_tts(self):
        callback = self.controller.tts_speaking_received.emit if self.controller else None
        started = bool(self.app and self.app.start_tts(state_callback=callback))
        if self.controller:
            self.controller.update_subsystem("VOICE OUTPUT", "ONLINE" if started else "ERROR")
        return started

    def _stop_tts(self):
        return self.app.stop_tts() if self.app else True

    def _health_tts(self):
        tts = getattr(self.app, "tts_manager", None)
        return bool(tts and tts.health_check())

    def _start_nodes(self):
        from Isabella.Nodes import NodeManager
        from Isabella.Security.Devices import DevicePairingManager
        from Isabella.Skills import create_node_security_skills
        self.device_security = DevicePairingManager.from_config(event_bus=self.event_bus)
        from Isabella.Sessions import SessionManager
        from Isabella.Notifications import NotificationManager
        self.sessions = SessionManager(context=self.brain.context, event_bus=self.event_bus)
        self.notifications = NotificationManager(event_bus=self.event_bus)
        self.notifications.bind_action_handler(self._notification_action)
        self.notifications.subscribe_sources(self.sessions)
        self.brain.sessions = self.sessions
        self.brain.notifications = self.notifications
        self.nodes = NodeManager.from_config(app=self.app, brain=self.brain, controller=self.controller, context=self.brain.context, event_bus=self.event_bus, device_security=self.device_security)
        self.brain.nodes = self.nodes
        started = self.nodes.start()
        for definition in create_node_security_skills(self.nodes, self.device_security):
            if self.brain.registry.get(definition.id) is None:
                self.brain.registry.register(definition)
        return started

    def _stop_nodes(self):
        if not self.nodes:
            return True
        stopped = self.nodes.shutdown()
        if self.brain:
            self.brain.nodes = None
        self.nodes = None
        self.device_security = None
        if self.notifications:
            self.notifications.shutdown()
        self.sessions = None
        self.notifications = None
        return stopped

    def _health_nodes(self):
        if not self.nodes:
            return ServiceState.ERROR
        details = self.nodes.diagnostics()
        return ServiceState.ONLINE if details["online"] >= 1 else ServiceState.DEGRADED

    def _start_transport(self):
        from Isabella.Transport import TransportManager
        self.transport = TransportManager.from_config(node_manager=self.nodes, registry=self.brain.registry, event_bus=self.event_bus, device_security=self.device_security, brain=self.brain, sessions=self.sessions, notifications=self.notifications)
        self.brain.transport = self.transport
        return self.transport.start()

    def _notification_action(self, notification, node_id, action):
        confirmation_id = notification.metadata.get("confirmation_id")
        if action == "Cancelar":
            return self.brain.cancel_confirmation(confirmation_id) if confirmation_id else True
        if action == "Confirmar" and confirmation_id:
            request = self.brain.pending_confirmation(confirmation_id)
            if request is None or request.expired:
                raise PermissionError("Confirmation is expired")
            return self.brain.confirm(request, source="trusted_node")
        return True

    def _stop_transport(self):
        if not self.transport:
            return True
        stopped = self.transport.shutdown()
        if self.brain:
            self.brain.transport = None
        self.transport = None
        return stopped

    def _health_transport(self):
        if not self.transport:
            return ServiceState.ERROR
        status = self.transport.diagnostics()["status"]
        return ServiceState.ONLINE if status == "ONLINE" else ServiceState.DEGRADED if not self.transport.enabled else ServiceState.ERROR

    def _start_home(self):
        from Isabella.Home import HomeManager
        from Isabella.Skills import create_home_skills
        self.home = HomeManager.from_config(event_bus=self.event_bus, context=self.brain.context, controller=self.controller)
        self.brain.home = self.home
        self.nodes.register_local_home_gateway(self.home.config["gateway_node_id"])
        for definition in create_home_skills(self.home):
            if self.brain.registry.get(definition.id) is None:
                self.brain.registry.register(definition)
        return self.home.start()

    def _stop_home(self):
        if not self.home: return True
        stopped = self.home.shutdown()
        gateway_id = self.home.config["gateway_node_id"]
        if self.nodes and self.nodes.get(gateway_id): self.nodes.unregister(gateway_id)
        if self.brain: self.brain.home = None
        self.home = None
        return stopped

    def _health_home(self):
        if not self.home: return ServiceState.ERROR
        details = self.home.health_check()
        if details["gateway"] != "ONLINE": return ServiceState.ERROR
        return ServiceState.DEGRADED if details["broker"] == "OFFLINE" else ServiceState.ONLINE

    def start(self) -> bool:
        success = super().start()
        self._print_startup_report()
        return success

    def wait(self) -> int:
        if self.mode == "gui":
            return self.qt_app.exec() if self.qt_app else 1
        return self._cli_loop()

    def _cli_loop(self) -> int:
        voice = self.registry.get("Voice Input").state.value
        tts = self.registry.get("Voice Output").state.value
        print(f"Entrada por voz: {voice}. Saída por voz: {tts}. Digite um comando ou 'sair'.")
        while self.accepting_commands:
            try:
                text = input("\nVocê:\n").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.casefold() == "sair":
                break
            if text:
                self._display_response(self.brain.process(text), allow_confirmation=True)
        return 0

    def _display_response(self, response, allow_confirmation: bool) -> None:
        from Isabella.Intelligence.models import Intent
        if response.response_type == Intent.CONVERSATION:
            print(f"\nISABELLA:\n{response.message}")
            self.app.speak(response.message)
            return
        print(f"\n[ROUTER] {response.response_type.value}")
        for result in response.skill_results:
            print(f"[SKILL] {result.skill_id}\n[STATUS] {result.status}\n\nISABELLA:\n{result.message}")
            if result.status != "confirmation_required":
                continue
            if not allow_confirmation:
                print("Confirme ações críticas pelo modo texto.")
                continue
            answer = input("Confirmar esta ação? (sim/não) ").strip().casefold()
            confirmation_id = result.data["confirmation_id"]
            if answer == "sim":
                request = self.brain.pending_confirmation(confirmation_id)
                confirmed = self.brain.confirm(request, source="cli") if request else None
                if confirmed:
                    print(f"[STATUS] {confirmed.status}\n\nISABELLA:\n{confirmed.message}")
                    self.app.speak(confirmed.message)
            else:
                self.brain.cancel_confirmation(confirmation_id)
                print("[STATUS] cancelled\n\nISABELLA:\nAção cancelada.")
        self.app.speak(response.message)

    def _print_startup_report(self) -> None:
        print(f"\nISABELLA {self.state.value}")
        for service in self.registry.list():
            print(f"{service.name.upper():<15} {service.state.value}")
