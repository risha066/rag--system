from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="Knowledge Graph RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# SIMPLE VERSION - No database required
@app.get("/")
def root():
    return {"message": "RAG System API is running!"}

@app.get("/health")
def health():
    return {"status": "ok"}

# Dummy routes for testing
@app.get("/documents")
def get_documents():
    return []

@app.get("/chunks")
def get_chunks():
    return []

@app.get("/entities")
def get_entities():
    return []

@app.post("/ingest")
def ingest():
    return {"status": "ok", "message": "Ingest endpoint working"}

@app.post("/query")
def query():
    return {"answer": "Query endpoint working. Connect database for full functionality."}
