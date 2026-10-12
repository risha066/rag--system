from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime, timedelta
from jose import jwt
from pydantic import BaseModel
import hashlib, secrets, os, io, math, json, re
import numpy as np

try:
    import PyPDF2
except Exception:
    PyPDF2 = None
try:
    import docx
except Exception:
    docx = None

# ---- Groq client (optional) ----
try:
    from groq import Groq
    GROQ_KEY = os.getenv("GROQ_API_KEY", "")
    groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None
except Exception:
    groq_client = None

# ---- Embeddings (local, free) ----
# ---- Embeddings (memory-efficient via fastembed/onnx) ----
EMBED_MODEL = None
EMBED_DIM = 384

def get_embed_model():
    global EMBED_MODEL
    if EMBED_MODEL is None:
        try:
            from fastembed import TextEmbedding
            EMBED_MODEL = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
        except Exception:
            from sentence_transformers import SentenceTransformer
            EMBED_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return EMBED_MODEL

SECRET_KEY = os.getenv("JWT_SECRET", "change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rag.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ================= AUTH =================
def get_password_hash(p):
    salt = secrets.token_hex(16)
    return salt + ":" + hashlib.sha256((salt + p).encode()).hexdigest()

def verify_password(plain, hashed):
    if ":" not in hashed: return False
    salt, h = hashed.split(":")
    return h == hashlib.sha256((salt + plain).encode()).hexdigest()

def create_access_token(data):
    d = data.copy()
    d.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)

# ================= MODELS =================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, index=True)
    title = Column(String)
    owner_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String)
    title = Column(String)
    text = Column(Text)
    embedding = Column(JSON)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# ================= SCHEMAS =================
class UserCreate(BaseModel):
    email: str
    password: str

class IngestRequest(BaseModel):
    document_id: str
    title: str
    text: str

class QueryRequest(BaseModel):
    question: str

# ================= APP =================
app = FastAPI(title="Knowledge Graph RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= EMBEDDING HELPERS =================
def embed(text: str):
    model = get_embed_model()
    if hasattr(model, "embed"):
        return list(model.embed([text]))[0].tolist()
    return model.encode([text], normalize_embeddings=True)[0].tolist()

def embed_batch(texts):
    model = get_embed_model()
    if hasattr(model, "embed"):
        return [v.tolist() for v in model.embed(texts)]
    return [v.tolist() for v in model.encode(texts, normalize_embeddings=True, batch_size=16)]

def cosine(a, b):
    a = np.array(a, dtype=np.float32)
    b = np.array(b, dtype=np.float32)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

def semantic_search(db: Session, question: str, top_k: int = 7):
    chunks = db.query(Chunk).filter(Chunk.embedding.isnot(None)).all()
    if not chunks:
        return []
    q_vec = embed(question)
    q_words = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 2]
    scored = []
    for c in chunks:
        try:
            sim = cosine(q_vec, c.embedding)
            c_text_lower = (c.text or "").lower()
            keyword_matches = sum(1 for w in q_words if w in c_text_lower)
            boost = (keyword_matches / max(len(q_words), 1)) * 0.35
            scored.append((sim + boost, c))
        except Exception:
            continue
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]

# ================= CHUNKING =================
def chunk_text(text: str, size: int = 700, overlap: int = 100):
    text = re.sub(r"\s+", " ", text).strip()
    pieces, start = [], 0
    while start < len(text):
        end = start + size
        pieces.append(text[start:end])
        start = end - overlap
    return [p for p in pieces if len(p.strip()) > 3]

# ================= ROUTES =================
@app.get("/")
def root():
    return {"message": "RAG System API is running!"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/auth/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    u = User(email=user.email, hashed_password=get_password_hash(user.password))
    db.add(u); db.commit(); db.refresh(u)
    return {"message": "User created", "email": user.email}

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token({"sub": user.email}), "token_type": "bearer"}

