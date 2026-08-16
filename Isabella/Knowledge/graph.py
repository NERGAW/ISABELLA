from __future__ import annotations

import json,re
from pathlib import Path
from time import perf_counter
from collections import deque

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .models import EntityType, RelationType
from .storage import KnowledgeStorage
from .retrieval import find_path as bfs

DEFAULT_CONFIG_PATH=PROJECT_ROOT/"config"/"knowledge.json"

def load_knowledge_config(path=None):
    target=path or DEFAULT_CONFIG_PATH
    try: config=json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ConfigurationError(f"Invalid knowledge configuration: {target}") from exc
    if not isinstance(config,dict) or {"enabled","database_path","max_path_depth","max_results"}-config.keys(): raise ConfigurationError("Knowledge configuration is missing required fields")
    if not 1<=int(config["max_path_depth"])<=8 or not 1<=int(config["max_results"])<=500: raise ConfigurationError("Knowledge limits are invalid")
    return config

class KnowledgeGraph:
    def __init__(self,config,*,storage=None,event_bus=None):
        self.config=config; self.enabled=bool(config["enabled"]); self.event_bus=event_bus; self.latencies_ms=deque(maxlen=200)
        path=Path(config["database_path"]); self.storage=storage or KnowledgeStorage(path if path.is_absolute() else PROJECT_ROOT/path)
        self.max_results=int(config["max_results"]); self.max_path_depth=int(config["max_path_depth"])
        if event_bus:
            event_bus.subscribe("node.registered",self._on_node); event_bus.subscribe("node.capabilities_changed",self._on_node)
    @classmethod
    def from_config(cls,path=None,**kwargs): return cls(load_knowledge_config(path),**kwargs)
    def add_entity(self,entity_id,entity_type,name,attributes=None):
        entity_id=self._id(entity_id); kind=entity_type if isinstance(entity_type,EntityType) else EntityType(str(entity_type).upper())
        existed=self.get_entity(entity_id); result=self.storage.upsert_entity(entity_id,kind,str(name).strip(),attributes or {})
        if not existed:self._emit(EventType.KNOWLEDGE_ENTITY_CREATED,{"entity_id":result.id,"entity_type":result.type.value})
        return result
    def get_entity(self,entity_id): return self.storage.get_entity(self._id(entity_id))
    def find_entity(self,query,entity_type=None): return self.storage.find_entities(query,self._entity_type(entity_type) if entity_type else None,self.max_results)
    def add_relation(self,source_entity,relation_type,target_entity,confidence=1.0,source="system"):
        source_id,target_id=self._id(source_entity),self._id(target_entity); kind=relation_type if isinstance(relation_type,RelationType) else RelationType(str(relation_type).upper())
        if not self.get_entity(source_id) or not self.get_entity(target_id): raise KeyError("Both relation entities must exist")
        if not 0<=float(confidence)<=1 or not str(source).strip(): raise ValueError("Invalid relation confidence or source")
        existing=next((item for item in self.storage.relations(source_id,kind,limit=self.max_results) if item.source_entity==source_id and item.target_entity==target_id and item.source==source),None)
        relation=self.storage.add_relation(source_id,kind,target_id,float(confidence),str(source))
        if not existing:self._emit(EventType.KNOWLEDGE_RELATION_CREATED,relation.to_dict())
        return relation
    def remove_relation(self,relation_id):
        removed=self.storage.remove_relation(int(relation_id))
        if removed:self._emit(EventType.KNOWLEDGE_RELATION_REMOVED,{"relation_id":int(relation_id)})
        return removed
    def neighbors(self,entity_id,relation_type=None):
        started=perf_counter(); result=self.storage.relations(self._id(entity_id),self._relation_type(relation_type) if relation_type else None,limit=self.max_results); self.latencies_ms.append((perf_counter()-started)*1000); return result
    def find_path(self,source,target,max_depth=None):
        started=perf_counter(); result=bfs(self.neighbors,self._id(source),self._id(target),min(int(max_depth or self.max_path_depth),self.max_path_depth)); self.latencies_ms.append((perf_counter()-started)*1000); return result
    def search_relations(self,query="",relation_type=None):
        started=perf_counter(); result=self.storage.relations(kind=self._relation_type(relation_type) if relation_type else None,query=query,limit=self.max_results); self.latencies_ms.append((perf_counter()-started)*1000); return result
    def ingest_memory(self,record):
        if record.key != "preferred_browser": return None
        self.add_entity("USER_REFERENCE",EntityType.PERSON_REFERENCE,"Usuário")
        target=self.add_entity(record.value,EntityType.APPLICATION,record.value.title())
        return self.add_relation("USER_REFERENCE",RelationType.PREFERS,target.id,record.confidence,"memory")
    def register_node(self,node_id,name,capabilities=()):
        node=self.add_entity(node_id,EntityType.NODE,name)
        for capability in capabilities:
            cap=self.add_entity(f"CAPABILITY_{capability}",EntityType.CONCEPT,str(capability))
            self.add_relation(node.id,RelationType.HAS_CAPABILITY,cap.id,1.0,"discovery")
        return node
    def seed(self,skills=()):
        self.add_entity("ISABELLA",EntityType.SYSTEM,"I.S.A.B.E.L.L.A."); self.add_entity("ISABELLA_PROJECT",EntityType.PROJECT,"ISABELLA Project")
        for concept in ("PYTHON","OLLAMA"):
            self.add_entity(concept,EntityType.CONCEPT,concept.title()); self.add_relation("ISABELLA_PROJECT",RelationType.USES,concept,1.0,"system")
        for skill in skills:
            self.add_entity(skill.id,EntityType.SKILL,skill.name,{"category":skill.category})
            if skill.id.startswith(("browser.","applications.")):
                target="BROWSER" if skill.id.startswith("browser.") else "APPLICATIONS"
                self.add_entity(target,EntityType.CONCEPT,target.title()); self.add_relation(skill.id,RelationType.CONTROLS,target,0.9,"system")
    def answer(self,text):
        normalized=text.casefold()
        if "skills controlam" in normalized or "skill controla" in normalized:
            rows=self.search_relations(relation_type=RelationType.CONTROLS)
            if "navegador" in normalized: rows=[row for row in rows if row.target_entity=="BROWSER"]
        elif "projeto usa" in normalized:
            rows=self.neighbors("ISABELLA_PROJECT",RelationType.USES)
        elif "ligados ao" in normalized or "conectados ao" in normalized:
            target_text=re.split(r"(?:ligados|conectados) ao\s+",text,flags=re.IGNORECASE,maxsplit=1)[-1].strip(" ?.")
            matches=self.find_entity(target_text); rows=self.neighbors(matches[0].id,RelationType.CONNECTED_TO) if matches else []
        else:
            matches=self.find_entity(re.sub(r".*(?:ao|à|o)\s+","",text).strip(" ?."))
            rows=self.neighbors(matches[0].id) if matches else []
        if not rows:return "Não encontrei uma relação estruturada correspondente."
        return "Relações encontradas: "+"; ".join(f"{r.source_entity} {r.relation_type.value} {r.target_entity}" for r in rows[:10])+"."
    def diagnostics(self): return {**self.storage.counts(),"database_accessible":self.storage.health_check(),"average_latency_ms":sum(self.latencies_ms)/len(self.latencies_ms) if self.latencies_ms else 0.0}
    def close(self):
        if self.event_bus:self.event_bus.unsubscribe("node.registered",self._on_node); self.event_bus.unsubscribe("node.capabilities_changed",self._on_node)
        self.storage.close()
    def _on_node(self,event):
        payload=event.payload
        node=self.register_node(payload["node_id"],payload.get("name",payload["node_id"]),payload.get("capabilities",()))
        if payload.get("node_type")=="PRIMARY_PC":
            self._primary_node=node.id
        elif getattr(self,"_primary_node",None):
            self.add_relation(node.id,RelationType.CONNECTED_TO,self._primary_node,1.0,"discovery")
    @staticmethod
    def _id(value):
        result=re.sub(r"[^A-Z0-9_.-]+","_",str(value).strip().upper()).strip("_")
        if not result: raise ValueError("Entity id is required")
        return result
    @staticmethod
    def _relation_type(value): return value if isinstance(value,RelationType) else RelationType(str(value).upper())
    @staticmethod
    def _entity_type(value): return value if isinstance(value,EntityType) else EntityType(str(value).upper())
    def _emit(self,event_type,payload):
        if self.event_bus:self.event_bus.emit(event_type,"knowledge",payload)
