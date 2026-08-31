from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.retrieval import answer_question

router = APIRouter(prefix="/query", tags=["query"])

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    answer: str
    route: str = ""
    citations: list = []
    latency_ms: int = 0

@router.post("", response_model=QueryResponse)
def query(payload: QueryRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        # Call with correct order: db, user_id, question
        result = answer_question(db, user.id, payload.question)
        return QueryResponse(
            answer=result.get("answer", "No answer found"),
            route=result.get("route", "UNKNOWN"),
            citations=result.get("citations", []),
            latency_ms=result.get("latency_ms", 0)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))