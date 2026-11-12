from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime, timedelta
from jose import jwt
from pydantic import BaseModel
import hashlib, secrets, os, io, math, json, re, glob
import numpy as np

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

try:
    import pypdf
except Exception:
    pypdf = None

try:
    import docx
except Exception:
    docx = None

# ================= CONFIGURATION & CLIENTS =================
SECRET_KEY = os.getenv("JWT_SECRET", "docuai-rag-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rag.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_groq_client():
    """Returns Groq client if API key is provided, else None for graceful fallback."""
    key = os.getenv("GROQ_API_KEY") or os.getenv("groq_api_key") or ""
    if not key or not key.strip():
        return None
    try:
        from groq import Groq
        return Groq(api_key=key.strip())
    except Exception:
        return None


# ================= EMBEDDINGS (Local, Free & Fast) =================
EMBED_MODEL = None
EMBED_DIM = 384


def get_embed_model():
    """Initializes local embedding model with fast offline snapshot preference."""
    global EMBED_MODEL
    if EMBED_MODEL is None:
        # 1. Prefer cached local SentenceTransformer snapshot to avoid network calls
        try:
            from sentence_transformers import SentenceTransformer
            cache_pattern = os.path.join(
                os.path.expanduser("~"),
                ".cache",
                "huggingface",
                "hub",
                "models--sentence-transformers--all-MiniLM-L6-v2",
                "snapshots",
                "*"
            )
            snapshots = glob.glob(cache_pattern)
            target = snapshots[0] if snapshots else "sentence-transformers/all-MiniLM-L6-v2"
            try:
                EMBED_MODEL = SentenceTransformer(target, local_files_only=True)
            except Exception:
                EMBED_MODEL = SentenceTransformer(target)
        except Exception:
            pass

        # 2. Try fastembed if installed
        if EMBED_MODEL is None:
            try:
                from fastembed import TextEmbedding
                EMBED_MODEL = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
            except Exception:
                pass

        # 3. Fallback
        if EMBED_MODEL is None:
            from sentence_transformers import SentenceTransformer
            EMBED_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    return EMBED_MODEL


def embed(text: str):
    model = get_embed_model()
    if hasattr(model, "embed"):
        return list(model.embed([text]))[0].tolist()
    return model.encode([text], normalize_embeddings=True)[0].tolist()


def embed_batch(texts):
    if not texts:
        return []
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


# ================= DATABASE MODELS =================
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
    document_id = Column(String, index=True)
    title = Column(String)
    text = Column(Text)
    embedding = Column(JSON)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= AUTHENTICATION HELPERS =================
def get_password_hash(p: str) -> str:
    salt = secrets.token_hex(16)
    return salt + ":" + hashlib.sha256((salt + p).encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    if ":" not in hashed:
        return False
    salt, h = hashed.split(":", 1)
    return h == hashlib.sha256((salt + plain).encode()).hexdigest()


def create_access_token(data: dict):
    d = data.copy()
    d.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)


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


# ================= TEXT PROCESSING & SECTION MATCHING =================
ITEM_SPLIT_RE = re.compile(r'(?:(?<=^)|(?<=[\s;]))(\d{1,2})\s*[.)]\s+')
SECTION_HEAD_RE = re.compile(
    r'((?:Functions?|Limitations?|Characteristics?|Advantages?|Disadvantages?|'
    r'Importance|Uses?|Definitions?|Types?|Objectives?|Merits?|Demerits?|'
    r'Features?|Role|Meaning|Scope|Nature|Methods?)\s+of\s+[A-Za-z][A-Za-z ]{0,40}?)\s*:',
    re.I,
)
STOP_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "what", "explain", "list", "give", "write", "all", "about", "with",
}
TOPIC_CONFLICTS = [
    ({"function"}, {"limitation", "demerit", "drawback", "defect", "disadvantage"}),
    ({"advantage", "merit"}, {"disadvantage", "demerit", "limitation"}),
    ({"use"}, {"limitation", "misuse", "abuse"}),
]
LIST_QUESTION_RE = re.compile(
    r"\b(function|functions|characteristic|characteristics|type|types|point|points|"
    r"advantage|advantages|disadvantage|disadvantages|step|steps|feature|features|"
    r"principle|principles|list|enumerate|explain all|all the)\b",
    re.I,
)
NUM_ITEM_RE = re.compile(r"(?:^|[^\d])(?:\(|\[)?(\d{1,2})(?:\)|\.|:)\s")


