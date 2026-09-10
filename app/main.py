from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import os

# ================= CONFIG =================
SECRET_KEY = os.getenv("JWT_SECRET", "your-secret-key-change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ================= DATABASE =================
DATABASE_URL = "sqlite:///./rag.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ================= PASSWORD =================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)
def get_password_hash(p): return pwd_context.hash(p)

# ================= JWT =================
def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

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
    text = Column(Text)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

# ================= CORS (THE FIX) =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= ROUTES =================
@app.get("/")
def root():
    return {"message": "RAG System API is running!"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/auth/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = User(email=user.email, hashed_password=get_password_hash(user.password))
    db.add(new_user)
    db.commit()
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
        db.add(Document(id=req.document_id, title=req.title, owner_id=1))
        db.commit()
    db.add(Chunk(id=f"{req.document_id}_0", document_id=req.document_id, text=req.text))
    db.commit()
    return {"status": "ingested", "chunks": 1, "document_id": req.document_id}

@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    chunks = db.query(Chunk).all()
    if not chunks:
        return {"answer": "No documents found. Please ingest some documents first."}
    context = "\n".join([c.text for c in chunks[:3]])
    return {"answer": f"Based on your documents:\n\n{context[:500]}..."}

@app.get("/documents")
def get_documents(db: Session = Depends(get_db)):
    return [{"id": d.id, "title": d.title} for d in db.query(Document).all()]

@app.get("/chunks")
def get_chunks(db: Session = Depends(get_db)):
    return [{"id": c.id, "document_id": c.document_id} for c in db.query(Chunk).all()]

@app.get("/entities")
def get_entities():
    return []

@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    results = []
    for c in db.query(Chunk).all():
        if q.lower() in c.text.lower():
            results.append({"chunk_id": c.id, "document_id": c.document_id, "text": c.text[:200]})
    return {"results": results[:10], "total": len(results)}
