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
from sentence_transformers import SentenceTransformer
EMBED_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
EMBED_DIM = 384

SECRET_KEY = os.getenv("JWT_SECRET", "change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

DATABASE_URL = "sqlite:///./rag.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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
    embedding = Column(JSON)          # list of 384 floats

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
    vec = EMBED_MODEL.encode([text], normalize_embeddings=True)[0]
    return vec.tolist()

def embed_batch(texts):
    vecs = EMBED_MODEL.encode(texts, normalize_embeddings=True, batch_size=16)
    return [v.tolist() for v in vecs]

def cosine(a, b):
    a = np.array(a); b = np.array(b)
    return float(np.dot(a, b))   # already normalized

def semantic_search(db: Session, question: str, top_k: int = 5):
    chunks = db.query(Chunk).filter(Chunk.embedding.isnot(None)).all()
    if not chunks:
        return []
    q_vec = embed(question)
    scored = []
    for c in chunks:
        try:
            s = cosine(q_vec, c.embedding)
            scored.append((s, c))
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
    return [p for p in pieces if len(p.strip()) > 30]

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

    # Build context
    context_parts = []
    sources = []
    for i, c in enumerate(chunks, 1):
        context_parts.append(f"[Source {i} - {c.title or c.document_id}]\n{c.text}")
        sources.append(c.title or c.document_id)
    context = "\n\n".join(context_parts)
    unique_sources = list(dict.fromkeys(sources))

    # If Groq is available -> real answer
    if groq_client:
        system = (
            "You are a precise assistant. Answer the user's question using ONLY the "
            "context provided. Be accurate and concise. If the answer isn't in the "
            "context, say 'I couldn't find that in your documents.'"
        )
        user = f"Context:\n{context}\n\nQuestion: {req.question}\n\nAnswer:"
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role":"system","content":system},
                          {"role":"user","content":user}],
                temperature=0.2,
                max_tokens=600,
            )
            answer = resp.choices[0].message.content.strip()
            answer += f"\n\n📚 Sources: {', '.join(unique_sources)}"
            return {"answer": answer}
        except Exception as e:
            # fall through to raw context if Groq fails
            return {"answer": f"(Groq error: {e})\n\nTop matches from your documents:\n\n{context[:1200]}"}

    # No Groq -> return best matching chunks
    return {"answer": f"Top matches for your question:\n\n{context[:1500]}"}

@app.get("/documents")
def get_documents(db: Session = Depends(get_db)):
    return [{"id": d.id, "title": d.title} for d in db.query(Document).all()]

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
