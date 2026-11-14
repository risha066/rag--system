# 📄 DocuAI — Intelligent Document Intelligence & RAG System

> **A fast, zero-friction Retrieval-Augmented Generation (RAG) platform with full document comprehension, local embeddings, and instant source-grounded answers.**

> ℹ️ **Note on Deployment:** Cloud server deployments have been taken down to preserve resources. The entire application is 100% self-contained and ready to run locally in under 2 minutes (or redeploy anywhere).

---

## 💡 Why DocuAI?

Most standard RAG setups look great in simple demos, but quickly stumble on real-world documents:
- **Missing points:** Ask a standard RAG bot for *"all 10 functions of statistics"*, and naive chunking almost always chops the list in half.
- **Heavy setup hurdles:** Many systems force you to configure cloud vector databases, knowledge graphs, or heavy infrastructure before you can even test a single question.
- **High latency & costs:** Calling paid embedding APIs on every document chunk quickly gets expensive and sluggish.

**DocuAI** was built to solve these exact headaches:
1. **Intelligent Section & List Stitching:** Document chunks are stitched across boundaries so enumerated lists, headings, and detailed points are preserved in full.
2. **Local & Free Embeddings:** Runs `all-MiniLM-L6-v2` locally on your CPU (via `sentence-transformers` / `fastembed`) — zero API keys required, zero rate limits, and 100% private.
3. **Lightning-Fast Generation:** Connects directly to Groq's high-speed inference engine running `llama-3.3-70b-versatile` (or `llama-3.1-8b-instant`) for instant, cited answers.
4. **Zero-Config Database:** Runs out-of-the-box on local SQLite (`rag.db`). When you're ready for production, plug in PostgreSQL with a single connection string in `.env`.
5. **Built-in Interactive UI:** Clean web dashboard served directly by the backend — upload documents, monitor index health, run semantic vector searches, and chat in real-time.

---

## 🚀 Quickstart (Under 2 Minutes)

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/risha066/rag--system.git
cd rag--system

# Create and activate virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example file:
```bash
cp .env.example .env
```