def _stems(text: str) -> set:
    words = [w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP_WORDS and len(w) > 2]
    stems = set()
    for w in words:
        stems.add(w)
        if w.endswith("ies") and len(w) > 5:
            stems.add(w[:-3] + "y")
        if w.endswith("s") and len(w) > 4:
            stems.add(w[:-1])
        if w.endswith("ing") and len(w) > 5:
            stems.add(w[:-3])
    return stems


def score_section(question: str, heading: str, preview: str = "") -> float:
    q = _stems(question)
    h = _stems(f"{heading} {preview}")
    if not q:
        return 0.0
    score = len(q & h) * 4.0
    qn = (question or "").lower()
    hn = f"{heading} {preview}".lower()
    if qn.strip() in hn:
        score += 12.0
    for pos, neg in TOPIC_CONFLICTS:
        if q & pos:
            if h & pos:
                score += 10.0
            if h & neg:
                score -= 12.0
    return score


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


def _parse_numbered_items(text: str):
    matches = list(ITEM_SPLIT_RE.finditer(text or ""))
    items = []
    for i, m in enumerate(matches):
        n = int(m.group(1))
        if n < 1 or n > 50:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = re.sub(r"\s+", " ", (text[start:end] or "").strip())
        if content:
            items.append((n, content, m.start()))
    return items


def extract_numbered_points(text: str):
    items = _parse_numbered_items(text)
    best, run, expected = [], [], None
    for n, content, _ in items:
        if n == 1:
            if len(run) > len(best):
                best = run
            run = [(n, content)]
            expected = 2
        elif expected is not None and n == expected:
            run.append((n, content))
            expected = n + 1
        else:
            if len(run) > len(best):
                best = run
            run, expected = [], None
            if n == 1:
                run = [(n, content)]
                expected = 2
    if len(run) > len(best):
        best = run
    return best


def extract_heading_sections(text: str):
    """Split notes into heading sections such as Functions of Statistics vs Limitations."""
    text = text or ""
    marks = list(SECTION_HEAD_RE.finditer(text))
    sections = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        sections.append({"heading": re.sub(r"\s+", " ", m.group(1)).strip(" :"), "body": body})
    return sections


def extract_numbered_runs(text: str):
    items = _parse_numbered_items(text)
    runs, run, expected, run_start = [], [], None, None
    for n, content, start in items:
        if n == 1:
            if run:
                runs.append((run_start, run))
            run = [(n, content)]
            expected, run_start = 2, start
        elif expected is not None and n == expected:
            run.append((n, content))
            expected = n + 1
        else:
            if run:
                runs.append((run_start, run))
            run, expected, run_start = [], None, None
            if n == 1:
                run = [(n, content)]
                expected, run_start = 2, start
    if run:
        runs.append((run_start, run))

    out = []
    for start, points in runs:
        window = (text[max(0, start - 140):start] or "").strip()
        hm = re.search(r'([A-Za-z][A-Za-z0-9 ,/&\-]{2,80})\s*:?\s*$', window)
        heading = re.sub(r"\s+", " ", hm.group(1)).strip(" :") if hm else ""
        out.append({"heading": heading, "points": points})
    return out


def format_point_list(points) -> str:
    return "\n\n".join(f"{n}. {t}" for n, t in points)


def format_section_answer(section) -> str:
    heading = section.get("heading") or ""
    points = section.get("points") or []
    if points:
        body = format_point_list(points)
        return f"{heading}:\n\n{body}" if heading else body
    body = re.sub(r"\s+", " ", (section.get("body") or "").strip())
    return f"{heading}: {body}" if heading else body


def select_matching_section(text: str, question: str):
    candidates = []
    for sec in extract_heading_sections(text):
        points = extract_numbered_points(sec["body"])
        preview = (points[0][1] if points else sec["body"])[:180]
        candidates.append({
            "heading": sec["heading"],
            "points": points,
            "body": sec["body"],
            "score": score_section(question, sec["heading"], preview),
        })
    for run in extract_numbered_runs(text):
        preview = run["points"][0][1][:180] if run["points"] else ""
        candidates.append({
            "heading": run["heading"],
            "points": run["points"],
            "body": format_point_list(run["points"]),
            "score": score_section(question, run["heading"], preview),
        })
    if not candidates:
        return None
    best = max(candidates, key=lambda c: (c["score"], len(c["points"])))
    if best["score"] <= 0:
        return None
    return best


