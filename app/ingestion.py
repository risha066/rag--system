import hashlib
from sqlalchemy.orm import Session
from .models import Document, Chunk
from .embeddings import embed_batch
from .llm_service import extract_entities_relationships
from .graph_db import ingest_extraction, ONTOLOGY

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = start + size
        chunks.append(" ".join(words[start:end]))
        start = end - overlap
    return chunks

def hash_document(text):
    return hashlib.sha256(text.encode()).hexdigest()[:24]

def ingest_document(db, document_id, title, text, owner_id):
    existing = db.query(Document).filter(Document.id == document_id).first()
    if existing:
        return {"status": "cached", "document_id": document_id}
    
    doc = Document(id=document_id, title=title, owner_id=owner_id)
    db.add(doc)
    db.flush()
    
    chunks = chunk_text(text)
    embeddings = embed_batch(chunks)
    
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_id = f"{document_id}::{i}"
        extraction = extract_entities_relationships(chunk, chunk_id, ONTOLOGY)
        ingest_extraction(extraction)
        entity_ids = [e["canonical_name"].lower() for e in extraction.get("entities", [])]
        
        db.add(Chunk(
            id=chunk_id,
            document_id=document_id,
            section_path=f"chunk_{i}",
            text=chunk,
            entity_ids=entity_ids,
            embedding=emb,
        ))
    
    db.commit()
    return {"status": "ingested", "document_id": document_id, "chunks": len(chunks)}