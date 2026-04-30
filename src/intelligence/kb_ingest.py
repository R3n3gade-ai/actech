"""
ARMS Intelligence Layer: Knowledge Base Ingest

Incremental document ingestion pipeline for ARMS vector knowledge base.
Processes session logs, thesis reviews, regime history, spec documents,
and market intelligence into searchable embeddings for LLM context retrieval.

Pipeline:
  1. Scan sources (session_log, tdc_state, regime_history, docs/, sec filings)
  2. Chunk documents into ~500-token segments with overlap
  3. Generate embeddings via OpenAI text-embedding-3-small (or local fallback)
  4. Upsert into vector store (Pinecone in production, local JSON in dev)
  5. Maintain ingest checkpoint for incremental processing

Sources:
  - achelion_arms/logs/session_log.jsonl   (operational audit trail)
  - achelion_arms/state/tdc_state.json     (thesis integrity snapshots)
  - achelion_arms/state/regime_history.json (regime transition history)
  - docs/                                   (ARMS spec documents)
  - SEC EDGAR filings (via sec_edgar_feed)  (regulatory intelligence)

Reference: ARMS Infrastructure Specification v1.0
Cadence: Nightly 2:00 AM CT (via master_scheduler job_kb_ingest)
"""

import datetime
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger('arms.kb')


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

CHUNK_SIZE = 500          # target tokens per chunk
CHUNK_OVERLAP = 50        # token overlap between chunks
EMBEDDING_MODEL = os.environ.get('ARMS_EMBEDDING_MODEL', 'text-embedding-3-small')
EMBEDDING_DIM = 1536      # dimension for text-embedding-3-small

# Vector store configuration
VECTOR_STORE = os.environ.get('ARMS_VECTOR_STORE', 'local')  # 'pinecone' | 'local'
PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY', '')
PINECONE_INDEX = os.environ.get('ARMS_PINECONE_INDEX', 'arms-kb')
PINECONE_ENVIRONMENT = os.environ.get('PINECONE_ENVIRONMENT', 'us-east-1')

LOCAL_KB_PATH = os.path.join('achelion_arms', 'state', 'kb_vectors.json')
CHECKPOINT_PATH = os.path.join('achelion_arms', 'state', 'kb_checkpoint.json')


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class KBDocument:
    """A document chunk ready for embedding."""
    doc_id: str
    source: str          # 'session_log', 'tdc_state', 'regime_history', 'spec', 'filing'
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ''


@dataclass
class KBSearchResult:
    """A search result from the knowledge base."""
    doc_id: str
    content: str
    source: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestResult:
    """Summary of a KB ingest run."""
    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    chunks_upserted: int
    errors: int
    duration_seconds: float


# ═══════════════════════════════════════════════════════════════
# Checkpoint Management
# ═══════════════════════════════════════════════════════════════

