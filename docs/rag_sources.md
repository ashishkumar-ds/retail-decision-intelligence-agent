# Priority 4 RAG — Grounding Sources for the "Why" Endpoint

Research note (2026-08-30). Decides which external corpora are safe and
useful to ground the Priority 4 explanation endpoint, alongside (never
instead of) the project's own evidence trail.

## 0. Two-tier grounding contract (hard rule)

- **Tier 1 — internal evidence (authoritative).** Recommendation JSONL,
  Phase-2 registry events, outcome payloads, causal evidence records, logs.
  Answers to "why did store X get REC" must cite record IDs from Tier 1 only.
  The LLM may not blend in Tier 2 to justify a decision.
- **Tier 2 — methodology corpus (supporting).** Vetted external documents
  that explain *why the method is valid* (e.g., why matched-control DiD beats
  own-baseline lift). Always attributed; never used to derive a decision;
  retrieval-augmented only for explanation phrasing.

Guardrail: if a Tier-2 document and a Tier-1 record disagree, Tier 1 wins and
the answer must say so.

## 1. Verified sources — dunnhumby / dataset-adjacent

| Source | Status | Use | Notes |
|---|---|---|---|
| dunnhumby.com case-studies index (verified fetchable, 2026-08-30) | ✅ live | Tier 2: business context for campaign effectiveness, price perception, loyalty, retail media measurement; same vendor as our dataset | Content marketing — treat as narrative evidence of practice, not science. Ingest via index links. |
| dunnhumby "The Complete Journey" dataset docs (shipped with the 8 CSVs; mirrored on Kaggle, e.g. kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey — page verified live) | ✅ live | Tier 2: variable definitions (DAY, WEEK_NO, COUPON_DISC, STORE_ID semantics), data dictionary for explaining fields in answers | No canonical academic paper exists: arXiv full-text query for "dunnhumby"/"The Complete Journey" returns **0 results** (checked via arXiv API 2026-08-30). |
| Our own DiD notebook (`store_performance_analysis_with_DiD.ipynb`) + P1 API methodology strings | ✅ in-repo | Tier 1.5 — first-class methodology source, the strongest grounding we own | Already structured; should be the primary Tier-2 corpus, chunked per markdown cell |

## 2. Methodology corpus (Tier 2 backbone) — established literature

These are stable, widely-cited anchors for every technique the agent uses.
All are well-known works (verify DOIs at ingestion time):

| Topic in our agent | Source |
|---|---|
| Difference-in-differences, parallel trends, matched controls | Angrist & Pischke, *Mostly Harmless Econometrics* (2009); Imbens & Rubin, *Causal Inference for Statistics and Policy* (2015) |
| Why raw pre/post lift is biased (market drift) | Same DiD references + our notebook's measured +9.7% drift example |
| Heterogeneous treatment effects / uplift targeting | Künzel et al., PNAS 2019 (meta-learners); Wager & Athey, JASA 2018 (causal forests); Radcliffe & Surry (2011), QINI/uplift curves |
| Matching / k-NN control selection | Imbens & Rubin (2015), matching chapter — matches our `/controls` implementation |
| Decision intelligence framing | Gartner DI strategic-trend framing as cited by HyperFinity; Pratt & Zangari (2008) DI origins; Kozyrkov's chief-decision-scientist essays |
| Experiment design at retail scale | Priority 5 roadmap: epsilon-greedy / Thompson sampling literature (standard bandits texts) |

## 3. Similar-but-not-dunnhumby substitutes (if more depth is needed)

- **Kroger / 84.51°** publications — dunnhumby USA became 84.51°; their
  published customer-science material is the closest organizational successor.
- **Instacart Public Data (2023)** and academic papers built on it — modern
  basket-level panel with published papers; good for basket-level examples.
- **Ta Feng / UCI online retail datasets** — older, smaller, but commonly used
  in published promotion/customer-analytics papers.
- Industry research: McKinsey/Gartner retail-AI pieces (bot-blocked during
  this research pass — ingest manually or via cached PDFs).

## 4. Ingestion plan for Priority 4 (concrete)

1. **Corpus builder script** (`rag/build_corpus.py`): chunk sources into
   ~400-token passages with metadata `{source_type, title, url_or_path,
   chunk_id, ingested_at, license_note}`; store JSONL + a local vector index
   (e.g., sentence-transformers + FAISS, or BM25-only for v1 determinism).
2. **Corpus v1 (recommended first cut, all in-repo or public):**
   DiD notebook markdown cells → dunnhumby dataset data dictionary → 3–5
   dunnhumby case studies from the index → methodology classics (sections,
   with citation) → our own `methodology` blocks already emitted by the APIs.
3. **Retrieval policy:** Tier-1 records retrieved by exact ID/provenance join
   (no semantic search needed — they are structured); Tier-2 retrieved
   semantically; the prompt template separates the two and mandates citation
   format `[record:<id>]` / `[source:<chunk_id>]`.
4. **Grounding tests:** hallucination guard tests asserting every claim in an
   answer maps to a cited chunk (string-match check), mirroring our
   fail-closed test style.

## 5. Honest limitations

- dunnhumby does not publish a methods paper for The Complete Journey; its
  case studies are marketing narratives. Our strongest methodology grounding
  is our own DiD notebook + the academic classics — this is *better* than
  vendor content because it is reproducible from our repo.
- Gartner/McKinsey full reports are paywalled/bot-blocked; use the
  publicly quotable DI definitions only.
