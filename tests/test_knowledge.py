from time import perf_counter
from types import SimpleNamespace

from Isabella.Knowledge import EntityType, KnowledgeGraph, RelationType
from Isabella.Knowledge.storage import KnowledgeStorage
from Isabella.Memory import MemoryManager, MemoryType


class Bus:
    def __init__(self): self.events=[]; self.subscribers={}
    def emit(self,event_type,source,payload): self.events.append((getattr(event_type,"value",event_type),payload))
    def subscribe(self,name,callback): self.subscribers[name]=callback
    def unsubscribe(self,name,callback): self.subscribers.pop(name,None)


def graph(tmp_path, bus=None):
    config={"enabled":True,"database_path":str(tmp_path/"knowledge.db"),"max_path_depth":4,"max_results":100}
    return KnowledgeGraph(config,storage=KnowledgeStorage(tmp_path/"knowledge.db"),event_bus=bus)


def test_entities_relations_duplicate_delete(tmp_path):
    bus=Bus(); item=graph(tmp_path,bus)
    item.add_entity("A",EntityType.SYSTEM,"A"); item.add_entity("B",EntityType.SERVICE,"B")
    relation=item.add_relation("A",RelationType.USES,"B",0.8,"system")
    duplicate=item.add_relation("A",RelationType.USES,"B",0.7,"system")
    assert relation.id==duplicate.id and len(item.search_relations())==1
    assert item.get_entity("a").name=="A" and item.find_entity("B")[0].id=="B"
    assert item.remove_relation(relation.id) and not item.remove_relation(relation.id)
    item.close()


def test_neighbors_path_and_search(tmp_path):
    item=graph(tmp_path)
    for name in "ABC": item.add_entity(name,EntityType.CONCEPT,name)
    item.add_relation("A","RELATED_TO","B",source="user_explicit")
    item.add_relation("B","DEPENDS_ON","C",source="system")
    assert len(item.neighbors("B"))==2
    assert item.find_path("A","C")==["A","B","C"]
    assert item.search_relations("B")
    item.close()


def test_memory_integration_is_explicit_and_limited(tmp_path):
    item=graph(tmp_path)
    memory=MemoryManager({"enabled":True,"database_path":str(tmp_path/"memory.db"),"working_memory_max_messages":5,"max_retrieval_results":5})
    memory.knowledge=item
    memory.remember(MemoryType.PREFERENCE,"preferred_browser","chrome",source="user_explicit")
    assert item.neighbors("USER_REFERENCE",RelationType.PREFERS)[0].target_entity=="CHROME"
    memory.remember(MemoryType.FACT,"unrelated","value",source="user_explicit")
    assert len(item.search_relations())==1
    memory.close(); item.close()


def test_node_integration_and_capabilities(tmp_path):
    bus=Bus(); item=graph(tmp_path,bus)
    primary=SimpleNamespace(payload={"node_id":"primary_pc","name":"Primary PC","node_type":"PRIMARY_PC","capabilities":["brain"]})
    mobile=SimpleNamespace(payload={"node_id":"mobile_node","name":"Mobile","node_type":"MOBILE","capabilities":["notifications"]})
    item._on_node(primary); item._on_node(mobile)
    assert item.neighbors("MOBILE_NODE",RelationType.CONNECTED_TO)[0].target_entity=="PRIMARY_PC"
    assert len(item.neighbors("MOBILE_NODE",RelationType.HAS_CAPABILITY))==1
    item.close()


def test_seed_queries_and_small_graph_performance(tmp_path):
    item=graph(tmp_path); item.seed([SimpleNamespace(id="browser.open_url",name="Browser",category="browser")])
    assert "OLLAMA" in item.answer("Qual projeto usa Ollama?")
    assert "CONTROLS" in item.answer("Que Skills controlam o navegador?")
    started=perf_counter()
    for _ in range(200): item.neighbors("ISABELLA_PROJECT")
    assert (perf_counter()-started)*1000<250
    item.close()
