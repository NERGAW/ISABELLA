from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    SYSTEM="SYSTEM"; PROJECT="PROJECT"; NODE="NODE"; DEVICE="DEVICE"; SKILL="SKILL"
    APPLICATION="APPLICATION"; SERVICE="SERVICE"; PERSON_REFERENCE="PERSON_REFERENCE"; CONCEPT="CONCEPT"


class RelationType(str, Enum):
    USES="USES"; RUNS="RUNS"; CONNECTED_TO="CONNECTED_TO"; BELONGS_TO="BELONGS_TO"
    DEPENDS_ON="DEPENDS_ON"; PREFERS="PREFERS"; HAS_CAPABILITY="HAS_CAPABILITY"; CONTROLS="CONTROLS"; RELATED_TO="RELATED_TO"


@dataclass(frozen=True)
class Entity:
    id: str; type: EntityType; name: str; attributes: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""; updated_at: str = ""
    def to_dict(self): return {"id":self.id,"type":self.type.value,"name":self.name,"attributes":self.attributes,"created_at":self.created_at,"updated_at":self.updated_at}


@dataclass(frozen=True)
class Relation:
    id: int; source_entity: str; relation_type: RelationType; target_entity: str
    confidence: float; source: str; created_at: str
    def to_dict(self): return {"id":self.id,"source_entity":self.source_entity,"relation_type":self.relation_type.value,"target_entity":self.target_entity,"confidence":self.confidence,"source":self.source,"created_at":self.created_at}