def expand_list_context(db: Session, chunks, question: str):
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

            # 1. Exact phrase & heading section boost
            phrase_boost = 0.0
            if q_clean in c_text_lower:
                phrase_boost = 1.6
            sec_sc = score_section(question, c_text_lower[:220])
            if sec_sc > 0:
                phrase_boost += 0.15 * sec_sc

            # 2. Keyword boost
            keyword_matches = sum(1 for w in q_words if w in c_text_lower)
            word_boost = (keyword_matches / max(len(q_words), 1)) * 0.4

            total = sim + phrase_boost + word_boost
            scored.append((total, c))
        except Exception:
            continue

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


def chunk_text(text: str, size: int = 1200, overlap: int = 200):
    text = re.sub(r"\s+", " ", text).strip()
    pieces, start = [], 0
    while start < len(text):
        end = start + size
        pieces.append(text[start:end])
        start = end - overlap
    return [p for p in pieces if len(p.strip()) > 3]


# ================= APP INITIALIZATION =================
app = FastAPI(
    title="DocuAI - Intelligent Document Intelligence & RAG System",
    description="Local-first Document QA, Semantic Search & Hybrid Retrieval RAG System with Groq & FastEmbed",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Locate and mount frontend folder
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(CURRENT_DIR), "frontend")
if not os.path.exists(FRONTEND_DIR):
    FRONTEND_DIR = os.path.join(CURRENT_DIR, "frontend")

if os.path.exists(FRONTEND_DIR):
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


# ================= ROUTES =================
@app.get("/")
def root():
    """Serves the DocuAI UI if available, else JSON API greeting."""
    if os.path.exists(FRONTEND_DIR):
        index_file = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
    return {
        "message": "DocuAI RAG System is running!",
        "docs": "/docs",
        "api": "/api",
        "health": "/health"
    }