def _load_checkpoint() -> Dict[str, Any]:
    if not os.path.exists(CHECKPOINT_PATH):
        return {'last_session_log_line': 0, 'last_regime_count': 0, 'processed_docs': []}
    try:
        with open(CHECKPOINT_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {'last_session_log_line': 0, 'last_regime_count': 0, 'processed_docs': []}


def _save_checkpoint(checkpoint: Dict[str, Any]):
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, 'w') as f:
        json.dump(checkpoint, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Text Chunking
# ═══════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks of approximately chunk_size tokens."""
    words = text.split()
    if not words:
        return []

    # Approximate words-per-chunk (avg ~1.3 tokens per word)
    words_per_chunk = int(chunk_size / 1.3)
    overlap_words = int(overlap / 1.3)
    chunks = []
    start = 0

    while start < len(words):
        end = min(start + words_per_chunk, len(words))
        chunk = ' '.join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap_words if (end - overlap_words) > start else end

    return chunks


# ═══════════════════════════════════════════════════════════════
# Source Scanners
# ═══════════════════════════════════════════════════════════════

def _scan_session_log(checkpoint: Dict) -> List[KBDocument]:
    """Scan new session log entries since last checkpoint."""
    path = 'achelion_arms/logs/session_log.jsonl'
    if not os.path.exists(path):
        return []

    last_line = checkpoint.get('last_session_log_line', 0)
    docs = []

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = lines[last_line:]
    checkpoint['last_session_log_line'] = len(lines)

    # Batch session log entries into daily summaries for efficient embedding
    daily_batches: Dict[str, List[str]] = {}
    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            date_key = entry.get('timestamp', '')[:10]
            summary = f"{entry.get('action_type', 'UNKNOWN')}: {entry.get('triggering_signal', '')}"
            daily_batches.setdefault(date_key, []).append(summary)
        except json.JSONDecodeError:
            continue

    for date_key, entries in daily_batches.items():
        content = f"ARMS Session Log — {date_key}\n" + '\n'.join(entries)
        doc_id = hashlib.sha256(f"session_log_{date_key}_{len(entries)}".encode()).hexdigest()[:16]
        docs.append(KBDocument(
            doc_id=doc_id,
            source='session_log',
            content=content,
            metadata={'date': date_key, 'entry_count': len(entries)},
            timestamp=date_key,
        ))

    return docs


def _scan_regime_history(checkpoint: Dict) -> List[KBDocument]:
    """Scan new regime history entries."""
    path = 'achelion_arms/state/regime_history.json'
    if not os.path.exists(path):
        return []

    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except Exception:
        return []

    last_count = checkpoint.get('last_regime_count', 0)
    new_entries = data[last_count:] if isinstance(data, list) else []
    checkpoint['last_regime_count'] = len(data) if isinstance(data, list) else 0

    if not new_entries:
        return []

    content_lines = [f"Regime History Transitions ({len(new_entries)} new entries):"]
    for entry in new_entries:
        content_lines.append(
            f"  {entry.get('timestamp', '?')}: {entry.get('regime', '?')} "
            f"(score={entry.get('score', '?')}, ceiling={entry.get('equity_ceiling_pct', '?')}) "
            f"— {entry.get('catalyst', 'none')}"
        )

    content = '\n'.join(content_lines)
    doc_id = hashlib.sha256(f"regime_{last_count}_{len(data)}".encode()).hexdigest()[:16]

    return [KBDocument(
        doc_id=doc_id,
        source='regime_history',
        content=content,
        metadata={'entries': len(new_entries)},
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )]


def _scan_tdc_state() -> List[KBDocument]:
    """Scan current TDC thesis state for knowledge base context."""
    path = 'achelion_arms/state/tdc_state.json'
    if not os.path.exists(path):
        return []

    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except Exception:
        return []

    if not data:
        return []

    content_lines = ["Current Thesis Defense Council State:"]
    for ticker, state in data.items():
        tis = state.get('tis_label', 'UNKNOWN')
        score = state.get('tis_score', '?')
        bear = state.get('bear_case', '')[:100]
        content_lines.append(f"  {ticker}: TIS={tis} ({score}) — Bear case: {bear}")

    content = '\n'.join(content_lines)
    doc_id = hashlib.sha256(f"tdc_state_{len(data)}".encode()).hexdigest()[:16]

    return [KBDocument(
        doc_id=doc_id,
        source='tdc_state',
        content=content,
        metadata={'tickers': list(data.keys())},
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )]


def _scan_spec_docs(checkpoint: Dict) -> List[KBDocument]:
    """Scan ARMS specification documents for reference embeddings."""
    docs_dir = 'docs'
    if not os.path.exists(docs_dir):
        return []

    processed = set(checkpoint.get('processed_docs', []))
    docs = []

    for filename in os.listdir(docs_dir):
        if not filename.endswith('.md'):
            continue
        filepath = os.path.join(docs_dir, filename)
        if filepath in processed:
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue

        if len(content) < 100:
            continue

        doc_id = hashlib.sha256(f"spec_{filename}".encode()).hexdigest()[:16]
        docs.append(KBDocument(
            doc_id=doc_id,
            source='spec',
            content=content,
            metadata={'filename': filename},
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ))
        processed.add(filepath)

    checkpoint['processed_docs'] = list(processed)
    return docs


# ═══════════════════════════════════════════════════════════════
# Embedding Generation
# ═══════════════════════════════════════════════════════════════

def _generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings via OpenAI API.
    Falls back to simple hash-based vectors when API is not available.
    """
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        logger.info("[KB] No OPENAI_API_KEY — using hash-based fallback embeddings")
        return _fallback_embeddings(texts)

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        logger.warning(f"[KB] Embedding API failed, using fallback: {e}")
        return _fallback_embeddings(texts)


def _fallback_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Hash-based deterministic pseudo-embeddings for dev/offline mode.
    NOT suitable for production similarity search — only for structure testing.
    """
    embeddings = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        # Expand hash to EMBEDDING_DIM dimensions using iterative hashing
        vec = []
        seed = h
        while len(vec) < EMBEDDING_DIM:
            seed = hashlib.sha256(seed).digest()
            for byte in seed:
                if len(vec) >= EMBEDDING_DIM:
                    break
                vec.append((byte / 255.0) * 2.0 - 1.0)  # normalize to [-1, 1]
        embeddings.append(vec)
    return embeddings


# ═══════════════════════════════════════════════════════════════
# Vector Store Operations
# ═══════════════════════════════════════════════════════════════

class LocalVectorStore:
    """File-based vector store for development. Stores vectors as JSON."""

    def __init__(self, path: str = LOCAL_KB_PATH):
        self.path = path
        self._data: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self._data, f)

    def upsert(self, doc_id: str, vector: List[float], content: str, metadata: Dict):
        self._data[doc_id] = {
            'vector': vector,
            'content': content,
            'metadata': metadata,
        }
        self._save()

    def search(self, query_vector: List[float], top_k: int = 5) -> List[KBSearchResult]:
        """Cosine similarity search over stored vectors."""
        if not self._data:
            return []

        results = []
        for doc_id, entry in self._data.items():
            stored_vec = entry.get('vector', [])
            if len(stored_vec) != len(query_vector):
                continue
            score = self._cosine_similarity(query_vector, stored_vec)
            results.append(KBSearchResult(
                doc_id=doc_id,
                content=entry.get('content', ''),
                source=entry.get('metadata', {}).get('source', 'unknown'),
                score=score,
                metadata=entry.get('metadata', {}),
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @property
    def count(self) -> int:
        return len(self._data)


class PineconeVectorStore:
    """Pinecone vector store for production."""

    def __init__(self):
        self._index = None

    def _ensure_connected(self):
        if self._index is not None:
            return
        try:
            from pinecone import Pinecone
            pc = Pinecone(api_key=PINECONE_API_KEY)
            self._index = pc.Index(PINECONE_INDEX)
            logger.info(f"[KB] Connected to Pinecone index: {PINECONE_INDEX}")
        except Exception as e:
            logger.error(f"[KB] Pinecone connection failed: {e}")
            raise

    def upsert(self, doc_id: str, vector: List[float], content: str, metadata: Dict):
        self._ensure_connected()
        self._index.upsert(vectors=[{
            'id': doc_id,
            'values': vector,
            'metadata': {**metadata, 'content': content[:1000]},
        }])

    def search(self, query_vector: List[float], top_k: int = 5) -> List[KBSearchResult]:
        self._ensure_connected()
        results = self._index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
        )
        return [
            KBSearchResult(
                doc_id=match.id,
                content=match.metadata.get('content', ''),
                source=match.metadata.get('source', 'unknown'),
                score=match.score,
                metadata=match.metadata,
            )
            for match in results.matches
        ]

    @property
    def count(self) -> int:
        self._ensure_connected()
        stats = self._index.describe_index_stats()
        return stats.total_vector_count


def _get_vector_store():
    """Factory for vector store based on configuration."""
    if VECTOR_STORE == 'pinecone' and PINECONE_API_KEY:
        return PineconeVectorStore()
    return LocalVectorStore()


# ═══════════════════════════════════════════════════════════════
# Main Ingest Pipeline
# ═══════════════════════════════════════════════════════════════

def run_kb_ingest() -> IngestResult:
    """
    Run incremental knowledge base ingest pipeline.
    Called nightly at 2:00 AM CT by master_scheduler.

    Steps:
      1. Load checkpoint (last processed positions)
      2. Scan all sources for new documents
      3. Chunk documents into embedding-sized segments
      4. Generate embeddings (OpenAI API or fallback)
      5. Upsert into vector store
      6. Save checkpoint for next incremental run
    """
    import time as _time
    start = _time.monotonic()

    checkpoint = _load_checkpoint()
    store = _get_vector_store()

    # Step 2: Scan all sources
    all_docs: List[KBDocument] = []
    all_docs.extend(_scan_session_log(checkpoint))
    all_docs.extend(_scan_regime_history(checkpoint))
    all_docs.extend(_scan_tdc_state())
    all_docs.extend(_scan_spec_docs(checkpoint))

    if not all_docs:
        _save_checkpoint(checkpoint)
        duration = _time.monotonic() - start
        logger.info("[KB] No new documents to ingest")
        return IngestResult(0, 0, 0, 0, 0, duration)

    # Step 3: Chunk
    chunks: List[Dict] = []
    for doc in all_docs:
        text_chunks = _chunk_text(doc.content)
        for i, chunk_text in enumerate(text_chunks):
            chunk_id = f"{doc.doc_id}_chunk{i}"
            chunks.append({
                'id': chunk_id,
                'text': chunk_text,
                'source': doc.source,
                'metadata': {**doc.metadata, 'source': doc.source, 'chunk_index': i},
            })

    # Step 4: Generate embeddings in batches
    batch_size = 100
    all_vectors = []
    errors = 0
    for i in range(0, len(chunks), batch_size):
        batch_texts = [c['text'] for c in chunks[i:i + batch_size]]
        try:
            vectors = _generate_embeddings(batch_texts)
            all_vectors.extend(vectors)
        except Exception as e:
            logger.error(f"[KB] Embedding batch {i // batch_size} failed: {e}")
            errors += 1
            all_vectors.extend(_fallback_embeddings(batch_texts))

    # Step 5: Upsert into store
    upserted = 0
    for chunk, vector in zip(chunks, all_vectors):
        try:
            store.upsert(
                doc_id=chunk['id'],
                vector=vector,
                content=chunk['text'],
                metadata=chunk['metadata'],
            )
            upserted += 1
        except Exception as e:
            logger.error(f"[KB] Upsert failed for {chunk['id']}: {e}")
            errors += 1

    # Step 6: Save checkpoint
    _save_checkpoint(checkpoint)

    duration = _time.monotonic() - start
    result = IngestResult(
        documents_processed=len(all_docs),
        chunks_created=len(chunks),
        chunks_embedded=len(all_vectors),
        chunks_upserted=upserted,
        errors=errors,
        duration_seconds=duration,
    )

    logger.info(
        f"[KB] Ingest complete: {result.documents_processed} docs, "
        f"{result.chunks_created} chunks, {result.chunks_upserted} upserted, "
        f"{result.errors} errors ({result.duration_seconds:.1f}s)"
    )
    return result


# ═══════════════════════════════════════════════════════════════
# Search Interface (used by LLM Wrapper)
# ═══════════════════════════════════════════════════════════════

def search_knowledge_base(query: str, top_k: int = 5) -> List[KBSearchResult]:
    """
    Search the ARMS knowledge base for context relevant to a query.
    Called by LLMWrapper.call() when knowledge_base_query is provided.
    """
    try:
        query_embedding = _generate_embeddings([query])[0]
        store = _get_vector_store()
        return store.search(query_embedding, top_k=top_k)
    except Exception as e:
        logger.error(f"[KB] Search failed: {e}")
        return []


def format_kb_context(results: List[KBSearchResult]) -> str:
    """Format search results into a context string for LLM prompts."""
    if not results:
        return ""
    sections = []
    for r in results:
        sections.append(f"[Source: {r.source} | Relevance: {r.score:.3f}]\n{r.content}")
    return "\n\n---\n\n".join(sections)