@app.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    if not db.query(Document).filter(Document.id == req.document_id).first():
        db.add(Document(id=req.document_id, title=req.title, owner_id=1)); db.commit()

    pieces = chunk_text(req.text)
    if not pieces:
        return {"status": "empty", "chunks": 0}

    vecs = embed_batch(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(id=f"{req.document_id}_{i}", document_id=req.document_id,
                     title=req.title, text=piece, embedding=vec))
    db.commit()
    return {"status": "ingested", "chunks": len(pieces), "document_id": req.document_id}

@app.post("/upload/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = (file.filename or "upload")
    fl = filename.lower()
    content_bytes = await file.read()
    content = ""

    try:
        if fl.endswith(".txt"):
            content = content_bytes.decode("utf-8", errors="ignore")
        elif fl.endswith(".pdf"):
            if PyPDF2 is None: raise HTTPException(500, "PDF support not installed")
            reader = PyPDF2.PdfReader(io.BytesIO(content_bytes))
            for page in reader.pages:
                content += (page.extract_text() or "") + "\n"
        elif fl.endswith(".docx"):
            if docx is None: raise HTTPException(500, "DOCX support not installed")
            d = docx.Document(io.BytesIO(content_bytes))
            for para in d.paragraphs:
                content += para.text + "\n"
        else:
            raise HTTPException(400, "Only .txt, .pdf, .docx allowed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Read error: {e}")

    if not content.strip():
        raise HTTPException(400, "No text found in file")

    doc_id = re.sub(r"[^a-z0-9]+", "_", fl).strip("_")
    if not db.query(Document).filter(Document.id == doc_id).first():
        db.add(Document(id=doc_id, title=filename, owner_id=1)); db.commit()

    pieces = chunk_text(content)
    vecs = embed_batch(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(id=f"{doc_id}_{i}", document_id=doc_id,
                     title=filename, text=piece, embedding=vec))
    db.commit()
    return {"message": f"{filename} uploaded", "document_id": doc_id, "chunks": len(pieces)}

@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    chunks = semantic_search(db, req.question, top_k=5)
    if not chunks:
        return {"answer": "No documents found. Please ingest some documents first."}

    context_parts = []
    sources = []
    for i, c in enumerate(chunks, 1):
        context_parts.append(f"[Source {i} - {c.title or c.document_id}]\n{c.text}")
        sources.append(c.title or c.document_id)
    context = "\n\n".join(context_parts)
    unique_sources = list(dict.fromkeys(sources))

    if groq_client:
        system = (
            "You are a helpful, intelligent assistant. Answer the user's question clearly, thoroughly, "
            "and accurately based on the provided context from their documents. Summarize and explain the relevant points. "
            "Only if the provided context is completely unrelated or empty, state: "
            "'I couldn't find that in your documents.'"
        )
        user = f"Context from documents:\n{context}\n\nQuestion: {req.question}\n\nAnswer:"
        
        models_to_try = [
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "llama-3.1-8b-instant",
            "gemma2-9b-it"
        ]
        for m in models_to_try:
            try:
                resp = groq_client.chat.completions.create(
                    model=m,
                    messages=[{"role":"system","content":system},
                              {"role":"user","content":user}],
                    temperature=0.2,
                    max_tokens=800,
                )
                answer = resp.choices[0].message.content.strip()
                answer += f"\n\n📚 Sources: {', '.join(unique_sources)}"
                return {"answer": answer}
            except Exception:
                continue

    return {"answer": f"Top matches for your question:\n\n{context[:1500]}"}

@app.get("/documents")
def get_documents(db: Session = Depends(get_db)):
    return [{"id": d.id, "title": d.title} for d in db.query(Document).all()]

# ---------- DELETE DOCUMENT (NEW) ----------
@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    return {"message": f"Deleted {doc_id}"}

@app.get("/chunks")
def get_chunks(db: Session = Depends(get_db)):
    return [{"id": c.id, "document_id": c.document_id} for c in db.query(Chunk).all()]

@app.get("/entities")
def get_entities(): return []

@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    top = semantic_search(db, q, top_k=5)
    results = [{"chunk_id": c.id, "document_id": c.document_id,
                "title": c.title, "text": c.text[:250]} for c in top]
    return {"results": results, "total": len(results)}
