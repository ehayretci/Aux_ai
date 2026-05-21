"""
Knowledge-base Retriever
========================

Reads from the local ChromaDB store at `training/knowledge_base/` and returns
labelled examples relevant to a query. Specialists call this BEFORE making
their Gemini API call so the model can be calibrated against curated
positive/negative examples for the same category.

Returns an empty list silently when the knowledge base is empty or the store
is missing — never crashes. Specialists check the result and only modify
their prompt if examples were returned.
"""

import os
from typing import List, Dict


# Resolve the persistent store path relative to the repo root, not CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_BASE_DIR = os.path.join(_HERE, "knowledge_base")
COLLECTION_NAME = "ux_examples"

_client = None
_collection = None


def _ensure_client():
    """Lazy-init ChromaDB. Avoids the import cost on hot paths that never use it."""
    global _client, _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=KNOWLEDGE_BASE_DIR)
        _collection = _client.get_or_create_collection(name=COLLECTION_NAME)
        return _collection
    except Exception as e:
        # If chromadb fails to load (missing native deps, etc.) we degrade
        # to "no examples" rather than crashing the whole evaluation.
        print(f"[retriever] knowledge base unavailable: {e}")
        return None


def get_relevant_examples(category: str, query_text: str, n_results: int = 3) -> List[Dict]:
    """Return up to `n_results` examples from the same category most relevant to `query_text`.

    Each result has: {note, polarity, image_path, distance}.
    Returns [] (silently) if the store is empty, missing, or unreachable.
    """
    col = _ensure_client()
    if col is None:
        return []

    try:
        # Don't error if the collection has fewer items than n_results.
        try:
            count = col.count()
        except Exception:
            count = 0
        if count == 0:
            return []

        n = min(n_results, count)
        res = col.query(
            query_texts=[query_text or "ux example"],
            n_results=n,
            where={"category": category},
        )
    except Exception as e:
        print(f"[retriever] query failed: {e}")
        return []

    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0] if res.get("distances") else [None] * len(docs)

    out: List[Dict] = []
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({
            "note": doc,
            "polarity": (meta or {}).get("polarity", "unknown"),
            "image_path": (meta or {}).get("image_path", ""),
            "distance": dist,
        })
    return out


def format_examples_for_prompt(examples: List[Dict]) -> str:
    """Render retrieved examples as a prompt-prefix block.

    Returns an empty string if `examples` is empty so the caller can `+` it
    into a prompt unconditionally.
    """
    if not examples:
        return ""
    lines = [
        "Here are relevant examples from your knowledge base. Use these to "
        "calibrate your evaluation:",
        "",
    ]
    for i, ex in enumerate(examples, start=1):
        polarity = ex.get("polarity", "?").upper()
        lines.append(f"  Example {i} ({polarity}): {ex.get('note', '').strip()}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    cat = sys.argv[1] if len(sys.argv) > 1 else "Visual Design"
    q = sys.argv[2] if len(sys.argv) > 2 else "primary CTA"
    res = get_relevant_examples(cat, q)
    print(f"category={cat!r} query={q!r}: {len(res)} result(s)")
    for r in res:
        print(f"  [{r['polarity']}] {r['note'][:120]}")
