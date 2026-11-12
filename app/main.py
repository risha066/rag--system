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

CATEGORY_KEYWORDS = {
    "function": {"function", "functions", "role", "roles", "purpose", "purposes", "objective", "objectives", "task", "tasks", "uses"},
    "limitation": {"limitation", "limitations", "drawback", "drawbacks", "demerit", "demerits", "defect", "defects", "disadvantage", "disadvantages", "weakness", "weaknesses"},
    "advantage": {"advantage", "advantages", "merit", "merits", "benefit", "benefits"},
    "characteristic": {"characteristic", "characteristics", "feature", "features", "nature", "property", "properties"},
    "definition": {"definition", "definitions", "meaning", "define", "what is", "concept", "origin", "introduction"}
}

LIST_QUESTION_RE = re.compile(
    r"\b(function|functions|characteristic|characteristics|type|types|point|points|"
    r"advantage|advantages|disadvantage|disadvantages|step|steps|feature|features|"
    r"principle|principles|merit|merits|demerit|demerits|limitation|limitations|list|enumerate|explain all|all the)\b",
    re.I,
)

DEFINITION_Q_RE = re.compile(
    r"\b(what is|what are|define|definition|meaning of|what do you mean by|explain|describe)\b",
    re.I
)

HEADING_WORDS = (
    "Functions?|Limitations?|Characteristics?|Advantages?|Disadvantages?|"
    "Importance|Uses?|Definitions?|Types?|Objectives?|Merits?|Demerits?|"
    "Features?|Role|Meaning|Scope|Nature|Methods?|Introduction"
)

HEADING_RE = re.compile(
    rf"((?:{HEADING_WORDS})(?:\s+(?:of|and|in|to)\s+[A-Za-z0-9 ]{{1,35}})?)\s*(?::|(?=\s+\d{{1,2}}\.\s+[A-Za-z]))",
    re.I
)

STOP_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "what", "explain", "list", "give", "write", "all", "about", "with", "tell", "me"
}
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
    q_lower = (question or "").lower()
    h_lower = (heading or "").lower()
    p_lower = (preview or "").lower()

    q_stems = _stems(question)
    h_stems = _stems(heading)
    p_stems = _stems(preview[:220])

    q_categories = set()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in q_lower for kw in kws):
            q_categories.add(cat)

    is_def_q = bool(DEFINITION_Q_RE.search(q_lower))
    if is_def_q and not (q_categories - {"definition"}):
        q_categories.add("definition")

    h_categories = set()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in h_lower for kw in kws):
            h_categories.add(cat)

    cat_all_words = set().union(*CATEGORY_KEYWORDS.values())
    topic_q = {w for w in q_stems if w not in cat_all_words}
    topic_h = {w for w in h_stems if w not in cat_all_words}

    # If question is about a specific topic (e.g. statistics), heading or preview must touch it
    if topic_q and not (topic_q & (topic_h | p_stems)):
        return -50.0

    # Cross-category rejection (e.g. asking for definition should not match functions or limitations)
    if h_categories and q_categories:
        if not (h_categories & q_categories):
            return -30.0

    score = 0.0
    if h_categories & q_categories:
        score += 30.0

    if topic_q & topic_h:
        score += 15.0

    if q_lower.strip() in f"{h_lower} {p_lower}":
        score += 15.0

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
    if not items:
        return []
    # Deduplicate repeated items (e.g. repeated numbers or OCR duplicated points)
    points = []
    seen = set()
    for num, content, _ in items:
        if num in seen:
            continue
        seen.add(num)
        points.append((num, content))
    return points


def extract_heading_sections(text: str):
    """Split notes into heading sections accurately whether headings end in colons or numbers."""
    text = text or ""
    marks = list(HEADING_RE.finditer(text))
    sections = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        h = re.sub(r"\s+", " ", m.group(1)).strip(" :;.")
        body = text[start:end].strip()
        sections.append({"heading": h, "body": body})
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


# ================= QUERY EXPANSION & ACRONYMS =================
ACRONYM_MAP = {
    "ai": "artificial intelligence",
    "ml": "machine learning",
    "nlp": "natural language processing",
    "dl": "deep learning",
    "stats": "statistics"
}

def expand_query(q: str) -> str:
    words = re.findall(r'\b\w+\b', (q or '').lower())
    expanded = list(words)
    for w in words:
        if w in ACRONYM_MAP:
            expanded.append(ACRONYM_MAP[w])
    return " ".join(expanded)


