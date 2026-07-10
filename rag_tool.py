"""Local Chroma RAG tool for chromatography knowledge lookup."""

from __future__ import annotations

from pathlib import Path
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import HashingVectorizer

from config import config
from tool_response import tool_response


EMBEDDING_DIM = 512


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _kb_dir() -> Path:
    path = Path(config.KNOWLEDGE_BASE_DIR)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def _chroma_dir() -> Path:
    path = Path(config.RAG_CHROMA_DIR)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Create deterministic local embeddings without downloading a model."""
    vectorizer = HashingVectorizer(
        n_features=EMBEDDING_DIM,
        alternate_sign=False,
        norm="l2",
        ngram_range=(1, 2),
    )
    return vectorizer.transform(texts).toarray().astype(float).tolist()


def expand_query(query: str) -> str:
    """Add compact bilingual domain terms for deterministic local retrieval."""
    expansions = {
        "柱色谱": "column chromatography CC silica column purification",
        "薄层": "thin layer chromatography TLC Rf",
        "TLC": "thin layer chromatography Rf",
        "HPLC": "high performance liquid chromatography enantioseparation retention time",
        "手性": "chiral enantioseparation",
        "保留时间": "retention time RT",
        "流动相": "mobile phase eluent",
        "洗脱剂": "eluent solvent system",
        "数据集": "dataset data organization",
        "健康": "health monitoring app platform",
        "上下文": "context engineering memory RAG token",
        "记忆": "memory context engineering",
        "苏仁": "health monitoring mat app health management platform",
    }
    extra = [value for key, value in expansions.items() if key.lower() in query.lower()]
    return f"{query} {' '.join(extra)}".strip()


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="Question or topic to search in the local chromatography knowledge base.")
    top_k: int = Field(default=4, description="Number of evidence chunks to return.")


class KnowledgeBaseSearchTool(BaseTool):
    name: str = "Knowledge_Base_Search"
    description: str = (
        "Search the local chromatography knowledge base. Use for literature, method background, "
        "project documentation, migration scenarios, context engineering, and citation-backed answers. "
        "Returns source IDs that must be cited in the final answer."
    )
    args_schema: Type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str, top_k: int = 4) -> str:
        kb_dir = _kb_dir()
        chroma_dir = _chroma_dir()
        top_k = max(1, min(int(top_k or config.RAG_TOP_K), 8))
        try:
            import chromadb

            if not chroma_dir.exists():
                return tool_response(
                    ok=False,
                    tool=self.name,
                    input_data={"query": query, "top_k": top_k},
                    error=(
                        f"Knowledge base index not found at {chroma_dir}. "
                        "Run knowledge_base/build_knowledge_base.py first."
                    ),
                )

            client = chromadb.PersistentClient(path=str(chroma_dir))
            collection = client.get_collection(config.RAG_COLLECTION_NAME)
            if collection.count() == 0:
                return tool_response(
                    ok=False,
                    tool=self.name,
                    input_data={"query": query, "top_k": top_k},
                    error="Knowledge base collection is empty.",
                )

            expanded_query = expand_query(query)
            response = collection.query(
                query_embeddings=embed_texts([expanded_query]),
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            documents = response.get("documents", [[]])[0]
            metadatas = response.get("metadatas", [[]])[0]
            distances = response.get("distances", [[]])[0]

            results = []
            sources = []
            max_chars = config.RAG_MAX_SNIPPET_CHARS
            for doc, meta, distance in zip(documents, metadatas, distances):
                snippet = " ".join(str(doc).split())
                if len(snippet) > max_chars:
                    snippet = snippet[:max_chars].rstrip() + "..."
                item = {
                    "doc_id": meta.get("doc_id", ""),
                    "title": meta.get("title", ""),
                    "source_path": meta.get("source_path", ""),
                    "chunk_id": meta.get("chunk_id", ""),
                    "score": round(1.0 / (1.0 + float(distance)), 4),
                    "snippet": snippet,
                }
                results.append(item)
                sources.append({
                    "doc_id": item["doc_id"],
                    "title": item["title"],
                    "source_path": item["source_path"],
                })

            return tool_response(
                ok=True,
                tool=self.name,
                input_data={"query": query, "top_k": top_k},
                result={"query": query, "results": results},
                summary=f"Found {len(results)} knowledge-base chunks for citation-backed answering.",
                sources=sources,
            )
        except Exception as exc:
            return tool_response(
                ok=False,
                tool=self.name,
                input_data={"query": query, "top_k": top_k},
                error=f"Knowledge base search failed: {exc}",
            )

    async def _arun(self, query: str, top_k: int = 4) -> str:
        return self._run(query, top_k)
