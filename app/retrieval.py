import time
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import Chunk, QueryLog
from .embeddings import embed_text
from .llm_service import classify_route, generate_answer
from .graph_db import get_related
def vector_search(db: Session, question: str, k: int = 5) -> list[dict]:
    q_emb = embed_text(question)
    rows = db.execute(
        select(Chunk.id, Chunk.text)
        .order_by(Chunk.embedding.cosine_distance(q_emb))
        .limit(k)
    ).all()
    return [{"chunk_id": r.id, "text": r.text} for r in rows]

def graph_search(question: str, entities: list[str]) -> list[str]:
    statements = []
    for name in entities:
        related = get_related(name)
        # Handle if related returns list of dicts or strings
        if related:
            for item in related:
                if isinstance(item, dict):
                    # If it's a dict, extract the name or relationship
                    if 'name' in item:
                        statements.append(f"{name} is related to {item['name']}")
                    elif 'relationship' in item:
                        statements.append(f"{name} {item['relationship']} {item.get('target', '')}")
                    else:
                        statements.append(str(item))
                elif isinstance(item, str):
                    statements.append(item)
                else:
                    statements.append(str(item))
    return statements

def naive_entity_guess(question: str, db: Session, k: int = 3) -> list[str]:
    # Cheap heuristic: pull entity_ids attached to the top vector hits
    hits = vector_search(db, question, k=k)
    ids = set()
    for h in hits:
        chunk = db.query(Chunk).filter(Chunk.id == h["chunk_id"]).first()
        if chunk and chunk.entity_ids:
            ids.update(chunk.entity_ids)
    return list(ids)[:5]

def answer_question(db: Session, user_id: int, question: str) -> dict:
    start = time.perf_counter()
    route = classify_route(question)

    passages, graph_statements = [], []
    if route in ("VECTOR", "HYBRID"):
        passages = vector_search(db, question)
    if route in ("GRAPH", "HYBRID"):
        entities = naive_entity_guess(question, db)
        graph_statements = graph_search(question, entities)
    if route == "GRAPH" and not graph_statements:
        # low-confidence fallback: merge in vector too
        passages = vector_search(db, question)

    answer = generate_answer(question, graph_statements, passages)
    latency_ms = int((time.perf_counter() - start) * 1000)

    db.add(QueryLog(user_id=user_id, question=question, route=route, outcome=answer, latency_ms=latency_ms))
    db.commit()

    return {
        "answer": answer,
        "route": route,
        "citations": passages,
        "latency_ms": latency_ms,
    }