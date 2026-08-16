from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    id: str
    name: str
    description: str
    enabled_skills: tuple[str, ...]
    disabled_skills: tuple[str, ...]
    interface_profile: str
    voice_profile: str
    research_allowed: bool
    network_policy: str
    diagnostics_level: str


@dataclass(frozen=True)
class ModePolicy:
    mode_id: str
    enabled_skills: tuple[str, ...]
    disabled_skills: tuple[str, ...]
    interface_profile: str
    voice_profile: str
    research_allowed: bool
    network_policy: str
    diagnostics_level: str

    def allows_skill(self, skill_id: str) -> bool:
        def matches(rule: str) -> bool:
            return rule == "*" or skill_id == rule or (rule.endswith(".*") and skill_id.startswith(rule[:-1]))
        if any(matches(rule) for rule in self.disabled_skills):
            return False
        return not self.enabled_skills or any(matches(rule) for rule in self.enabled_skills)
