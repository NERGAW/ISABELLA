from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TwinEntityType(str, Enum):
    PRIMARY_PC="PRIMARY_PC"; MOBILE="MOBILE"; HOME_GATEWAY="HOME_GATEWAY"; ESP32="ESP32"
    SENSOR="SENSOR"; APPLICATION="APPLICATION"; SERVICE="SERVICE"


class TwinStatus(str, Enum):
    ONLINE="ONLINE"; DEGRADED="DEGRADED"; OFFLINE="OFFLINE"; ERROR="ERROR"; UNKNOWN="UNKNOWN"; STALE="STALE"


@dataclass(frozen=True)
class TwinEntity:
    twin_id: str; entity_type: TwinEntityType; name: str; status: TwinStatus
    physical_id: str | None = None; logical_id: str | None = None
    capabilities: tuple[str,...] = (); state: dict[str,Any] = field(default_factory=dict)
    telemetry: dict[str,Any] = field(default_factory=dict); telemetry_timestamp: str | None = None
    last_updated: str = ""; source: str = "system"; knowledge_entity_id: str | None = None
    def to_dict(self):
        return {"twin_id":self.twin_id,"entity_type":self.entity_type.value,"physical_id":self.physical_id,"logical_id":self.logical_id,"name":self.name,"status":self.status.value,"capabilities":list(self.capabilities),"state":dict(self.state),"telemetry":dict(self.telemetry),"telemetry_timestamp":self.telemetry_timestamp,"last_updated":self.last_updated,"source":self.source,"knowledge_entity_id":self.knowledge_entity_id}
