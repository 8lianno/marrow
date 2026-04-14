"""Embedding backends. Implements late chunking via direct transformer access.

The Embedder protocol is the single seam for stage_02_chunk. Backends:

- `StubEmbedder`  — deterministic zero-vectors (CI-safe, no model download).
- `JinaLateChunkingEmbedder` — real Jina v2, late-chunked. For documents that
  fit Jina's 8192 token context, embeds the full doc in one forward pass and
  pools per chunk's token range. For longer documents, sliding window with
  25% overlap and merge-pool at boundaries.

Determinism: model.eval() + temperature N/A (encoder model). Floats can drift
across hardware; tests assert dim and shape, not exact values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marrow.chunking import PlannedChunk
from marrow.logging import get_logger

log = get_logger(__name__)

DEFAULT_DIM = 768  # Jina v2 base


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    def embed_chunks(self, doc_text: str, chunks: list[PlannedChunk]) -> list[list[float]]:
        """Return one embedding per planned chunk (in order)."""


@dataclass
class StubEmbedder:
    """Returns deterministic zero-vectors. Used in CI and when model unavailable."""

    dim: int = DEFAULT_DIM
    model_name: str = "stub"

    def embed_chunks(self, doc_text: str, chunks: list[PlannedChunk]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in chunks]


class JinaLateChunkingEmbedder:
    """Real Jina v2 with late chunking pooling.

    Lazy-loads the model on first embed call so tests not exercising the real
    path don't pay the load cost.
    """

    model_name = "jinaai/jina-embeddings-v2-base-en"
    max_seq_length = 8192
    dim = DEFAULT_DIM

    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModel, AutoTokenizer

        log.info("loading_jina_embeddings_v2", model=self.model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        self._model = AutoModel.from_pretrained(self.model_name, trust_remote_code=True)
        self._model.eval()

    def embed_chunks(self, doc_text: str, chunks: list[PlannedChunk]) -> list[list[float]]:
        if not chunks:
            return []
        self._ensure_loaded()

        import torch

        assert self._tokenizer is not None and self._model is not None

        # Locate each chunk's char span in doc_text. We rely on the chunk's
        # text being a verbatim substring (paragraph-aligned construction
        # guarantees this for the M2 chunk planner).
        spans: list[tuple[int, int]] = []
        cursor = 0
        for chunk in chunks:
            idx = doc_text.find(chunk.text, cursor)
            if idx < 0:
                idx = doc_text.find(chunk.text)  # retry without cursor
            if idx < 0:
                raise ValueError(f"Chunk text not found in doc_text (window {chunk.window_index})")
            spans.append((idx, idx + len(chunk.text)))
            cursor = idx

        # Single window if doc fits in context, else sliding windows with overlap.
        with torch.no_grad():
            all_token_embs = self._encode_with_windows(doc_text)

        offsets, token_embs = all_token_embs
        out: list[list[float]] = []
        for chunk, (char_start, char_end) in zip(chunks, spans, strict=True):
            mask = (
                (offsets[:, 0] >= char_start)
                & (offsets[:, 1] <= char_end)
                & (offsets[:, 1] > offsets[:, 0])  # exclude special tokens with (0,0) offsets
            )
            tokens_in_chunk = token_embs[mask]
            if tokens_in_chunk.shape[0] == 0:
                # Fallback: encode the chunk text alone.
                fallback = self._encode_text(chunk.text)
                out.append(fallback.tolist())
            else:
                pooled = tokens_in_chunk.mean(dim=0)
                out.append(pooled.tolist())
        return out

    def _encode_with_windows(self, text: str):
        """Tokenize + forward, returning (offsets, token_embeddings) in doc-char space.

        For text that fits one context window: single forward pass.
        For longer text: sliding window with 25% overlap; for each token position,
        average the embeddings from all windows that contain it.
        """
        import torch

        assert self._tokenizer is not None and self._model is not None

        full = self._tokenizer(
            text,
            return_tensors="pt",
            return_offsets_mapping=True,
            truncation=False,
            add_special_tokens=False,
        )
        full_ids = full["input_ids"][0]
        full_offsets = full["offset_mapping"][0]
        n = full_ids.shape[0]

        if n <= self.max_seq_length:
            inputs = self._tokenizer(
                text,
                return_tensors="pt",
                return_offsets_mapping=True,
                truncation=True,
                max_length=self.max_seq_length,
                add_special_tokens=True,
            )
            offsets = inputs.pop("offset_mapping")[0]
            outputs = self._model(**inputs)
            return offsets, outputs.last_hidden_state[0]

        # Sliding windows.
        window = self.max_seq_length
        stride = int(window * 0.75)  # 25% overlap
        emb_sum = torch.zeros((n, self.dim))
        emb_count = torch.zeros((n,))

        start = 0
        while start < n:
            end = min(start + window, n)
            window_ids = full_ids[start:end].unsqueeze(0)
            attention = torch.ones_like(window_ids)
            outputs = self._model(input_ids=window_ids, attention_mask=attention)
            hidden = outputs.last_hidden_state[0]  # [end-start, dim]
            emb_sum[start:end] += hidden
            emb_count[start:end] += 1
            if end == n:
                break
            start += stride

        token_embs = emb_sum / emb_count.unsqueeze(-1).clamp(min=1.0)
        return full_offsets, token_embs

    def _encode_text(self, text: str):
        import torch

        assert self._tokenizer is not None and self._model is not None
        with torch.no_grad():
            inputs = self._tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=self.max_seq_length,
            )
            outputs = self._model(**inputs)
            mask = inputs["attention_mask"].unsqueeze(-1).float()
            return (outputs.last_hidden_state * mask).sum(dim=1).squeeze(0) / mask.sum().clamp(
                min=1.0
            )


def get_embedder(model_name: str) -> Embedder:
    """Factory: 'stub' → StubEmbedder, anything else → JinaLateChunkingEmbedder."""
    if model_name == "stub" or not model_name:
        return StubEmbedder()
    return JinaLateChunkingEmbedder()
