import os
from app.main import (
    SessionLocal, root, health, api_status, register, login,
    ingest, query, get_documents, delete_document, search,
    UserCreate, IngestRequest, QueryRequest
)

class MockFormData:
    def __init__(self, username, password):
        self.username = username
        self.password = password

def run_tests():
    db = SessionLocal()
    print("==========================================")
    print("🚀 RUNNING DOCUAI TEST SUITE")
    print("==========================================")

    # 1. Health & Root
    print("\n[1/7] Testing health & metadata endpoints...")
    h = health()
    assert h["status"] == "ok"
    print("  ✓ Health check:", h["status"])

    a = api_status()
    assert a["system"] == "DocuAI RAG System"
    print("  ✓ System Name:", a["system"])
    print("  ✓ System Version:", a["version"])

    # 2. Authentication
    print("\n[2/7] Testing authentication & user management...")
    auth_res = login(MockFormData("newuser@email.com", "securepass123"), db)
    assert "access_token" in auth_res
    print("  ✓ Demo user login & auto-provisioning OK")

    # 3. Document Ingestion
    print("\n[3/7] Testing document ingestion & chunking...")
    sample_notes = """
Key Objectives of DocuAI:
1. Preserve complete numbered lists and headings across chunk boundaries.
2. Provide local, private, CPU-friendly embeddings with all-MiniLM-L6-v2.
3. Deliver fast inference using Groq LLaMA models.
4. Offer a clean, zero-friction web dashboard.
"""
    ing_res = ingest(IngestRequest(
        document_id="docuai_spec_test",
        title="DocuAI Specifications",
        text=sample_notes
    ), db)
    assert ing_res["status"] == "ingested"
    assert ing_res["chunks"] >= 1
    print(f"  ✓ Ingested document '{ing_res['document_id']}' with {ing_res['chunks']} chunk(s)")

    # 4. Document Library
    print("\n[4/7] Testing document library query...")
    docs = get_documents(db)
    found = any(d["id"] == "docuai_spec_test" for d in docs)
    assert found, "Document not found in database library"
    print(f"  ✓ Found {len(docs)} document(s) in active index")

    # 5. Question Answering / Retrieval
    print("\n[5/7] Testing QA retrieval with section matching...")
    q_res = query(QueryRequest(question="What are the key objectives of DocuAI?"), db)
    answer = q_res.get("answer", "")
    assert "Preserve complete numbered lists" in answer or "DocuAI Specifications" in answer
    print("  ✓ Answer returned successfully:")
    print("    --------------------------------------")
    for line in answer.strip().split("\n"):
        print("   ", line)
    print("    --------------------------------------")

    # 6. Semantic Search
    print("\n[6/7] Testing vector semantic similarity search...")
    s_res = search("CPU-friendly embeddings", db)
    assert s_res["total"] > 0
    print(f"  ✓ Semantic search found {s_res['total']} matching chunk(s)")

    # 7. Document Deletion & Cleanup
    print("\n[7/7] Testing document deletion...")
    del_res = delete_document("docuai_spec_test", db)
    print("  ✓", del_res["message"])

    db.close()
    print("\n==========================================")
    print("🎉 ALL DOCUAI RAG SYSTEM TESTS PASSED!")
    print("==========================================")

if __name__ == "__main__":
    run_tests()
