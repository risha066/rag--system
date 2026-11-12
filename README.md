# 📚 MyAI Assistant (Document Intelligence RAG)

A full-stack Retrieval-Augmented Generation (RAG) system that allows users to upload PDF/DOCX/TXT documents and ask natural language questions, receiving grounded answers with exact source citations.

> **Note on Deployment:** The live cloud servers have been taken down to preserve resources. The entire application is fully ready to run locally in under 2 minutes (see instructions below).

## 🚀 Features
* **Document Ingestion:** Upload PDFs, DOCX, or TXT files and paste raw text. The backend automatically chunks the text and generates embeddings.
* **Semantic Search:** Uses `sentence-transformers` (MiniLM) to convert text into 384-dimensional vectors for highly accurate context retrieval.
* **Fast Inference:** Utilizes the Groq API (`openai/gpt-oss-20b`) for blazing-fast, precise answer generation.
* **Accurate Citations:** The LLM is strictly prompted to answer *only* from the retrieved context and lists the exact source document at the end.
* **JWT Authentication:** Secure user registration and login so each user only sees their own documents.

## 🛠️ Tech Stack
* **Frontend:** HTML, CSS, Vanilla JavaScript
* **Backend:** FastAPI (Python 3.11), Uvicorn
* **Database:** SQLite + SQLAlchemy (Lightweight, zero-config storage for embeddings and users)
* **AI/LLM:** Groq API (`openai/gpt-oss-20b`), HuggingFace `sentence-transformers`

## 🏃‍♂️ How to Run Locally

### 1. Clone the repository
```bash
git clone https://github.com/risha066/rag--system.git
cd rag--system
