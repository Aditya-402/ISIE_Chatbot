"""Build the Tier-1 Q&A-bank cache used by rag_core.RAGEngine.

Embeds every question in question_bank/qa_bank_all.json with the bundled
embedder (so the vector space matches runtime exactly) and writes:
  rag_data/faiss.cache.index   - cosine (IndexFlatIP) over bank questions
  rag_data/cache_map.json      - [{qa_id, question, gold_answer, topic, source, page}]

Re-run this whenever the question bank handed to students changes:
  python build_cache.py
Paths are taken from config.py, so it is portable (Windows dev / Pi deploy).
"""
import json
import pathlib

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

import config

ROOT = pathlib.Path(__file__).resolve().parent
BANK = ROOT / "question_bank" / "qa_bank_all.json"

print(f"[build_cache] bank: {BANK}")
data = json.load(open(BANK, encoding="utf-8"))
qa = data["qa"] if isinstance(data, dict) and "qa" in data else data
print(f"[build_cache] {len(qa)} Q&As")

print(f"[build_cache] embedder: {config.EMBED_MODEL_DIR}")
emb = SentenceTransformer(str(config.EMBED_MODEL_DIR))
questions = [q["question"] for q in qa]
vecs = emb.encode(questions, normalize_embeddings=True, batch_size=64,
                  show_progress_bar=True).astype(np.float32)

idx = faiss.IndexFlatIP(vecs.shape[1])
idx.add(vecs)
faiss.write_index(idx, str(config.CACHE_INDEX))

cmap = []
for q in qa:
    src = q.get("source") or {}
    cmap.append({
        "qa_id":       q.get("id"),
        "question":    q["question"],
        "gold_answer": q.get("answer", ""),
        "topic":       q.get("topic"),
        "source":      src.get("book"),
        "page":        src.get("page"),
    })
json.dump(cmap, open(config.CACHE_MAP, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

print(f"[build_cache] wrote {idx.ntotal} vectors -> {config.CACHE_INDEX}")
print(f"[build_cache] wrote {len(cmap)} entries -> {config.CACHE_MAP}")
