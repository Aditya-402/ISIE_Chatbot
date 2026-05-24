"""Runtime RAG: hybrid retrieval (BM25 + BGE via RRF) + 2-signal
retrieval-confidence gate + Ollama answer generation.

Extracted from the research project's evaluate_gold.py — this file contains
only what's needed to serve answers, no evaluation/judging code.
"""

import json
import pickle
import re
import threading
import time
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


# --- switching-mode command vocabulary (LLM intent classifier) -------------
SWITCH_CHANNELS = ("ignition", "headlight", "all_lamp", "hazard",
                   "left_ind", "right_ind", "brake", "horn", "reverse")

_CMD_SYS = (
    "You map a spoken vehicle command to ONE control and an action.\n"
    "Controls: ignition, headlight, all_lamp, hazard, left_ind, right_ind, brake, horn, reverse.\n"
    "Reply with EXACTLY one line, nothing else, one of:\n"
    "  <control>:on\n"
    "  <control>:off\n"
    "  none:unknown        (gibberish or unclear)\n"
    "  none:not_a_control  (a real request but not one of these controls)\n"
    "Synonyms: engine=ignition; lights/headlamp=headlight; blinker/turn signal=left_ind or "
    "right_ind; honk=horn:on; back up/reverse=reverse:on; apply/press brake=brake:on; "
    "release brake=brake:off.\n"
    "Examples: 'start the engine'->ignition:on  'kill the engine'->ignition:off  "
    "'apply the brake'->brake:on  'do not apply the brake'->brake:off  "
    "'indicate left'->left_ind:on  'honk'->horn:on  'back up'->reverse:on  "
    "'what is regen braking'->none:not_a_control  'asdf'->none:unknown"
)

