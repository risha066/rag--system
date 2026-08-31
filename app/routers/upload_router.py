from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
import PyPDF2
import docx
import io
from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.ingestion import ingest_document

router = APIRouter()

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Upload and ingest a PDF, Word, or Text file"""
    
    # Check file type
    filename = file.filename.lower()
    content = ""
    
    try:
        # Read file content
        file_content = await file.read()
        
        if filename.endswith('.txt'):
            content = file_content.decode('utf-8')
            
        elif filename.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
            for page in pdf_reader.pages:
                content += page.extract_text() + "\n"
                
        elif filename.endswith('.docx'):
            doc = docx.Document(io.BytesIO(file_content))
            for para in doc.paragraphs:
                content += para.text + "\n"
        else:
            raise HTTPException(
                status_code=400, 
                detail="Unsupported file type. Please upload .txt, .pdf, or .docx"
            )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")
    
    if not content.strip():
        raise HTTPException(status_code=400, detail="No text content found in file")
    
    # Ingest the document
    doc_id = file.filename.replace(' ', '_').replace('.', '_')
    title = file.filename.replace(' ', '_')
    
    try:
        result = ingest_document(db, doc_id, title, content, user.id)
        return {
            "status": "success",
            "document_id": doc_id,
            "title": title,
            "chunks": result.get("chunks", 0),
            "message": f"File '{file.filename}' ingested successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")