def is_table_of_contents_or_meta(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if ("chapter no." in lower and "page no." in lower) or "table of contents" in lower:
        return True
    section_markers = len(re.findall(r'\b\d+\.\d+\s+[A-Z]', text))
    if section_markers >= 4:
        return True
    return False


def get_subject_terms(question: str):
    words = [w.lower() for w in re.findall(r'\b\w+\b', question) if w.lower() not in STOP_WORDS and w.lower() not in ['what', 'is', 'are', 'the', 'definition', 'meaning', 'defined', 'explain', 'describe']]
    terms = list(words)
    for w in words:
        if w in ACRONYM_MAP:
            terms.append(ACRONYM_MAP[w])
    joined = ' '.join(words)
    if joined and joined not in terms:
        terms.append(joined)
    return [t for t in terms if len(t) >= 2]


def load_full_passage(db: Session, chunk, max_chunks=3):
    chunks = [chunk]
    if '_' in (chunk.id or ''):
        prefix, idx_str = chunk.id.rsplit('_', 1)
        try:
            curr_idx = int(idx_str)
            for step in range(1, max_chunks):
                next_id = f"{prefix}_{curr_idx + step}"
                next_chunk = db.query(Chunk).filter(Chunk.id == next_id).first()
                if next_chunk:
                    chunks.append(next_chunk)
                else:
                    break
        except Exception:
            pass
    return stitch_chunk_texts(chunks)


def format_table_and_sections(text: str) -> str:
    # 1. Format Table 1.1 if present
    if "table 1.1" in text.lower():
        text = re.sub(
            r'Table\s+1\.1\s+The following are several definitions of artificial intelligence[^\.]*\.',
            r'\n\n**Table 1.1: Definitions of Artificial Intelligence (Four Categories)**\n',
            text,
            flags=re.I
        )
        text = re.sub(r'Taking People into Account\s*["“]?', '\n- **Taking People into Account (Thinking Humanly):**\n  - "', text)
        text = re.sub(r'Thinking Rationally\s*["“]?', '\n\n- **Thinking Rationally:**\n  - "', text)
        text = re.sub(r'Acting Rationally\s*["“]?', '\n\n- **Acting Rationally:**\n  - "', text)
        text = re.sub(r'Acting Humanly\s*["“]?', '\n\n- **Acting Humanly:**\n  - "', text)
        text = re.sub(r'Source:\s*In the year 2010', '\n\n*Source: In the year 2010', text)

    # 2. Format Statistics Definition if present
    if "unit 1" in text.lower() or "in the plural sense:" in text.lower() or "in the singular sense:" in text.lower():
        text = re.sub(r'UNIT\s+1\s+Introduction To Statistics\s+Meaning:\s*', r'**Unit 1: Introduction to Statistics**\n\n**Meaning:**\n', text, flags=re.I)
        text = re.sub(r'\bDefinition:\s*', r'\n\n**Definition:**\n', text)
        text = re.sub(r'A\.\s*In the Plural Sense:\s*', r'\n\n**A. In the Plural Sense:**\n', text)
        text = re.sub(r'B\.\s*In the Singular Sense:\s*', r'\n\n**B. In the Singular Sense:**\n', text)

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_definitional_text(text: str, terms: list) -> str:
    cleaned = re.sub(r'Title:\s*[\w\.\-]+\s*ID:\s*[\w\.\-]+', '', text or '')
    cleaned = re.sub(r'\d+\s*\|\s*P\s*a\s*g\s*e', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Try finding subject heading start
    found_start = None
    for t in terms:
        pattern = r'(\b(?:\d+\.\d+\s+)?(?:WHAT IS|DEFINING|MEANING OF|DEFINITION OF|INTRODUCTION TO)\s+' + re.escape(t) + r'[A-Za-z0-9 ,/:\-]*\b.*)'
        m = re.search(pattern, cleaned, re.I)
        if m:
            found_start = m.start(1)
            break

    if found_start is not None:
        cleaned = cleaned[found_start:]
    else:
        for t in terms:
            pattern = r'(?:^|[.!?]\s+)([A-Z][^.!?]*\b' + re.escape(t) + r'\b[^.!?]*[.!?].*)'
            m = re.search(pattern, cleaned)
            if m:
                cleaned = m.group(1).strip()
                break

    # Look for clean boundary stop before next sub-topic (e.g. 1.1.1 or Stages of Investigations)
    m_sub = re.search(r'\b(?:1\.1\.1\b|Stages of Investigations:|\bChapter\s+\d+\b)', cleaned)
    if m_sub and m_sub.start() > 200:
        cleaned = cleaned[:m_sub.start()].strip()
    elif len(cleaned) > 2200:
        cut = cleaned[:2200]
        last_p = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
        if last_p > 300:
            cleaned = cut[:last_p + 1]

    return format_table_and_sections(cleaned)


def extract_definition_answer(db: Session, primary_chunks, question: str) -> str:
    terms = get_subject_terms(question)
    scored = []
    for c in primary_chunks:
        if is_table_of_contents_or_meta(c.text):
            continue
        txt = c.text or ''
        sc = 0
        for t in terms:
            if re.search(r'\b(?:what is|defining|meaning of|definition of|introduction to)\s+' + re.escape(t) + r'\b', txt, re.I):
                sc += 35
            if re.search(r'\b' + re.escape(t) + r'\s+(?:is defined as|refers to|means|is a technology|is a branch|is considered|encompasses)\b', txt, re.I):
                sc += 25
            if re.search(r'\b' + re.escape(t) + r'\s*\([^)]*\)\s+is\b', txt, re.I):
                sc += 25
            if re.search(r'\b(?:meaning|definition)\s*:\s*["\']?' + re.escape(t) + r'\b', txt, re.I):
                sc += 25
            if re.search(r'\b' + re.escape(t) + r'\b', txt, re.I):
                sc += 2
        if sc > 0:
            scored.append((sc, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        scored = [(0, c) for c in primary_chunks if not is_table_of_contents_or_meta(c.text)]

    best_chunk = scored[0][1]
    full_passage = load_full_passage(db, best_chunk, max_chunks=3)
    answer = clean_definitional_text(full_passage, terms)
    return answer


def semantic_search(db: Session, question: str, top_k: int = 15):
    chunks = db.query(Chunk).filter(Chunk.embedding.isnot(None)).all()
    if not chunks:
        return []

    expanded_q = expand_query(question)
    q_vec = embed(expanded_q)
    q_clean = question.lower().strip()
    q_words = [w.lower() for w in re.findall(r"\b\w+\b", expanded_q) if len(w) >= 2 and w not in STOP_WORDS]

    scored = []
    for c in chunks:
        if is_table_of_contents_or_meta(c.text):
            continue
        try:
            sim = cosine(q_vec, c.embedding)
            c_text_lower = (c.text or "").lower()

            # 1. Exact phrase & acronym boost
            phrase_boost = 0.0
            if q_clean in c_text_lower:
                phrase_boost += 1.5
            if "artificial intelligence" in c_text_lower and "ai" in q_clean:
                phrase_boost += 1.0

            sec_sc = score_section(question, c_text_lower[:220], c_text_lower[:400])
            if sec_sc > 0:
                phrase_boost += 0.15 * sec_sc
            elif sec_sc < -20:
                phrase_boost -= 0.5

            # Definitional boost for what is / definition questions
            if bool(DEFINITION_Q_RE.search(question)):
                if any(k in c_text_lower for k in ["meaning:", "definition:", "is defined as", "refers to", "derived from", "concept of", "defining"]):
                    phrase_boost += 0.8

            # 2. Keyword boost using word boundaries
            kw_hits = sum(1 for w in q_words if re.search(r'\b' + re.escape(w) + r'\b', c_text_lower))
            word_boost = (kw_hits / max(len(q_words), 1)) * 0.4

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

    # 1. Identify the primary matching document from top search result
    primary_doc_id = chunks[0].document_id
    primary_chunks = [c for c in chunks if c.document_id == primary_doc_id]
    doc_title = primary_chunks[0].title or primary_doc_id
    source_line = f"\n\n📚 Sources: {doc_title}"

    q_lower = (req.question or "").lower()
    is_list_q = bool(LIST_QUESTION_RE.search(q_lower))
    is_def_q = bool(DEFINITION_Q_RE.search(q_lower))

    # 2. Check for high-confidence structured list / heading section strictly within target document
    best_section = None
    if is_list_q:
        doc_chunks = load_document_chunks(db, primary_doc_id)
        stitched_doc = stitch_chunk_texts(doc_chunks)
        best_section = select_matching_section(stitched_doc, req.question)
        if best_section and best_section.get("points") and best_section["score"] >= 20:
            answer = format_section_answer(best_section)
            if answer.strip():
                return {"answer": answer + source_line}

    # 3. Check for high-confidence definitional answer strictly within target document
    if is_def_q and not is_list_q:
        doc_chunks = load_document_chunks(db, primary_doc_id)
        def_answer = extract_definition_answer(db, doc_chunks, req.question)
        if def_answer.strip():
            return {"answer": def_answer + source_line}

    # 4. Compile context from the primary document
    primary_context = "\n\n".join(c.text for c in primary_chunks[:6])

    groq_client = get_groq_client()
    if groq_client:
        system = (
            "You are DocuAI, an intelligent document QA assistant. "
            "Answer using ONLY the provided document context. Be comprehensive, precise, and faithful. "
            "If the question asks for functions or specific points, include all points clearly without omitting later ones. "
            "If the question asks for a definition or meaning, explain the concept accurately without diverting into unrelated sections. "
            "If the context does not contain the answer, state that clearly."
        )
        user = f"Context from '{doc_title}':\n{primary_context}\n\nQuestion: {req.question}\n\nComplete and accurate answer:"
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

    # 5. Fallback when Groq API key is not configured or offline:
    # Return cleaned passage excerpts from the primary document
    doc_passages = [clean_definitional_text(load_full_passage(db, c, max_chars=1200), get_subject_terms(req.question)) for c in primary_chunks[:3]]
    joined_passages = "\n\n".join(p for p in doc_passages if len(p) > 40)
    return {"answer": f"{joined_passages or primary_context[:3500]}{source_line}"}


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
