from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime, timedelta
from jose import jwt
from pydantic import BaseModel
import hashlib, secrets, os, io, math, re
from collections import Counter

# Optional PDF/DOCX
try:
    import PyPDF2
except Exception:
    PyPDF2 = None
try:
    import docx
except Exception:
    docx = None

SECRET_KEY = os.getenv("JWT_SECRET", "change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

DATABASE_URL = "sqlite:///./rag.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ================= PASSWORD =================
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
    title = Column(String)               # <-- store doc title on chunk
    text = Column(Text)

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

# ================= TEXT UTILS (BM25-lite search) =================
STOPWORDS = set("""a an the is are was were be been being of to in on for with and or
not but if then so as at by from this that these those it its i you he she we they
what which who whom how why when where do does did can could should would will""".split())

def tokenize(text: str):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]

def bm25_score(query_terms, doc_terms, avg_len, N, df):
    k1, b = 1.5, 0.75
    score = 0.0
    L = len(doc_terms)
    for term in query_terms:
        f = doc_terms.count(term)
        if f == 0: continue
        idf = math.log((N - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1)
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * L / avg_len))
    return score

def smart_search(db: Session, question: str, top_k: int = 3):
    """BM25-ish keyword search across chunks. Returns best chunks."""
    chunks = db.query(Chunk).all()
    if not chunks:
        return []

    query_terms = tokenize(question)
    if not query_terms:
        return chunks[:top_k]

    docs_tokens = [tokenize(c.text) for c in chunks]
    N = len(chunks)
    avg_len = sum(len(t) for t in docs_tokens) / N
    df = Counter()
    for terms in docs_tokens:
        for t in set(terms):
            df[t] += 1

    scored = []
    for c, terms in zip(chunks, docs_tokens):
        s = bm25_score(query_terms, terms, avg_len, N, df)
        scored.append((s, c))

    scored.sort(key=lambda x: -x[0])
    return [c for s, c in scored[:top_k]]

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
    # store with title on the chunk
    db.add(Chunk(id=f"{req.document_id}_{datetime.utcnow().timestamp()}",
                 document_id=req.document_id, title=req.title, text=req.text))
    db.commit()
    return {"status": "ingested", "chunks": 1, "document_id": req.document_id}

@app.post("/upload/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = (file.filename or "upload").lower()
    content_bytes = await file.read()
    content = ""

    try:
        if filename.endswith(".txt"):
            content = content_bytes.decode("utf-8", errors="ignore")
        elif filename.endswith(".pdf"):
            if PyPDF2 is None:
                raise HTTPException(status_code=500, detail="PDF support not installed")
            reader = PyPDF2.PdfReader(io.BytesIO(content_bytes))
            for page in reader.pages:
                content += (page.extract_text() or "") + "\n"
        elif filename.endswith(".docx"):
            if docx is None:
                raise HTTPException(status_code=500, detail="DOCX support not installed")
            d = docx.Document(io.BytesIO(content_bytes))
            for para in d.paragraphs:
                content += para.text + "\n"
        else:
            raise HTTPException(status_code=400, detail="Only .txt, .pdf, .docx allowed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Read error: {e}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="No text found in file")

    doc_id = filename.replace(" ", "_").replace(".", "_")
    if not db.query(Document).filter(Document.id == doc_id).first():
        db.add(Document(id=doc_id, title=file.filename, owner_id=1)); db.commit()

    chunk_size = 800
    n = 0
    for i in range(0, len(content), chunk_size):
        piece = content[i:i+chunk_size]
        db.add(Chunk(id=f"{doc_id}_{i}", document_id=doc_id, title=file.filename, text=piece))
        n += 1
    db.commit()

    return {"message": f"{file.filename} uploaded", "document_id": doc_id, "chunks": n}

@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    top = smart_search(db, req.question, top_k=3)
    if not top:
        return {"answer": "No documents found. Please ingest some documents first."}

    parts = []
    for c in top:
        title = c.title or c.document_id
        parts.append(f"📄 [{title}]\n{c.text.strip()}")

    context = "\n\n---\n\n".join(parts)
    return {"answer": f"Here is what I found in your documents:\n\n{context[:1200]}"}

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
    top = smart_search(db, q, top_k=5)
    results = [{"chunk_id": c.id, "document_id": c.document_id, "title": c.title, "text": c.text[:250]} for c in top]
    return {"results": results, "total": len(results)}
