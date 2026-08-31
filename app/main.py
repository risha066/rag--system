from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import init_db, get_db
from .routers import auth_router, ingest_router, query_router, upload_router
from .auth import get_current_user
from .models import User, Document, Chunk
from .graph_db import get_all_entities
from .llm_service import _chat

app = FastAPI(title="Knowledge Graph RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

app.include_router(auth_router.router)
app.include_router(ingest_router.router)
app.include_router(query_router.router)
app.include_router(upload_router.router, prefix="/upload", tags=["upload"])

@app.get("/health")
def health():
    return {"status": "ok"}

# ==================== DOCUMENTS ====================
@app.get("/documents")
def get_documents(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    docs = db.query(Document).filter(Document.owner_id == user.id).all()
    return [{"id": d.id, "title": d.title, "created_at": d.created_at} for d in docs]

@app.get("/chunks")
def get_chunks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc_ids = [d[0] for d in db.query(Document.id).filter(Document.owner_id == user.id).all()]
    if not doc_ids:
        return []
    chunks = db.query(Chunk).filter(Chunk.document_id.in_(doc_ids)).all()
    return [{"id": c.id, "document_id": c.document_id} for c in chunks]

@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.owner_id == user.id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    db.delete(doc)
    db.commit()
    return {"message": f"Document {doc_id} deleted"}

@app.get("/entities")
def get_entities():
    return get_all_entities()

# ==================== SEARCH ====================
@app.get("/search")
def search_documents(q: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc_ids = [d[0] for d in db.query(Document.id).filter(Document.owner_id == user.id).all()]
    if not doc_ids:
        return {"results": []}
    chunks = db.query(Chunk).filter(Chunk.document_id.in_(doc_ids)).all()
    results = []
    q_lower = q.lower()
    for chunk in chunks:
        if q_lower in chunk.text.lower():
            results.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "text": chunk.text[:300] + "..." if len(chunk.text) > 300 else chunk.text
            })
    return {"results": results[:10], "total": len(results)}

# ==================== SUMMARIZE ====================
@app.post("/summarize")
def summarize_document(data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.llm_service import _chat
    doc_id = data.get("doc_id")
    doc = db.query(Document).filter(Document.id == doc_id, Document.owner_id == user.id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    chunks = db.query(Chunk).filter(Chunk.document_id == doc_id).all()
    if not chunks:
        return {"summary": "No content to summarize", "title": doc.title}
    full_text = " ".join([c.text for c in chunks])
    if len(full_text) > 8000:
        full_text = full_text[:8000] + "..."
    system = "You are a helpful assistant. Summarize the following document concisely in 3-5 sentences."
    user_msg = f"Document: {doc.title}\n\nContent: {full_text}\n\nProvide a concise summary."
    try:
        summary = _chat("openai/gpt-oss-20b", system, user_msg)
        return {"summary": summary, "document_id": doc_id, "title": doc.title}
    except Exception as e:
        return {"summary": f"Error: {str(e)}", "title": doc.title}

# ==================== EXPORT ====================
@app.get("/export/{doc_id}")
def export_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from fastapi.responses import PlainTextResponse
    doc = db.query(Document).filter(Document.id == doc_id, Document.owner_id == user.id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    chunks = db.query(Chunk).filter(Chunk.document_id == doc_id).all()
    content = f"Title: {doc.title}\nID: {doc.id}\n\n"
    for c in chunks:
        content += c.text + "\n\n"
    return PlainTextResponse(content, headers={"Content-Disposition": f"attachment; filename={doc_id}.txt"})