Open `.env` and paste your free Groq API key:
```env
GROQ_API_KEY=gsk_your_key_here
```
*(Grab a free key in 30 seconds at [console.groq.com/keys](https://console.groq.com/keys)).*

> **Note:** Even without a Groq key, DocuAI will still start, chunk documents, generate embeddings, and return the most relevant text matches with source tags!

### 3. Launch DocuAI

**Option A (1-Click Persistent Background Runner - Windows):**
- Double-click **`start_docuai.bat`** — Runs a watchdog loop that keeps DocuAI alive continuously and auto-restarts if ever interrupted.
- Double-click **`run_background.vbs`** — Runs DocuAI completely silently in the background with zero visible terminal windows.
- Double-click **`enable_autostart.bat`** — Configures DocuAI to boot automatically on Windows startup.
- Double-click **`stop_docuai.bat`** — Stops the server anytime.

**Option B (Terminal):**
```bash
uvicorn app.main:app --reload --port 8000
```

Open your browser to:
👉 **[http://localhost:8000](http://localhost:8000)**

- **Default Test Login:** `newuser@email.com` / `securepass123`
- Or click **Register** to create your own account with 1 click.

---

## 🖥️ What You Can Do in the Web Dashboard

| Tab | What it does |
| :--- | :--- |
| **📊 Dashboard** | Live overview of your indexed knowledge base: document count, total vector chunks, and system health. |
| **📤 Ingest** | Upload `.pdf`, `.docx`, or `.txt` files directly, or paste text/notes. DocuAI automatically normalizes, chunks, and indexes them. |
| **💬 Chat** | Ask natural-language questions. Answers cite the exact source documents with badges (`📚 Sources: ...`). |
| **📁 Documents** | View your uploaded documents and chunk allocations, or delete outdated files with one click. |
| **🔍 Search** | Run real-time semantic vector queries across stored embeddings with instant similarity matching. |

---

## 🛠️ Architecture at a Glance

```
  [User / Browser]
        │
        ▼
   FastAPI Web App (app/main.py)  ◄── Serves Web Dashboard & REST API
        │
   ┌────┴───────────────────────────┐
   │                                │
   ▼                                ▼
[Document Ingestion]         [Query & Retrieval]
 ├── File Parser (.pdf/.docx/.txt)  ├── Local MiniLM-L6-v2 Query Embedding
 ├── Context-Aware Chunking        ├── Cosine Similarity + Heading Section Boost
 └── Local MiniLM Embedding        ├── Numbered List & Heading Re-stitching
        │                                   │
        ▼                                   ▼
 [Storage Engine]                  [Groq LLM Engine]
  SQLite (rag.db) or PostgreSQL     LLaMA-3.3-70B / LLaMA-3.1-8B
  (Documents, Chunks, Embeddings)   (Generates cited, grounded answer)
```

---

## 📡 REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the interactive DocuAI Web UI |
| `GET` | `/api` | API status and metadata |
| `GET` | `/health` | Health check endpoint |
| `POST` | `/auth/register` | Register a new user |
| `POST` | `/auth/login` | Authenticate and obtain JWT token |
| `POST` | `/upload` | Upload `.pdf`, `.docx`, or `.txt` multipart file |
| `POST` | `/ingest` | Ingest raw text directly with custom title & ID |
| `POST` | `/query` | Ask questions with hybrid retrieval and cited synthesis |
| `GET` | `/documents` | List all indexed documents and their chunk counts |
| `DELETE` | `/documents/{doc_id}` | Delete a document and its stored embeddings |
| `GET` | `/search?q=...` | Direct semantic vector similarity search |

Interactive Swagger documentation is available at **[http://localhost:8000/docs](http://localhost:8000/docs)**.

---

## 🚢 Deployment Guide

If you want to host DocuAI on the web (e.g. Render, Railway, HuggingFace Spaces, or a VPS):

### Environment Variables on the Host:
- `GROQ_API_KEY`: Your Groq API key.
- `DATABASE_URL`: Your persistent database connection string (e.g., free PostgreSQL on Neon or Supabase).
- `JWT_SECRET`: Any random 32+ character string.

### Start Command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

> **Client URL Switcher:** The web interface contains an API URL switcher in the header and footer (`API: localhost:8000`). If your frontend and backend run on separate hosts, you can point the frontend to your custom backend domain without touching any code.

---

## 📂 Project Structure

```
rag--system/
├── app/
│   ├── main.py          # Unified FastAPI backend: auth, embeddings, ingestion, RAG, & UI serving
│   ├── config.py        # Settings configuration helper
│   ├── database.py      # Database session utilities
│   ├── models.py        # SQLAlchemy schema definitions
│   └── routers/         # Optional modular routers
├── frontend/
│   ├── index.html       # Full DocuAI interactive dashboard (Dashboard, Ingest, Chat, Search, Docs)
│   ├── simple.html      # Minimalist chat-only interface
│   └── final.html       # Single-page direct portal
├── .env.example         # Documented template for environment variables
├── test_docuai.py       # Automated 7-suite validation script
├── requirements.txt     # Python package requirements
├── runtime.txt          # Python runtime version
└── README.md            # Complete documentation
```

---

## 🧪 Testing

Run the built-in test suite:
```bash
python test_docuai.py
```
Outputs:
```
==========================================
🚀 RUNNING DOCUAI TEST SUITE
==========================================
  ✓ Health check: ok
  ✓ System Name: DocuAI RAG System
  ✓ Demo user login & auto-provisioning OK
  ✓ Ingested document with chunk(s)
  ✓ Found document(s) in active index
  ✓ Answer returned successfully with complete list
  ✓ Semantic search found matching chunk(s)
  ✓ Deleted document
==========================================
🎉 ALL DOCUAI RAG SYSTEM TESTS PASSED!
==========================================
```

---

## 🤝 Contributing & Feedback

Have ideas for enhancements or found a bug? Feel free to open an issue or submit a pull request!
