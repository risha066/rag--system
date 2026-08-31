from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, JSON, func
from pgvector.sqlalchemy import Vector
from .database import Base
from .config import settings

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True)      # document hash, used for caching
    title = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, server_default=func.now())

class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(String, primary_key=True)       # e.g. f"{document_id}::{index}"
    document_id = Column(String, ForeignKey("documents.id"))
    section_path = Column(String)
    text = Column(Text, nullable=False)
    entity_ids = Column(JSON, default=list)
    embedding = Column(Vector(settings.embedding_dim))
    created_at = Column(DateTime, server_default=func.now())

class QueryLog(Base):
    __tablename__ = "query_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    question = Column(Text)
    route = Column(String)          # GRAPH | VECTOR | HYBRID
    outcome = Column(Text)
    latency_ms = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())
