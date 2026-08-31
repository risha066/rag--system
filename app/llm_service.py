"""
Thin wrapper around Groq for:
  - fast/cheap classification (routing)      -> groq_model_fast  (openai/gpt-oss-20b)
  - accurate extraction + answer generation  -> groq_model_accurate (openai/gpt-oss-120b)
"""
import json
import os
from openai import OpenAI

# HARDCODE YOUR API KEY HERE - REPLACE WITH YOUR ACTUAL KEY
GROQ_API_KEY = "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"  # <-- PASTE YOUR FULL KEY HERE

# Initialize OpenAI client with Groq's base URL
_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# CORRECT MODELS
ACCURATE_MODEL = "openai/gpt-oss-120b"
FAST_MODEL = "openai/gpt-oss-20b"

def _chat(model: str, system: str, user: str, json_mode: bool = False) -> str:
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    
    response = _client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        temperature=0.1,
        max_tokens=2048,
        **kwargs,
    )
    return response.choices[0].message.content

def extract_entities_relationships(chunk_text, chunk_id="", ontology=None):
    if ontology is None:
        ontology = {
            "entity_types": ["PERSON", "ORGANIZATION", "CONCEPT", "TECHNOLOGY", "LOCATION"],
            "relationship_types": ["RELATED_TO", "PART_OF", "USES", "DEVELOPS"]
        }
    
    system = (
        "Extract entities and relationships from text. "
        f"Allowed entity types: {', '.join(ontology.get('entity_types', []))}. "
        f"Allowed relationship types: {', '.join(ontology.get('relationship_types', []))}. "
        "Return ONLY valid JSON with this exact structure: "
        '{"entities": [{"canonical_name": "name", "type": "CONCEPT", "aliases": [], "confidence": 0.9}], '
        '"relationships": [{"source": "entity1", "target": "entity2", "type": "RELATED_TO", "confidence": 0.9}]}'
    )
    
    try:
        raw = _chat(ACCURATE_MODEL, system, chunk_text, json_mode=True)
        data = json.loads(raw)
        
        for e in data.get("entities", []):
            e["source_chunk_id"] = chunk_id
        for r in data.get("relationships", []):
            r["source_chunk_id"] = chunk_id
            
        return data
    except Exception as e:
        print(f"Extract error: {e}")
        return {"entities": [], "relationships": []}

def classify_route(question: str) -> str:
    system = (
        "Classify the question into exactly one label: GRAPH, VECTOR, or HYBRID. "
        "Reply with ONLY the label word."
    )
    label = _chat(FAST_MODEL, system, question).strip().upper()
    return label if label in ("GRAPH", "VECTOR", "HYBRID") else "HYBRID"

def generate_answer(question: str, graph_statements: list, passages: list) -> str:
    graph_block = "\n".join(graph_statements) if graph_statements else "(none)"
    passage_block = "\n".join(f"[{p.get('chunk_id', 'unknown')}] {p.get('text', '')}" for p in passages) if passages else "(none)"
    
    system = (
        "You are a precise assistant. Answer using ONLY the given context. "
        "Provide a detailed, comprehensive answer with examples from the context. "
        "Cite every factual claim with the matching [chunk_id]. "
        "If the context doesn't contain the answer, say you don't know — do not guess."
    )
    user = (
        f"Context (Passage-derived):\n{passage_block}\n\n"
        f"Question: {question}\n\n"
        "Provide a thorough, detailed answer with all relevant information from the context."
    )
    return _chat(ACCURATE_MODEL, system, user)