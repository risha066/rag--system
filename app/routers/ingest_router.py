from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import User
from ..schemas import IngestRequest
from ..ingestion import ingest_document, hash_document

router = APIRouter(prefix="/ingest", tags=["ingest"])

@router.post("")
def ingest(payload: IngestRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc_id = payload.document_id or hash_document(payload.text)
    return ingest_document(db, doc_id, payload.title, payload.text, user.id)
