from __future__ import annotations

import json, threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .entity import twin_id
from .models import TwinEntity, TwinEntityType, TwinStatus
from .state import age_seconds, now_iso

DEFAULT_CONFIG_PATH=PROJECT_ROOT/"config"/"digital_twin.json"

def load_digital_twin_config(path=None):
    target=path or DEFAULT_CONFIG_PATH
    try: config=json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ConfigurationError(f"Invalid Digital Twin configuration: {target}") from exc
    if not isinstance(config,dict) or {"enabled","stale_after_seconds","history_limit"}-config.keys(): raise ConfigurationError("Digital Twin configuration is missing required fields")
    if not 5<=float(config["stale_after_seconds"])<=86400 or not 0<=int(config["history_limit"])<=500: raise ConfigurationError("Digital Twin limits are invalid")
    return config

class DigitalTwinManager:
    """Event-fed state projection. It exposes no device-control operation."""
    def __init__(self,config,*,event_bus=None,context=None,knowledge=None):
        self.config=config; self.enabled=bool(config["enabled"]); self.event_bus=event_bus; self.context=context; self.knowledge=knowledge
        self.stale_after=float(config["stale_after_seconds"]); self._twins={}; self._lock=threading.RLock()
        self._shutdown=False
        self._subscriptions=("node.registered","node.online","node.offline","node.capabilities_changed","home.device_online","home.device_offline","home.telemetry","service.online","service.error","service.stopped")
        if event_bus:
            for name in self._subscriptions:event_bus.subscribe(name,self._on_event)
    @classmethod
    def from_config(cls,path=None,**kwargs): return cls(load_digital_twin_config(path),**kwargs)
    def start(self):
        self._upsert("PRIMARY_PC",TwinEntityType.PRIMARY_PC,"Primary PC",TwinStatus.UNKNOWN,logical_id="primary_pc",source="runtime",knowledge_entity_id="PRIMARY_PC")
        return True
    def get(self,twin):
        with self._lock:return self._twins.get(twin_id(twin))
    def list(self,*,refresh_stale=True):
        if refresh_stale:self.mark_stale()
        with self._lock:return list(self._twins.values())
    def online(self): return [item for item in self.list() if item.status is TwinStatus.ONLINE]
    def consume_diagnostics(self,report):
        metrics=report.metrics; statuses=report.statuses
        services={name:item.status.value for name,item in statuses.items()}
        status=TwinStatus.ONLINE if services.get("CORE")=="ONLINE" else TwinStatus.DEGRADED
        self._upsert("PRIMARY_PC",TwinEntityType.PRIMARY_PC,"Primary PC",status,logical_id="primary_pc",capabilities=tuple(sorted(services)),state={"services":services},telemetry={"cpu_percent":metrics.cpu_percent,"ram_percent":metrics.system_ram_percent,"process_memory_mb":metrics.process_memory_mb,"uptime_seconds":metrics.uptime_seconds},telemetry_timestamp=report.generated_at,source="diagnostics",knowledge_entity_id="PRIMARY_PC")
    def sync_home(self,home):
        self._upsert("HOME_GATEWAY",TwinEntityType.HOME_GATEWAY,"Home Gateway",TwinStatus.ONLINE,logical_id="home.gateway",state=home.health_check(),source="home",knowledge_entity_id="HOME.GATEWAY")
        for device in home.list_devices(): self._home_device(device)
    def update_telemetry(self,twin,telemetry,timestamp,*,source="event_bus"):
        current=self.get(twin)
        if not current: raise KeyError(f"Unknown Twin: {twin}")
        merged={**current.telemetry,**dict(telemetry)}
        return self._upsert(current.twin_id,current.entity_type,current.name,current.status,physical_id=current.physical_id,logical_id=current.logical_id,capabilities=current.capabilities,state=current.state,telemetry=merged,telemetry_timestamp=timestamp,source=source,knowledge_entity_id=current.knowledge_entity_id)
    def mark_stale(self,now=None):
        changed=[]
        with self._lock: items=list(self._twins.values())
        for item in items:
            timestamp=item.telemetry_timestamp or item.last_updated
            if item.status not in {TwinStatus.OFFLINE,TwinStatus.ERROR,TwinStatus.STALE} and age_seconds(timestamp,now)>self.stale_after:
                changed.append(self._upsert(item.twin_id,item.entity_type,item.name,TwinStatus.STALE,physical_id=item.physical_id,logical_id=item.logical_id,capabilities=item.capabilities,state=item.state,telemetry=item.telemetry,telemetry_timestamp=item.telemetry_timestamp,source=item.source,knowledge_entity_id=item.knowledge_entity_id,event=EventType.TWIN_STALE))
        return changed
    def context_summary(self):
        twins=self.list(); return {"devices_online":[x.twin_id for x in twins if x.status is TwinStatus.ONLINE],"capabilities":sorted({c for x in twins if x.status is TwinStatus.ONLINE for c in x.capabilities}),"statuses":{x.twin_id:x.status.value for x in twins}}
    def answer(self,text):
        normalized=text.casefold(); items=self.list()
        if "celular" in normalized or "mobile" in normalized:
            items=[x for x in items if x.entity_type is TwinEntityType.MOBILE]
        elif "casa" in normalized or "home" in normalized:
            items=[x for x in items if x.entity_type in {TwinEntityType.HOME_GATEWAY,TwinEntityType.ESP32,TwinEntityType.SENSOR}]
        else: items=[x for x in items if x.entity_type not in {TwinEntityType.APPLICATION,TwinEntityType.SERVICE}]
        if not items:return "Não há estado real disponível para essa entidade."
        return "; ".join(f"{x.name}: {x.status.value}"+(f", bateria {x.telemetry['battery']}" if 'battery' in x.telemetry else "") for x in items)+"."
    def diagnostics(self):
        items=self.list(); return {"twins_total":len(items),"online":sum(x.status is TwinStatus.ONLINE for x in items),"offline":sum(x.status is TwinStatus.OFFLINE for x in items),"stale":sum(x.status is TwinStatus.STALE for x in items)}
    def shutdown(self):
        if self._shutdown:return True
        self._shutdown=True
        if self.event_bus:
            for name in self._subscriptions:self.event_bus.unsubscribe(name,self._on_event)
        return True
    def _on_event(self,event):
        p=event.payload
        if event.type.startswith("node."):
            types={"PRIMARY_PC":TwinEntityType.PRIMARY_PC,"MOBILE":TwinEntityType.MOBILE,"HOME":TwinEntityType.HOME_GATEWAY,"EMBEDDED":TwinEntityType.ESP32,"SENSOR":TwinEntityType.SENSOR}
            kind=types.get(p.get("node_type"));
            if not kind:return
            status=TwinStatus.OFFLINE if event.type=="node.offline" else TwinStatus(p.get("status","ONLINE")) if p.get("status") in TwinStatus._value2member_map_ else TwinStatus.ONLINE
            self._upsert(p["node_id"],kind,p.get("name",p["node_id"]),status,logical_id=p["node_id"],capabilities=tuple(p.get("capabilities",())),state={"trust":p.get("trust"),"last_seen":p.get("last_seen")},source="nodes",knowledge_entity_id=twin_id(p["node_id"]),event=EventType.TWIN_OFFLINE if status is TwinStatus.OFFLINE else None)
        elif event.type.startswith("home.device_"):
            current=self.get(p["device_id"]); status=TwinStatus.ONLINE if event.type.endswith("online") else TwinStatus.OFFLINE
            if current:self._upsert(current.twin_id,current.entity_type,current.name,status,physical_id=current.physical_id,logical_id=current.logical_id,capabilities=current.capabilities,state=current.state,telemetry=current.telemetry,telemetry_timestamp=current.telemetry_timestamp,source="home",knowledge_entity_id=current.knowledge_entity_id,event=EventType.TWIN_OFFLINE if status is TwinStatus.OFFLINE else None)
        elif event.type=="home.telemetry":
            current=self.get(p["device_id"])
            if current:self.update_telemetry(current.twin_id,{p["capability"]:p["value"]},p["timestamp"],source="home")
    def _home_device(self,device):
        kind=TwinEntityType.SENSOR if any(c in {"temperature","humidity","motion"} for c in device.get("capabilities",())) else TwinEntityType.ESP32 if "esp32" in device.get("type","").casefold() else TwinEntityType.APPLICATION
        self._upsert(device["device_id"],kind,device["name"],TwinStatus(device["status"]),physical_id=device["device_id"],capabilities=tuple(device.get("capabilities",())),state={"node_id":device.get("node_id"),"simulated":device.get("simulated")},source="home",knowledge_entity_id=twin_id(device["device_id"]))
    def _upsert(self,twin,kind,name,status,*,physical_id=None,logical_id=None,capabilities=(),state=None,telemetry=None,telemetry_timestamp=None,source="system",knowledge_entity_id=None,event=None):
        key=twin_id(twin); now=now_iso()
        with self._lock:
            previous=self._twins.get(key); item=TwinEntity(key,kind,name,status,physical_id,logical_id,tuple(capabilities),dict(state or {}),dict(telemetry or {}),telemetry_timestamp,now,source,knowledge_entity_id); self._twins[key]=item
        self._emit(event or (EventType.TWIN_CREATED if previous is None else EventType.TWIN_UPDATED),{"twin_id":key,"status":status.value,"entity_type":kind.value}); self._sync_context(); return item
    def _sync_context(self):
        if not self.context:return
        with self._lock: items=list(self._twins.values())
        self.context.update(twin_devices_online=tuple(sorted(x.twin_id for x in items if x.status is TwinStatus.ONLINE)),twin_capabilities=tuple(sorted({c for x in items if x.status is TwinStatus.ONLINE for c in x.capabilities})),twin_status={x.twin_id:x.status.value for x in items})
    def _emit(self,event_type,payload):
        if self.event_bus:self.event_bus.emit(event_type,"digital_twin",payload)
