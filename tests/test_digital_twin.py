from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from Isabella.DigitalTwin import DigitalTwinManager, TwinEntityType, TwinStatus
from Isabella.Diagnostics.models import DiagnosticsReport, HealthStatus, Subsystem, SubsystemHealth, SystemMetrics


class Bus:
    def __init__(self): self.events=[]; self.subscribers={}
    def subscribe(self,name,callback): self.subscribers.setdefault(name,[]).append(callback)
    def unsubscribe(self,name,callback): self.subscribers.get(name,[]).remove(callback)
    def emit(self,event_type,source,payload): self.events.append((getattr(event_type,"value",event_type),payload))


class Context:
    def __init__(self): self.values={}
    def update(self,**values): self.values.update(values)


def manager(**kwargs): return DigitalTwinManager({"enabled":True,"stale_after_seconds":60,"history_limit":10},**kwargs)


def test_primary_pc_from_existing_diagnostics():
    item=manager(); item.start()
    health=SubsystemHealth(Subsystem.CORE,HealthStatus.ONLINE)
    report=DiagnosticsReport({"CORE":health},SystemMetrics(12,48,120,5,{},90),"ok",True)
    item.consume_diagnostics(report); twin=item.get("PRIMARY_PC")
    assert twin.status is TwinStatus.ONLINE and twin.telemetry["cpu_percent"]==12 and twin.telemetry["ram_percent"]==48


def test_mobile_twin_and_offline_event():
    item=manager()
    event=SimpleNamespace(type="node.registered",payload={"node_id":"mobile_node","node_type":"MOBILE","name":"Celular","status":"ONLINE","capabilities":["battery"],"last_seen":"now"})
    item._on_event(event); assert item.get("mobile_node").entity_type is TwinEntityType.MOBILE
    item.update_telemetry("mobile_node",{"battery":71},datetime.now(timezone.utc).isoformat())
    assert "bateria 71" in item.answer("Como está o celular?")
    item._on_event(SimpleNamespace(type="node.offline",payload=event.payload)); assert item.get("mobile_node").status is TwinStatus.OFFLINE


def test_home_sensor_telemetry_and_knowledge_reference():
    item=manager()
    home=SimpleNamespace(health_check=lambda:{"gateway":"ONLINE"},list_devices=lambda:[{"device_id":"esp32_temp","name":"Sensor","type":"esp32","status":"ONLINE","capabilities":["temperature"],"node_id":"home.gateway","simulated":False}])
    item.sync_home(home); twin=item.get("esp32_temp")
    assert twin.entity_type is TwinEntityType.SENSOR and twin.knowledge_entity_id=="ESP32_TEMP"
    stamp=datetime.now(timezone.utc).isoformat()
    item._on_event(SimpleNamespace(type="home.telemetry",payload={"device_id":"esp32_temp","capability":"temperature","value":24,"timestamp":stamp}))
    assert item.get("esp32_temp").telemetry["temperature"]==24


def test_stale_detection_and_context_query():
    context=Context(); bus=Bus(); item=manager(context=context,event_bus=bus); item.start()
    future=datetime.now(timezone.utc)+timedelta(seconds=61)
    assert item.mark_stale(future)[0].status is TwinStatus.STALE
    assert context.values["twin_status"]["PRIMARY_PC"]=="STALE"
    assert any(name=="twin.stale" for name,_ in bus.events)


def test_context_online_capabilities_are_derived():
    context=Context(); item=manager(context=context)
    item._on_event(SimpleNamespace(type="node.online",payload={"node_id":"phone","node_type":"MOBILE","status":"ONLINE","capabilities":["notifications"]}))
    summary=item.context_summary()
    assert summary["devices_online"]==["PHONE"] and summary["capabilities"]==["notifications"]