@app.get("/api")
def api_status():
    return {
        "system": "DocuAI RAG System",
        "version": "2.0.0",
        "status": "ready",
        "docs": "/docs"
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/auth/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    u = User(email=user.email, hashed_password=get_password_hash(user.password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"message": "User registered successfully", "email": user.email}


@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    # Seamless auto-registration on first login for smooth local workflow
    if not user:
        user = User(email=form_data.username, hashed_password=get_password_hash(form_data.password))
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect password for this account")

    return {
        "access_token": create_access_token({"sub": user.email}),
        "token_type": "bearer",
        "email": user.email
    }


@app.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text content is required")

    doc = db.query(Document).filter(Document.id == req.document_id).first()
    if not doc:
        db.add(Document(id=req.document_id, title=req.title, owner_id=1))
        db.commit()

    pieces = chunk_text(req.text)
    if not pieces:
        return {"status": "empty", "chunks": 0}

    db.query(Chunk).filter(Chunk.document_id == req.document_id).delete()
    vecs = embed_batch(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(
            id=f"{req.document_id}_{i}",
            document_id=req.document_id,
            title=req.title,
            text=piece,
            embedding=vec
        ))
    db.commit()
    return {"status": "ingested", "chunks": len(pieces), "document_id": req.document_id}


async def _handle_upload(file: UploadFile, db: Session):
    filename = file.filename or "upload"
    fl = filename.lower()
    content_bytes = await file.read()
    content = ""

    try:
        if fl.endswith(".txt"):
            content = content_bytes.decode("utf-8", errors="ignore")
        elif fl.endswith(".pdf"):
            if PyPDF2 is not None:
                reader = PyPDF2.PdfReader(io.BytesIO(content_bytes))
                for page in reader.pages:
                    content += (page.extract_text() or "") + "\n"
            elif pypdf is not None:
                reader = pypdf.PdfReader(io.BytesIO(content_bytes))
                for page in reader.pages:
                    content += (page.extract_text() or "") + "\n"
            else:
                raise HTTPException(500, "PDF reader library (PyPDF2/pypdf) is not installed")
        elif fl.endswith(".docx"):
            if docx is None:
                raise HTTPException(500, "DOCX reader library (python-docx) is not installed")
            d = docx.Document(io.BytesIO(content_bytes))
            for para in d.paragraphs:
                content += para.text + "\n"
        else:
            raise HTTPException(400, "Unsupported format. Allowed: .txt, .pdf, .docx")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error processing file: {e}")

    if not content.strip():
        raise HTTPException(400, "No readable text extracted from document")

    doc_id = re.sub(r"[^a-z0-9]+", "_", fl).strip("_")
    if not db.query(Document).filter(Document.id == doc_id).first():
        db.add(Document(id=doc_id, title=filename, owner_id=1))
        db.commit()

    pieces = chunk_text(content)
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    vecs = embed_batch(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(
            id=f"{doc_id}_{i}",
            document_id=doc_id,
            title=filename,
            text=piece,
            embedding=vec
        ))
    db.commit()
    return {"message": f"'{filename}' processed successfully", "document_id": doc_id, "chunks": len(pieces)}


@app.post("/upload/upload")
async def upload_file_legacy(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, db)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, db)


@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    chunks = semantic_search(db, req.question, top_k=20)
    if not chunks:
        return {"answer": "No relevant documents found. Please ingest or upload documents first."}

    doc_ids = []
    seen = set()
    for c in chunks:
        if c.document_id and c.document_id not in seen:
            seen.add(c.document_id)
            doc_ids.append(c.document_id)

    best_section = None
    used_chunks = []
    for doc_id in doc_ids[:4]:
        doc_chunks = load_document_chunks(db, doc_id)
        stitched_doc = stitch_chunk_texts(doc_chunks)
        section = select_matching_section(stitched_doc, req.question)
        if section and (best_section is None or section["score"] > best_section["score"]):
            best_section = section
            used_chunks = doc_chunks

    if used_chunks:
        unique_sources = list(dict.fromkeys([(c.title or c.document_id) for c in used_chunks]))
    else:
        unique_sources = list(dict.fromkeys([(c.title or c.document_id) for c in chunks]))
    source_line = f"\n\n📚 Sources: {', '.join(unique_sources)}"

    is_list_q = bool(LIST_QUESTION_RE.search(req.question or ""))
    if best_section and (is_list_q or (best_section.get("points") and best_section["score"] >= 8)):
        answer = format_section_answer(best_section)
        if answer.strip():
            return {"answer": answer + source_line}

    all_chunks_to_use = used_chunks if used_chunks else expand_list_context(db, chunks, req.question)
    context = stitch_chunk_texts(all_chunks_to_use) if all_chunks_to_use else "\n\n".join(
        f"[Source {i} - {c.title or c.document_id}]\n{c.text}"
        for i, c in enumerate(chunks, 1)
    )
    unique_sources = list(dict.fromkeys([(c.title or c.document_id) for c in all_chunks_to_use or chunks]))
    source_line = f"\n\n📚 Sources: {', '.join(unique_sources)}"

    groq_client = get_groq_client()
    if groq_client:
        system = (
            "You are DocuAI, an intelligent document QA assistant. "
            "Answer using ONLY the provided document context. Be comprehensive, precise, and faithful. "
            "If the question asks for functions or specific points, include all points clearly without omitting later ones. "
            "If the context does not contain the answer, state that clearly."
        )
        user = f"Context:\n{context}\n\nQuestion: {req.question}\n\nComplete answer from context:"
        models_to_try = [
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
        ]
        for m in models_to_try:
            try:
                resp = groq_client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )
                answer = (resp.choices[0].message.content or "").strip()
                if answer:
                    return {"answer": answer + source_line}
            except Exception:
                continue

    # Fallback when Groq API key is not configured or offline
    return {"answer": f"Relevant document excerpts:\n\n{context[:3500]}{source_line}"}


@app.get("/documents")
def get_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).all()
    out = []
    for d in docs:
        chunk_count = db.query(Chunk).filter(Chunk.document_id == d.id).count()
        out.append({
            "id": d.id,
            "title": d.title,
            "chunks": chunk_count,
            "created_at": d.created_at.isoformat() if d.created_at else None
        })
    return out


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    return {"message": f"Deleted document '{doc_id}'"}


@app.get("/chunks")
def get_chunks(db: Session = Depends(get_db)):
    return [{"id": c.id, "document_id": c.document_id, "title": c.title} for c in db.query(Chunk).all()]


@app.get("/entities")
def get_entities():
    return []


@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    top = semantic_search(db, q, top_k=5)
    results = [
        {
            "chunk_id": c.id,
            "document_id": c.document_id,
            "title": c.title,
            "text": c.text[:280] + ("..." if len(c.text or "") > 280 else "")
        }
        for c in top
    ]
    return {"results": results, "total": len(results)}
