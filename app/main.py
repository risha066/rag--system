from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import os

# ==================== CONFIG ====================
SECRET_KEY = os.getenv("JWT_SECRET", "your-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# ==================== PASSWORD HASHING ====================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# ==================== JWT ====================
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ==================== MODELS ====================
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime as dt

# Use SQLite for simplicity (works on Railway)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rag.db")
if DATABASE_URL.startswith("postgresql"):
    import psycopg2
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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
    created_at = Column(DateTime, default=dt.datetime.utcnow)

class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(String, primary_key=True, index=True)
    document_id = Column(String)
    text = Column(Text)
    embedding = Column(JSON, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==================== SCHEMAS ====================
class UserCreate(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class IngestRequest(BaseModel):
    document_id: str
    title: str
    text: str

class QueryRequest(BaseModel):
    question: str

# ==================== APP ====================
app = FastAPI(title="Knowledge Graph RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== ROUTES ====================
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
    hashed = get_password_hash(user.password)
    new_user = User(email=user.email, hashed_password=hashed)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created", "email": user.email}

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    # Simple ingest - just store the text
    doc = db.query(Document).filter(Document.id == req.document_id).first()
    if not doc:
        doc = Document(id=req.document_id, title=req.title, owner_id=1)
        db.add(doc)
        db.commit()
    
    chunk = Chunk(id=f"{req.document_id}_0", document_id=req.document_id, text=req.text)
    db.add(chunk)
    db.commit()
    return {"status": "ingested", "chunks": 1, "document_id": req.document_id}

@app.post("/query")
def query(req: QueryRequest, db: Session = Depends(get_db)):
    # Simple query - just return a response
    chunks = db.query(Chunk).all()
    context = "\n".join([c.text for c in chunks[:3]])
    return {"answer": f"Based on your documents: {context[:200]}..." if context else "No documents found. Please ingest some documents first."}

@app.get("/documents")
def get_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).all()
    return [{"id": d.id, "title": d.title} for d in docs]

@app.get("/chunks")
def get_chunks(db: Session = Depends(get_db)):
    chunks = db.query(Chunk).all()
    return [{"id": c.id, "document_id": c.document_id} for c in chunks]

@app.get("/entities")
def get_entities():
    return []
