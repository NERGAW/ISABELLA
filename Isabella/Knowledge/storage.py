from __future__ import annotations

import json, sqlite3, threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Entity, EntityType, Relation, RelationType


SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY,type TEXT NOT NULL,name TEXT NOT NULL,attributes TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS relations(id INTEGER PRIMARY KEY AUTOINCREMENT,source_entity TEXT NOT NULL,relation_type TEXT NOT NULL,target_entity TEXT NOT NULL,confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),source TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(source_entity) REFERENCES entities(id),FOREIGN KEY(target_entity) REFERENCES entities(id),UNIQUE(source_entity,relation_type,target_entity,source));
CREATE INDEX IF NOT EXISTS idx_relation_source ON relations(source_entity);
CREATE INDEX IF NOT EXISTS idx_relation_target ON relations(target_entity);
"""


class KnowledgeStorage:
    def __init__(self,path:Path):
        self.path=path; path.parent.mkdir(parents=True,exist_ok=True); self._lock=threading.RLock()
        self._connection=sqlite3.connect(path,check_same_thread=False); self._connection.row_factory=sqlite3.Row
        with self._lock: self._connection.executescript(SCHEMA); self._connection.commit()
    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    @staticmethod
    def entity(row): return Entity(row["id"],EntityType(row["type"]),row["name"],json.loads(row["attributes"]),row["created_at"],row["updated_at"])
    @staticmethod
    def relation(row): return Relation(row["id"],row["source_entity"],RelationType(row["relation_type"]),row["target_entity"],row["confidence"],row["source"],row["created_at"])
    def upsert_entity(self,entity_id,entity_type,name,attributes):
        now=self.now()
        with self._lock:
            self._connection.execute("INSERT INTO entities VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,name=excluded.name,attributes=excluded.attributes,updated_at=excluded.updated_at",(entity_id,entity_type.value,name,json.dumps(attributes,ensure_ascii=False),now,now)); self._connection.commit()
            row=self._connection.execute("SELECT * FROM entities WHERE id=?",(entity_id,)).fetchone()
        return self.entity(row)
    def get_entity(self,entity_id):
        with self._lock: row=self._connection.execute("SELECT * FROM entities WHERE id=?",(entity_id,)).fetchone()
        return self.entity(row) if row else None
    def find_entities(self,query,entity_type=None,limit=25):
        pattern=f"%{query.replace('%','').replace('_','')}%"; sql="SELECT * FROM entities WHERE (id LIKE ? OR name LIKE ?)"; params=[pattern,pattern]
        if entity_type: sql+=" AND type=?"; params.append(entity_type.value)
        sql+=" ORDER BY name LIMIT ?"; params.append(limit)
        with self._lock: rows=self._connection.execute(sql,params).fetchall()
        return [self.entity(row) for row in rows]
    def add_relation(self,source,kind,target,confidence,provenance):
        now=self.now()
        with self._lock:
            self._connection.execute("INSERT INTO relations(source_entity,relation_type,target_entity,confidence,source,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(source_entity,relation_type,target_entity,source) DO UPDATE SET confidence=MAX(confidence,excluded.confidence)",(source,kind.value,target,confidence,provenance,now)); self._connection.commit()
            row=self._connection.execute("SELECT * FROM relations WHERE source_entity=? AND relation_type=? AND target_entity=? AND source=?",(source,kind.value,target,provenance)).fetchone()
        return self.relation(row)
    def remove_relation(self,relation_id):
        with self._lock: cursor=self._connection.execute("DELETE FROM relations WHERE id=?",(relation_id,)); self._connection.commit(); return cursor.rowcount>0
    def relations(self,entity_id=None,kind=None,query=None,limit=100):
        clauses=[]; params=[]
        if entity_id: clauses.append("(source_entity=? OR target_entity=?)"); params += [entity_id,entity_id]
        if kind: clauses.append("relation_type=?"); params.append(kind.value)
        if query: clauses.append("(source_entity LIKE ? OR target_entity LIKE ? OR relation_type LIKE ?)"); params += [f"%{query}%"]*3
        sql="SELECT * FROM relations"+(" WHERE "+" AND ".join(clauses) if clauses else "")+" ORDER BY id DESC LIMIT ?"; params.append(limit)
        with self._lock: rows=self._connection.execute(sql,params).fetchall()
        return [self.relation(row) for row in rows]
    def counts(self):
        with self._lock: return {"entities":self._connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],"relations":self._connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0]}
    def health_check(self):
        try:
            with self._lock: return self._connection.execute("SELECT 1").fetchone()[0]==1
        except sqlite3.Error: return False
    def close(self):
        with self._lock: self._connection.close()
