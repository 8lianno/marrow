"""LanceDB vector store wrapper.

Schema mirrors the relevant ChunkRecord fields. Embedding column is fixed-size
float32 list of length DIM. Index creation deferred until M3+ when query
performance matters; for M2 we just write the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from marrow.schemas.chunk import ChunkRecord


class VectorStore:
    """LanceDB embedded table at runs/<slug>/02_chunk/vectors.lance/."""

    def __init__(self, table_dir: Path, dim: int) -> None:
        self.table_dir = table_dir
        self.dim = dim

    def write(self, chunks: list[ChunkRecord]) -> int:
        if not chunks:
            return 0
        try:
            import lancedb
            import pyarrow as pa
        except ImportError:
            return self._write_stub(chunks)

        self.table_dir.parent.mkdir(parents=True, exist_ok=True)
        # Connect to the parent directory; LanceDB names the table by file.
        db = lancedb.connect(str(self.table_dir.parent))
        records = [
            {
                "chunk_uuid": str(c.chunk_uuid),
                "text": c.text,
                "chapter_path": c.chapter_path,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "token_count": c.token_count,
                "embedding": c.embedding or [0.0] * self.dim,
            }
            for c in chunks
        ]
        schema = pa.schema(
            [
                pa.field("chunk_uuid", pa.string()),
                pa.field("text", pa.string()),
                pa.field("chapter_path", pa.list_(pa.string())),
                pa.field("page_start", pa.int32()),
                pa.field("page_end", pa.int32()),
                pa.field("token_count", pa.int32()),
                pa.field("embedding", pa.list_(pa.float32(), self.dim)),
            ]
        )
        table_name = self.table_dir.name.replace(".lance", "")
        if table_name in db.table_names():
            db.drop_table(table_name)
        db.create_table(table_name, data=records, schema=schema)
        return len(records)

    def _write_stub(self, chunks: list[ChunkRecord]) -> int:
        """Fallback when lancedb isn't installed: write a JSONL placeholder."""
        from marrow.io import write_jsonl

        placeholder = self.table_dir.with_suffix(".jsonl")
        write_jsonl(placeholder, chunks)
        return len(chunks)

    def search(self, query_vector: list[float], k: int = 10) -> list[dict[str, Any]]:
        import lancedb

        db = lancedb.connect(str(self.table_dir.parent))
        table_name = self.table_dir.name.replace(".lance", "")
        table = db.open_table(table_name)
        return table.search(query_vector).limit(k).to_list()

    def fetch_by_uuid(self, chunk_uuid: UUID) -> dict[str, Any] | None:
        import lancedb

        db = lancedb.connect(str(self.table_dir.parent))
        table_name = self.table_dir.name.replace(".lance", "")
        table = db.open_table(table_name)
        rows = table.search().where(f"chunk_uuid = '{chunk_uuid}'").limit(1).to_list()
        return rows[0] if rows else None
