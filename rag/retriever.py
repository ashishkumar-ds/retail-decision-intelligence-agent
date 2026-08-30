"""Deterministic lexical retrieval (BM25) over the Tier-2 methodology corpus.

Pure Python, no external index dependency: the corpus is small and retrieval
must be reproducible from the repo alone. Same query + corpus always yields
the same ranking.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .corpus import CorpusChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Retriever:
    def __init__(self, chunks: list[CorpusChunk]):
        self.chunks = sorted(chunks, key=lambda c: c.chunk_id)  # stable order
        self.doc_tokens = [tokenize(c.title + "\n" + c.text) for c in self.chunks]
        self.doc_counts = [Counter(tokens) for tokens in self.doc_tokens]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avg_length = (sum(self.doc_lengths) / len(self.doc_lengths)) if self.doc_lengths else 0.0
        self.n_docs = len(self.chunks)
        self.idf: dict[str, float] = self._compute_idf()

    def _compute_idf(self) -> dict[str, float]:
        df: Counter[str] = Counter()
        for counts in self.doc_counts:
            df.update(counts.keys())
        return {
            term: math.log((self.n_docs - n + 0.5) / (n + 0.5) + 1.0)
            for term, n in df.items()
        }

    def score(self, query: str, index: int) -> float:
        query_tokens = tokenize(query)
        counts = self.doc_counts[index]
        length = self.doc_lengths[index] or 1
        score = 0.0
        for term in query_tokens:
            freq = counts.get(term, 0)
            if freq == 0:
                continue
            idf = self.idf.get(term, 0.0)
            score += idf * (freq * (K1 + 1)) / (freq + K1 * (1 - B + B * length / self.avg_length))
        return score

    def retrieve(self, query: str, k: int = 3) -> list[tuple[CorpusChunk, float]]:
        """Top-k chunks for the query; ties broken by chunk_id for determinism."""
        scored = [(self.score(query, i), -i, self.chunks[i]) for i in range(self.n_docs)]
        scored = [s for s in scored if s[0] > 0.0]
        scored.sort(key=lambda t: (-t[0], t[2].chunk_id))
        return [(chunk, round(score, 4)) for score, _, chunk in scored[:max(k, 0)]]
