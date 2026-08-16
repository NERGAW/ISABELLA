"""Lightweight persistent relationship graph."""

from .graph import KnowledgeGraph, load_knowledge_config
from .models import Entity, EntityType, Relation, RelationType

__all__ = ["Entity", "EntityType", "KnowledgeGraph", "Relation", "RelationType", "load_knowledge_config"]
