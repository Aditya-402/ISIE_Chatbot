"""Runtime RAG: hybrid retrieval (BM25 + BGE via RRF) + 2-signal
retrieval-confidence gate + Ollama answer generation.

Extracted from the research project's evaluate_gold.py — this file contains
only what's needed to serve answers, no evaluation/judging code.
"""

import json
import pickle
import re
from typing import Optional

import numpy as np
import faiss
import ollama
from sentence_transformers import SentenceTransformer

import config


# ---------------------------------------------------------------------------

_BM25_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")

def _tokenize(text: str) -> list[str]:
    return _BM25_TOKEN_RE.findall(text.lower())


class RAGEngine:
    """Loads embedder + FAISS + BM25 + chunk map once, then serves answers.

    Call .answer(question) to get back an Answer dataclass with the text,
    the sources used, latency, and whether the gate refused (and why)."""

    def __init__(self):
        print(f"[rag] loading embedder from {config.EMBED_MODEL_DIR}")
        self.embedder = SentenceTransformer(str(config.EMBED_MODEL_DIR))

        print(f"[rag] loading FAISS index from {config.FAISS_INDEX}")
        self.index = faiss.read_index(str(config.FAISS_INDEX))

        print(f"[rag] loading chunk map from {config.CHUNK_MAP}")
        with open(config.CHUNK_MAP, encoding="utf-8") as f:
            self.chunk_map = json.load(f)

        print(f"[rag] loading BM25 from {config.BM25_PKL}")
        with open(config.BM25_PKL, "rb") as f:
            payload = pickle.load(f)
        self.bm25 = payload["bm25"]

        print(f"[rag] {self.index.ntotal} chunks ready, "
              f"Ollama @ {config.OLLAMA_HOST}, model {config.LLM_MODEL}")

        self._ollama = ollama.Client(host=f"http://{config.OLLAMA_HOST}")

        # Tier-1 Q&A-bank cache (optional). Answers (reworded) bank questions
        # instantly from the vetted gold answer, with no LLM call.
        self.cache_index = None
        self.cache_map = None
        if getattr(config, "CACHE_ENABLED", False):
            try:
                print(f"[rag] loading Q&A cache from {config.CACHE_INDEX}")
                self.cache_index = faiss.read_index(str(config.CACHE_INDEX))
                with open(config.CACHE_MAP, encoding="utf-8") as f:
                    self.cache_map = json.load(f)
                print(f"[rag] cache ready: {self.cache_index.ntotal} bank questions "
                      f"(T={config.T_CACHE})")
            except Exception as e:
                print(f"[rag] cache disabled (load failed: {e})")
                self.cache_index = None

    # --- cache --------------------------------------------------------------

    def cache_lookup(self, question: str) -> Optional[dict]:
        """Return the bank Q&A dict if the question matches a cached bank
        question at >= T_CACHE cosine similarity, else None."""
        if self.cache_index is None:
            return None
        vec = self.embedder.encode([question], normalize_embeddings=True).astype(np.float32)
        sims, ids = self.cache_index.search(vec, 1)
        score = float(sims[0][0])
        if score >= config.T_CACHE:
            hit = dict(self.cache_map[int(ids[0][0])])
            hit["_cache_score"] = round(score, 4)
            return hit
        return None

    # --- retrieval ----------------------------------------------------------

    def retrieve(self, query: str) -> list[dict]:
        """Hybrid retrieval (BM25 + BGE via Reciprocal Rank Fusion). Each
        returned chunk carries `score` (RRF) and `dense_score` (BGE cosine)
        so the score gate can use both signals."""
        vec = self.embedder.encode([query], normalize_embeddings=True).astype(np.float32)
        dense_sims, dense_ids = self.index.search(vec, config.HYBRID_OVER_K)
        dense_rank = {int(i): r for r, i in enumerate(dense_ids[0]) if i != -1}
        dense_score_by_id = {int(i): float(s)
                             for i, s in zip(dense_ids[0], dense_sims[0]) if i != -1}

        tokens = _tokenize(query)
        bm25_scores = self.bm25.get_scores(tokens) if tokens else None
        if bm25_scores is None or not len(bm25_scores):
            sparse_rank = {}
        else:
            order = np.argsort(bm25_scores)[::-1][:config.HYBRID_OVER_K]
            sparse_rank = {int(i): r for r, i in enumerate(order)
                           if bm25_scores[i] > 0}

        fused: dict[int, float] = {}
        for i, r in dense_rank.items():
            fused[i] = fused.get(i, 0.0) + 1.0 / (config.RRF_K + r)
        for i, r in sparse_rank.items():
            fused[i] = fused.get(i, 0.0) + 1.0 / (config.RRF_K + r)

        top = sorted(fused.items(), key=lambda kv: -kv[1])[:config.TOP_K]
        return [
            {
                **self.chunk_map[i],
                "score":       round(score, 4),
                "dense_score": round(dense_score_by_id.get(i, 0.0), 4),
            }
            for i, score in top
        ]

    # --- gate ---------------------------------------------------------------

    @staticmethod
    def gate_triggers(retrieved: list[dict]) -> tuple[bool, str]:
        """Calibrated 2-signal gate. Returns (refuse, reason)."""
        if not retrieved:
            return True, "no chunks retrieved"
        top = retrieved[0]
        dense = top.get("dense_score", 0.0)
        rrf   = top.get("score", 0.0)
        if dense < config.GATE_DENSE:
            return True, f"dense_top1={dense:.4f} < {config.GATE_DENSE}"
        if dense < config.GATE_DENSE_STRICT and rrf < config.GATE_HYBRID:
            return True, (f"dense_top1={dense:.4f}<{config.GATE_DENSE_STRICT} "
                          f"AND hybrid_top1={rrf:.4f}<{config.GATE_HYBRID}")
        return False, "ok"

    # --- generation ---------------------------------------------------------

    def _truncate_to_words(self, raw: str) -> str:
        words = raw.split()
        if len(words) <= config.MAX_ANSWER_WORDS:
            return raw
        cand = " ".join(words[:config.MAX_ANSWER_WORDS])
        cut = -1
        for i, ch in enumerate(cand):
            if ch in ".!?":
                cut = i
        if cut > len(cand) // 3:
            return cand[:cut + 1]
        return cand.rstrip(",;:") + "..."

    @staticmethod
    def _trim_words(text: str, n: int) -> str:
        """Cap a chunk's text at n words before it goes into the prompt."""
        w = text.split()
        return text if len(w) <= n else " ".join(w[:n])

    def answer(self, question: str) -> dict:
        """Main entry. Tier 1: Q&A-bank cache (instant, no LLM). Tier 2/3:
        hybrid retrieval -> gate -> trimmed-context LLM. Adds cache_hit/
        cache_score to the returned dict (existing keys unchanged)."""
        import time
        t0 = time.time()

        # Tier 1 - Q&A-bank cache: a (reworded) bank question -> vetted gold answer.
        hit = self.cache_lookup(question)
        if hit is not None:
            return {
                "text":            hit.get("gold_answer", ""),
                "sources":         [{"source": hit.get("source"),
                                     "file":   hit.get("source"),
                                     "page":   hit.get("page")}],
                "gate_reason":     None,
                "top_dense_score": hit.get("_cache_score"),
                "top_rrf_score":   None,
                "latency":         round(time.time() - t0, 2),
                "refused":         False,
                "cache_hit":       True,
                "cache_score":     hit.get("_cache_score"),
            }

        retrieved = self.retrieve(question)

        if config.GATE_ENABLED:
            refuse, reason = self.gate_triggers(retrieved)
            if refuse:
                return {
                    "text":            config.REFUSAL_TEXT,
                    "sources":         [],
                    "gate_reason":     reason,
                    "top_dense_score": retrieved[0]["dense_score"] if retrieved else None,
                    "top_rrf_score":   retrieved[0]["score"]       if retrieved else None,
                    "latency":         round(time.time() - t0, 2),
                    "refused":         True,
                    "cache_hit":       False,
                }

        # Feed only the top CONTEXT_TOP_K chunks, each capped at CONTEXT_WORD_CAP
        # words - the time-to-first-token lever validated on the Pi.
        context = "\n\n".join(
            f"[Context {i}] Source: {r['source']}, Page {r['page']}\n"
            f"{self._trim_words(r['text'], config.CONTEXT_WORD_CAP)}"
            for i, r in enumerate(retrieved[:config.CONTEXT_TOP_K], 1)
        )
        resp = self._ollama.chat(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": config.RAG_SYSTEM_PROMPT},
                {"role": "user",   "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ],
            options={"temperature": config.TEMPERATURE,
                     "num_predict": config.NUM_PREDICT},
        )
        raw = resp["message"]["content"].strip()
        answer_text = self._truncate_to_words(raw)
        refused = answer_text.lower().startswith("out of scope") \
                  or "out of scope for the ev lab" in answer_text.lower()

        return {
            "text":            answer_text,
            "sources":         [
                {"source": r["source"], "file": r["file"], "page": r["page"]}
                for r in retrieved[:config.TOP_K]
            ],
            "gate_reason":     None,
            "top_dense_score": retrieved[0]["dense_score"] if retrieved else None,
            "top_rrf_score":   retrieved[0]["score"]       if retrieved else None,
            "latency":         round(time.time() - t0, 2),
            "refused":         refused,
            "cache_hit":       False,
        }


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick sanity check: load + answer one question + print.
    eng = RAGEngine()
    out = eng.answer("What is regenerative braking?")
    print(f"\nA: {out['text']}\n"
          f"Latency: {out['latency']}s  refused={out['refused']}  "
          f"gate={out['gate_reason']}\n"
          f"Sources: {out['sources']}")
