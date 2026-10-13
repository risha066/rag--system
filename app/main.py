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

# ---- Groq client helper ----
def get_groq_client():
    key = os.getenv("GROQ_API_KEY") or os.getenv("groq_api_key") or ""
    if not key or not key.strip():
        return None
    try:
        from groq import Groq
        return Groq(api_key=key.strip())
    except Exception:
        return None

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

def semantic_search(db: Session, question: str, top_k: int = 15):
    chunks = db.query(Chunk).filter(Chunk.embedding.isnot(None)).all()
    if not chunks:
        return []
    q_vec = embed(question)
    q_clean = question.lower().strip()
    q_words = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 2]
    
    scored = []
    for c in chunks:
        try:
            sim = cosine(q_vec, c.embedding)
            c_text_lower = (c.text or "").lower()
            
            # 1. Exact phrase boost (e.g. "functions of statistics")
            phrase_boost = 0.0
            if q_clean in c_text_lower:
                phrase_boost = 1.2
            elif "functions of" in c_text_lower or "function of" in c_text_lower:
                phrase_boost = 0.8
            elif "functions:" in c_text_lower or "function:" in c_text_lower:
                phrase_boost = 0.7
                
            # 2. Keyword boost
            keyword_matches = sum(1 for w in q_words if w in c_text_lower)
            word_boost = (keyword_matches / max(len(q_words), 1)) * 0.4

            # 3. Later numbered items (10–15) often sit in later chunks
            if re.search(r"\b(?:1[0-5]|[6-9])\s*[.)]", c.text or ""):
                phrase_boost += 0.35
            
            total = sim + phrase_boost + word_boost
            scored.append((total, c))
        except Exception:
            continue
            
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]

LIST_QUESTION_RE = re.compile(
    r"\b(function|functions|characteristic|characteristics|type|types|point|points|"
    r"advantage|advantages|disadvantage|disadvantages|step|steps|feature|features|"
    r"principle|principles|list|enumerate|explain all|all the)\b",
    re.I,
)
NUM_ITEM_RE = re.compile(r"(?:^|[^\d])(?:\(|\[)?(\d{1,2})(?:\)|\.|:)\s")

def extract_item_numbers(text: str) -> set:
    nums = set()
    for m in NUM_ITEM_RE.finditer(text or ""):
        n = int(m.group(1))
        if 1 <= n <= 30:
            nums.add(n)
    return nums

def chunk_sort_key(c):
    cid = c.id or ""
    if "_" in cid:
        try:
            prefix, idx = cid.rsplit("_", 1)
            return (c.document_id or "", prefix, int(idx))
        except Exception:
            pass
    return (c.document_id or "", cid, 0)

def load_document_chunks(db: Session, doc_id: str):
    rows = db.query(Chunk).filter(Chunk.document_id == doc_id).all()
    rows.sort(key=chunk_sort_key)
    return rows

def stitch_chunk_texts(chunks) -> str:
    ordered = sorted(chunks, key=chunk_sort_key)
    if not ordered:
        return ""
    text = ordered[0].text or ""
    for c in ordered[1:]:
        piece = c.text or ""
        if not piece:
            continue
        overlap = 0
        max_o = min(len(text), len(piece), 400)
        for o in range(max_o, 30, -1):
            if text.endswith(piece[:o]):
                overlap = o
                break
        text += piece[overlap:] if overlap else (" " + piece)
    return text

def extract_numbered_points(text: str):
    """Pull sequential 1..N list items out of flattened document text."""
    matches = list(re.finditer(r'(?:(?<=^)|(?<=[\s;]))(\d{1,2})\s*[.)]\s+', text or ""))
    if not matches:
        return []
    items = []
    for i, m in enumerate(matches):
        n = int(m.group(1))
        if n < 1 or n > 30:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = re.sub(r"\s+", " ", (text[start:end] or "").strip())
        if content:
            items.append((n, content))

    best, run, expected = [], [], None
    for n, content in items:
        if n == 1:
            run = [(n, content)]
            expected = 2
        elif expected is not None and n == expected:
            run.append((n, content))
            expected = n + 1
        else:
            if len(run) > len(best):
                best = run
            run, expected = ([], None)
            if n == 1:
                run = [(n, content)]
                expected = 2
    if len(run) > len(best):
        best = run
    return best

def format_point_list(points) -> str:
    return "\n\n".join(f"{n}. {t}" for n, t in points)

