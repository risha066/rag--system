"""
Local, free embeddings via sentence-transformers.
Groq does NOT serve an embeddings endpoint (LLM inference only), so embeddings
are generated on-machine with a small, fast MiniLM model. No API cost, no rate limits.
"""
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from .config import settings

@lru_cache(maxsize=1)
def _model():
    return SentenceTransformer(settings.embedding_model)

def embed_text(text: str) -> list[float]:
    return _model().encode(text, normalize_embeddings=True).tolist()

def embed_batch(texts: list[str]) -> list[list[float]]:
    return _model().encode(texts, normalize_embeddings=True, batch_size=32).tolist()
