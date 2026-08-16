from collections import deque


def find_path(relations_for, start: str, target: str, max_depth: int = 4):
    queue=deque([(start,[start])]); visited={start}
    while queue:
        current,path=queue.popleft()
        if len(path)-1>=max_depth: continue
        for relation in relations_for(current):
            neighbor=relation.target_entity if relation.source_entity==current else relation.source_entity
            if neighbor==target: return path+[neighbor]
            if neighbor not in visited: visited.add(neighbor); queue.append((neighbor,path+[neighbor]))
    return []


def relationship_question(text: str) -> bool:
    normalized=text.casefold()
    return any(phrase in normalized for phrase in ("ligados ao","conectados ao","skills controlam","skill controla","projeto usa","depende de","relacionado a"))