def expand_list_context(db: Session, chunks, question: str):
    """Load the whole top document for list questions so later points are not dropped."""
    by_id = {c.id: c for c in chunks}
    doc_ids = []
    seen = set()
    for c in chunks:
        if c.document_id and c.document_id not in seen:
            seen.add(c.document_id)
            doc_ids.append(c.document_id)

    if LIST_QUESTION_RE.search(question or "") and doc_ids:
        for extra in load_document_chunks(db, doc_ids[0]):
            by_id[extra.id] = extra
    else:
        prefixes = {}
        for c in list(by_id.values()):
            if "_" in (c.id or ""):
                try:
                    prefix, idx = c.id.rsplit("_", 1)
                    prefixes.setdefault(prefix, []).append(int(idx))
                except Exception:
                    pass
        for prefix, idxs in prefixes.items():
            lo, hi = min(idxs), max(idxs) + 6
            for i in range(max(0, lo - 1), hi + 1):
                nid = f"{prefix}_{i}"
                if nid not in by_id:
                    extra = db.query(Chunk).filter(Chunk.id == nid).first()
                    if extra:
                        by_id[nid] = extra

    result = list(by_id.values())
    result.sort(key=chunk_sort_key)
    return result

# ================= CHUNKING =================
def chunk_text(text: str, size: int = 1200, overlap: int = 200):
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

    db.query(Chunk).filter(Chunk.document_id == req.document_id).delete()
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
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    vecs = embed_batch(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(id=f"{doc_id}_{i}", document_id=doc_id,
                     title=filename, text=piece, embedding=vec))
    db.commit()
    return {"message": f"{filename} uploaded", "document_id": doc_id, "chunks": len(pieces)}

@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    chunks = semantic_search(db, req.question, top_k=20)
    if not chunks:
        return {"answer": "No documents found. Please ingest some documents first."}

    top_doc_id = chunks[0].document_id
    doc_chunks = load_document_chunks(db, top_doc_id) if top_doc_id else expand_list_context(db, chunks, req.question)
    stitched = stitch_chunk_texts(doc_chunks)
    points = extract_numbered_points(stitched)
    unique_sources = list(dict.fromkeys([(c.title or c.document_id) for c in doc_chunks]))
    source_line = f"\n\n📚 Sources: {', '.join(unique_sources)}"

    is_list_q = bool(LIST_QUESTION_RE.search(req.question or ""))
    extracted = format_point_list(points) if points else ""

    # If the notes contain a real numbered list, return that list as the source of truth.
    # The model may polish wording, but missing points fall back to the extracted text.
    groq_client = get_groq_client()
    if groq_client and extracted and (is_list_q or len(points) >= 6):
        system = (
            "You copy and clearly present a numbered list from the user's notes. "
            "You must keep EVERY point number and the original meaning. "
            "Do not summarize. Do not skip later points. Do not invent extra points."
        )
        user = (
            f"Question: {req.question}\n\n"
            f"COMPLETE LIST FROM THE DOCUMENT ({len(points)} points, {points[0][0]} through {points[-1][0]}):\n"
            f"{extracted}\n\n"
            f"Rewrite this list clearly. Include all {len(points)} points with their original numbers."
        )
        models_to_try = [
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
        ]
        for m in models_to_try:
            try:
                resp = groq_client.chat.completions.create(
                    model=m,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.1,
                    max_tokens=4096,
                )
                answer = (resp.choices[0].message.content or "").strip()
                missing = [n for n, _ in points if n not in extract_item_numbers(answer)]
                if missing or not answer:
                    answer = extracted
                return {"answer": answer + source_line}
            except Exception:
                continue
        return {"answer": extracted + source_line}

    all_chunks_to_use = doc_chunks if (is_list_q and doc_chunks) else expand_list_context(db, chunks, req.question)
    context = stitched if stitched else "\n\n".join(
        f"[Source {i} - {c.title or c.document_id}]\n{c.text}"
        for i, c in enumerate(all_chunks_to_use, 1)
    )
    unique_sources = list(dict.fromkeys([(c.title or c.document_id) for c in all_chunks_to_use]))
    source_line = f"\n\n📚 Sources: {', '.join(unique_sources)}"

    if groq_client:
        system = (
            "Answer using ONLY the document context. Be complete and accurate. "
            "Do not skip later sections of the notes. "
            "If the context is insufficient, say you couldn't find it."
        )
        user = f"Context:\n{context}\n\nQuestion: {req.question}\n\nComplete answer:"
        models_to_try = [
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
        ]
        for m in models_to_try:
            try:
                resp = groq_client.chat.completions.create(
                    model=m,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.1,
                    max_tokens=4096,
                )
                answer = (resp.choices[0].message.content or "").strip()
                if answer:
                    return {"answer": answer + source_line}
            except Exception:
                continue

    if extracted:
        return {"answer": extracted + source_line}
    return {"answer": f"Top matches for your question:\n\n{context[:4000]}"}

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
