"""Embedding providers.

* ``wordllama`` (default): WordLlama ``l2_supercat`` token-embedding model,
  256 dimensions, MIT licensed. Its weights ship inside the pip package, so
  it runs fully offline on CPU in a few milliseconds per chunk. It is a
  static (bag-of-tokens) model: strong on vocabulary overlap and synonyms,
  weaker on word order, which is one reason retrieval is hybrid.
* ``fastembed``: BAAI/bge-small-en-v1.5 (384 dimensions) through the
  fastembed ONNX runtime. Higher quality, but downloads ~130 MB from
  Hugging Face on first use and needs ``EMBEDDING_DIM=384`` before the
  database migration is run. Not exercised by the automated tests.

All vectors are L2-normalised, so cosine similarity equals the dot product
and pgvector's cosine distance (``<=>``) is used for search.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from app.config import get_settings
from app.core.errors import ServiceUnavailableError

logger = logging.getLogger("finsense.embeddings")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0) or not np.all(np.isfinite(vectors)):
        raise ServiceUnavailableError("The embedding model returned an invalid (zero or non-finite) vector.")
    return vectors / norms


class WordLlamaEmbedder:
    def __init__(self, config: str, dim: int) -> None:
        import wordllama
        from wordllama import WordLlama

        # The wheel bundles weights and tokenizer; point the loader at the
        # package directory and forbid downloads so startup never needs network.
        package_dir = Path(wordllama.__file__).parent
        self._model = WordLlama.load(config=config, dim=dim, cache_dir=package_dir, disable_download=True)
        self.name = f"wordllama/{config}-{dim}"
        self.dim = dim
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            vectors = self._model.embed(texts, norm=True)
        return _normalise(vectors)


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: Path) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ServiceUnavailableError(
                "EMBEDDING_PROVIDER=fastembed requires `pip install fastembed`."
            ) from exc
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        self.name = f"fastembed/{model_name}"
        self.dim = int(next(iter(self._model.embed(["dimension probe"]))).shape[0])

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalise(np.stack(list(self._model.embed(texts))))


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    try:
        if settings.embedding_provider == "wordllama":
            embedder: Embedder = WordLlamaEmbedder(settings.embedding_model, settings.embedding_dim)
        else:
            model = settings.embedding_model if "/" in settings.embedding_model else "BAAI/bge-small-en-v1.5"
            embedder = FastEmbedEmbedder(model, settings.cache_dir / "fastembed")
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        logger.exception("Embedding model failed to load")
        raise ServiceUnavailableError("The embedding model could not be loaded.") from exc
    if embedder.dim != settings.embedding_dim:
        raise ServiceUnavailableError(
            f"The embedding model produces {embedder.dim}-dimensional vectors but EMBEDDING_DIM is "
            f"{settings.embedding_dim}. Set EMBEDDING_DIM to match, create a new database (or migration) "
            "and re-index all documents."
        )
    logger.info("Loaded embedding model %s (%d dims)", embedder.name, embedder.dim)
    return embedder
