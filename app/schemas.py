from pydantic import BaseModel, EmailStr
from typing import List, Optional

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class IngestRequest(BaseModel):
    document_id: str
    title: str
    text: str

class QueryRequest(BaseModel):
    question: str

class Citation(BaseModel):
    chunk_id: str
    text: str

class QueryResponse(BaseModel):
    answer: str
    route: str
    citations: List[Citation]
    latency_ms: int
