# 📚 MyAI Assistant (Document Intelligence RAG)

A full-stack Retrieval-Augmented Generation (RAG) system that allows users to upload PDF documents and ask natural language questions, receiving grounded answers with citations.

> **Note on Deployment:** The live servers (Vercel/Render) have been taken down to preserve resources and data privacy. The entire application is fully Dockerized and can be run locally in under 2 minutes.

## 🚀 Features
* **Document Ingestion:** Upload PDFs and automatically chunk/embed text.
* **Hybrid Retrieval:** Combines Neo4j (Knowledge Graph) and PostgreSQL/pgvector (Vector Search) for highly accurate context retrieval.
* **Local Embeddings:** Uses `fastembed` so no OpenAI keys are needed (100% free).
* **Fast Inference:** Utilizes Groq API (Llama 3) for blazing-fast answer generation.
* **Citation Validation:** The LLM must cite its sources, reducing hallucinations.

## 🛠️ Tech Stack
* **Frontend:** HTML, CSS, JavaScript
* **Backend:** FastAPI (Python 3.12)
* **Databases:** Neo4j (Graph), PostgreSQL with pgvector (Vector)
* **AI/LLM:** Groq API (Llama 3.3), Fastembed (MiniLM)
* **Infrastructure:** Docker, Docker Compose

## 🏃‍♂️ How to Run Locally

### 1. Clone the repository
```bash
git clone https://github.com/risha066/rag-system.git
cd rag-system