_CMD_RE = re.compile(
    r"(ignition|headlight|all_lamp|hazard|left_ind|right_ind|brake|horn|reverse|none)"
    r":(on|off|unknown|not_a_control)")


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

        # Editable bank (Knowledge Base tab): full Q&A records + a lock that
        # guards concurrent add-vs-search on the shared cache index.
        self._bank_lock = threading.Lock()
        self.bank_data = {"qa": []}
        self.bank = []
        try:
            with open(config.BANK_JSON, encoding="utf-8") as f:
                self.bank_data = json.load(f)
            self.bank = self.bank_data.setdefault("qa", [])
            print(f"[rag] bank: {len(self.bank)} Q&As ({config.BANK_JSON.name})")
        except Exception as e:
            print(f"[rag] bank load failed: {e}")

    # --- cache --------------------------------------------------------------

    def cache_lookup(self, question: str) -> Optional[dict]:
        """Return the bank Q&A dict if the question matches a cached bank
        question at >= T_CACHE cosine similarity, else None."""
        if self.cache_index is None:
            return None
        vec = self.embedder.encode([question], normalize_embeddings=True).astype(np.float32)
        with self._bank_lock:                       # guard vs. a concurrent add_qa()
            sims, ids = self.cache_index.search(vec, 1)
            score = float(sims[0][0])
            hit = dict(self.cache_map[int(ids[0][0])]) if score >= config.T_CACHE else None
        if hit is not None:
            hit["_cache_score"] = round(score, 4)
            return hit
        return None

    # --- knowledge base: list + live add ------------------------------------

    def list_qa(self) -> list:
        """Lightweight view of every bank Q&A for the Knowledge Base browser."""
        with self._bank_lock:
            out = []
            for e in self.bank:
                src = e.get("source") or {}
                ref = str(src.get("book") or "")
                page = src.get("page")
                if page not in (None, ""):
                    ref = f"{ref}, p.{page}" if ref else f"p.{page}"
                out.append({"id": e.get("id"), "question": e.get("question", ""),
                            "answer": e.get("answer", ""), "topic": e.get("topic"),
                            "reference": ref,
                            "deletable": str(e.get("id", "")).startswith("user-")})
            return out

    @staticmethod
    def _is_user_added(qa_id: str) -> bool:
        return str(qa_id or "").startswith("user-")

    def delete_qa(self, qa_id: str) -> dict:
        """Delete a USER-ADDED question (id 'user-...') from the bank JSON and the
        live cache. Base questions are protected and cannot be deleted. Thread-safe."""
        qa_id = (qa_id or "").strip()
        if not self._is_user_added(qa_id):
            raise ValueError("only user-added questions can be deleted")
        with self._bank_lock:
            idx = next((i for i, e in enumerate(self.bank) if e.get("id") == qa_id), None)
            if idx is None:
                raise ValueError(f"question '{qa_id}' not found")
            if not self._is_user_added(self.bank[idx].get("id")):   # defence-in-depth
                raise ValueError("only user-added questions can be deleted")
            removed = self.bank.pop(idx)
            with open(config.BANK_JSON, "w", encoding="utf-8") as f:
                json.dump(self.bank_data, f, indent=2, ensure_ascii=False)

            if self.cache_index is not None:
                pos = next((i for i, m in enumerate(self.cache_map)
                            if m.get("qa_id") == qa_id), None)
                if pos is not None:
                    n = self.cache_index.ntotal
                    vecs = self.cache_index.reconstruct_n(0, n)   # (n, d) float32
                    keep = [i for i in range(n) if i != pos]
                    new_idx = faiss.IndexFlatIP(vecs.shape[1])
                    if keep:
                        new_idx.add(vecs[keep])
                    self.cache_index = new_idx
                    del self.cache_map[pos]
                    faiss.write_index(self.cache_index, str(config.CACHE_INDEX))
                    with open(config.CACHE_MAP, "w", encoding="utf-8") as f:
                        json.dump(self.cache_map, f, indent=2, ensure_ascii=False)
            return removed

    def add_qa(self, question: str, answer: str, reference: str = "") -> dict:
        """Append a Q&A to the bank JSON AND the live cache, persisting both to
        disk so it survives restarts. Thread-safe. Returns the new entry."""
        question = (question or "").strip()
        answer = (answer or "").strip()
        reference = (reference or "").strip()
        if not question or not answer:
            raise ValueError("question and answer are required")
        with self._bank_lock:
            nums = [int(str(e.get("id", "")).split("-")[-1])
                    for e in self.bank
                    if str(e.get("id", "")).startswith("user-")
                    and str(e.get("id", "")).split("-")[-1].isdigit()]
            new_id = f"user-{(max(nums) + 1) if nums else 1:03d}"
            entry = {
                "id": new_id, "question": question, "answer": answer,
                "topic": "user-added",
                "source": {"book": reference or "User added", "page": "-"},
                "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self.bank.append(entry)
            with open(config.BANK_JSON, "w", encoding="utf-8") as f:
                json.dump(self.bank_data, f, indent=2, ensure_ascii=False)

            if self.cache_index is not None:
                vec = self.embedder.encode([question], normalize_embeddings=True).astype(np.float32)
                self.cache_index.add(vec)
                self.cache_map.append({
                    "qa_id": new_id, "question": question, "gold_answer": answer,
                    "topic": "user-added", "source": entry["source"]["book"], "page": "-",
                })
                faiss.write_index(self.cache_index, str(config.CACHE_INDEX))
                with open(config.CACHE_MAP, "w", encoding="utf-8") as f:
                    json.dump(self.cache_map, f, indent=2, ensure_ascii=False)
            return entry

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

    # --- switching mode: free-form command -> control intent ---------------

    def classify_command(self, utterance: str, states: dict = None) -> dict:
        """Map a free-form command to ONE control + on/off via the LLM. Returns
        {channel, action, error}. Deterministic: the LLM only emits a fixed token."""
        states = states or {}
        state_line = ", ".join(f"{c}={'on' if states.get(c) else 'off'}" for c in SWITCH_CHANNELS)
        resp = self._ollama.chat(
            model=config.LLM_MODEL,
            messages=[{"role": "system", "content": _CMD_SYS},
                      {"role": "user", "content": f"Current state: {state_line}\nCommand: {utterance}"}],
            options={"temperature": 0, "num_predict": 12},
        )
        raw = resp["message"]["content"].strip().lower()
        m = _CMD_RE.search(raw)
        if not m:
            return {"channel": None, "action": None, "error": "unknown", "raw": raw[:80]}
        ch, act = m.group(1), m.group(2)
        if ch == "none":
            return {"channel": None, "action": None,
                    "error": act if act in ("unknown", "not_a_control") else "unknown",
                    "raw": raw[:80]}
        return {"channel": ch, "action": act, "error": None, "raw": raw[:80]}


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick sanity check: load + answer one question + print.
    eng = RAGEngine()
    out = eng.answer("What is regenerative braking?")
    print(f"\nA: {out['text']}\n"
          f"Latency: {out['latency']}s  refused={out['refused']}  "
          f"gate={out['gate_reason']}\n"
          f"Sources: {out['sources']}")
