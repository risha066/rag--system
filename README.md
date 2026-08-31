# Knowledge Graph RAG (Groq edition, free-tier friendly)

Hybrid retrieval system: Neo4j knowledge graph + pgvector index, routed through
FastAPI, with JWT auth and a lightweight HTML/JS frontend.

## Why Groq
Groq's API is free-tier and very low-latency for LLM inference, which is used for:
- entity/relationship extraction (`llama-3.3-70b-versatile` — accuracy matters here)
- query routing classification (`llama-3.1-8b-instant` — speed matters here)
- final answer generation (`llama-3.3-70b-versatile`)

**Groq does not serve embeddings.** Embeddings are generated locally and for free
with `sentence-transformers/all-MiniLM-L6-v2` (384-dim, runs on CPU, no API key,
no rate limit, no cost) — see `app/embeddings.py`.

## Setup

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY, DATABASE_URL, NEO4J_* , JWT_SECRET
```

You need:
- **Postgres with the `pgvector` extension** (free: use a local Docker container,
  or a free-tier hosted Postgres like Neon/Supabase which both support pgvector).
- **Neo4j** (free: Neo4j Aura Free tier, or `docker run neo4j`).
- **Groq API key** — free at https://console.groq.com/keys

```bash
uvicorn app.main:app --reload --port 8000
```

Open `frontend/index.html` directly in a browser (or serve it with any static
server). Update `API_BASE` in the `<script>` if your backend isn't on
`localhost:8000`.

## Endpoints
- `POST /auth/register`, `POST /auth/login` — JWT auth
- `POST /ingest` (auth required) — chunk, extract entities/relationships into
  Neo4j, embed chunks into pgvector
- `POST /query` (auth required) — classify route (GRAPH/VECTOR/HYBRID), retrieve,
  generate a cited answer

## Notes / things to tune for accuracy & speed
- `CHUNK_SIZE` / `CHUNK_OVERLAP` in `app/ingestion.py` — 500/50 words is a
  starting point, tune per corpus.
- HNSW index on the `chunks.embedding` column speeds up vector search at scale:
  ```sql
  CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
  ```
- `graph_db.py`'s `apoc.coll.toSet` call requires the APOC plugin on Neo4j; if
  you don't have APOC, replace that line with a plain list concatenation.
- Citation validation (rejecting answers that cite a chunk_id never retrieved)
  is not yet wired in `llm_service.generate_answer` — add a post-check there
  before shipping to production.
- For higher accuracy at the cost of latency, swap `groq_model_fast` for
  `groq_model_accurate` in the router too.

## Not yet built (stretch goals from the original spec)
- Incremental graph updates without full reingestion
- Community detection / corpus-level summaries
- In-response graph visualization
- Benchmark harness (Phase 5) — the query_logs table gives you the raw data
  (route, latency, outcome) to build this against.
