"""Build the local chromatography knowledge base and Chroma index."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from sklearn.feature_extraction.text import HashingVectorizer


ROOT_DIR = Path(__file__).resolve().parents[1]
KB_DIR = ROOT_DIR / "knowledge_base"
DOCS_DIR = KB_DIR / "docs"
CHROMA_DIR = Path(
    os.getenv("RAG_CHROMA_DIR", "")
    or str(Path(os.getenv("TEMP", str(KB_DIR))) / "Chromatography_agent" / "chroma")
)
MANIFEST_PATH = KB_DIR / "sources_manifest.json"
COLLECTION_NAME = "chromatography_agent_kb"
PDF_ROOT = Path(r"C:\Users\13459\Documents\发表")
EMBEDDING_DIM = 512


KEYWORDS = [
    "chromatography", "色谱", "retention", "Rf", "TLC", "thin layer",
    "column chromatography", "enantioseparation", "HPLC", "separation",
    "machine learning", "automation", "GeoGNN", "retention time",
]


@dataclass(frozen=True)
class SourceSpec:
    doc_id: str
    title: str
    source_path: Path
    tags: tuple[str, ...]
    reuse: str
    max_pages: int = 4
    max_chars: int = 5000


def source_specs() -> list[SourceSpec]:
    return [
        SourceSpec(
            "PAPER-AI-CHROM-2026",
            "Automation and AI-Powered Prediction in Chromatographic Separation",
            PDF_ROOT / "825" / "Liu和Mo - 2026 - Automation and AI-Powered Prediction in Chromatographic Separation.pdf",
            ("chromatography", "automation", "AI prediction", "portfolio"),
            "Use as the high-level rationale for a chromatography agent that combines models, tools, and RAG.",
        ),
        SourceSpec(
            "PAPER-CC-2025",
            "Intelligent column chromatography prediction model based on automation and machine learning",
            PDF_ROOT / "335" / "Wu 等 - 2025 - Intelligent column chromatography prediction model based on automation and machine learning.pdf",
            ("column chromatography", "CC", "machine learning", "automation"),
            "Use as background for the CC prediction tool and purification workflow automation.",
        ),
        SourceSpec(
            "PAPER-TLC-CC-2025",
            "Explicit relation between thin film chromatography and column chromatography conditions",
            PDF_ROOT / "345" / "Xu 等 - 2025 - Explicit relation between thin film chromatography and column chromatography conditions from statist.pdf",
            ("TLC", "CC", "condition transfer", "separation"),
            "Use to explain how TLC signals can support column chromatography condition reasoning.",
        ),
        SourceSpec(
            "PAPER-TLC-AUTO-2022",
            "High-throughput automated platform for thin layer chromatography analysis",
            PDF_ROOT / "349" / "Xu 等 - 2022 - High-throughput automated platform for thin layer chromatography analysis.pdf",
            ("TLC", "automation", "Rf", "high-throughput"),
            "Use as background for TLC Rf prediction and automated experimental data collection.",
        ),
        SourceSpec(
            "PAPER-TLC-POLARITY-2022",
            "High-throughput discovery of chemical structure-polarity relationships",
            PDF_ROOT / "351" / "Xu 等 - 2022 - High-throughput discovery of chemical structure-polarity relationships combining automation and mach.pdf",
            ("TLC", "polarity", "structure-property", "machine learning"),
            "Use to connect molecular structure, polarity, and TLC/retention behavior.",
        ),
        SourceSpec(
            "PAPER-HPLC-QGEOGNN-2023",
            "Retention time prediction for chromatographic enantioseparation by quantile geometry-enhanced graph",
            PDF_ROOT / "245" / "Xu 等 - 2023 - Retention time prediction for chromatographic enantioseparation by quantile geometry-enhanced graph.pdf",
            ("HPLC", "enantioseparation", "GeoGNN", "retention time"),
            "Use as primary background for HPLC chiral retention-time prediction.",
        ),
        SourceSpec(
            "PAPER-GC-MM-2025",
            "Multimodal learning in synthetic chemistry applications gas chromatography retention time prediction",
            PDF_ROOT / "672" / "Lin 等 - 2025 - Multimodal learning in synthetic chemistry applications gas chromatography retention time predictio.pdf",
            ("GC", "retention time", "multimodal learning", "chemistry"),
            "Use as adjacent chromatography retention-time ML background.",
        ),
        SourceSpec(
            "PAPER-CHEM-AI-2023",
            "Transforming organic chemistry research paradigms",
            PDF_ROOT / "353" / "Liu 等 - 2023 - Transforming organic chemistry research paradigms Moving from manual efforts to the intersection of.pdf",
            ("chemistry AI", "automation", "research paradigm"),
            "Use as broad justification for AI-assisted chemistry workflows.",
            max_pages=3,
        ),
        SourceSpec(
            "PAPER-AI-SYNTH-2024",
            "AI for organic and polymer synthesis",
            PDF_ROOT / "343" / "Hong 等 - 2024 - AI for organic and polymer synthesis.pdf",
            ("AI chemistry", "synthesis", "review"),
            "Use as broad AI-for-chemistry background when explaining portfolio transferability.",
            max_pages=3,
        ),
        SourceSpec(
            "PAPER-INTELLIGENT-ALG-2024",
            "Empowering research in chemistry and materials science through intelligent algorithms",
            PDF_ROOT / "347" / "Lin和Mo - 2024 - Empowering research in chemistry and materials science through intelligent algorithms.pdf",
            ("intelligent algorithms", "chemistry", "materials"),
            "Use as context for departmental AI enablement and internal productivity scenarios.",
            max_pages=3,
        ),
        SourceSpec(
            "SERVICE-TLC-MODEL",
            "Local TLC Rf model service and dataset implementation",
            ROOT_DIR / "Services" / "ML-Prediction-for-Rf-values-main" / "README.md",
            ("project", "TLC", "Rf model", "service"),
            "Use as model card background for the local TLC tool.",
        ),
        SourceSpec(
            "SERVICE-CC-MODEL",
            "Local column chromatography GeoGNN service and dataset organization",
            ROOT_DIR / "Services" / "column_chromatography_prediction-main" / "README.md",
            ("project", "CC", "GeoGNN", "service"),
            "Use as model card background for the local CC tool.",
        ),
        SourceSpec(
            "SERVICE-CC-DATASET",
            "Column chromatography dataset organization",
            ROOT_DIR / "Services" / "column_chromatography_prediction-main" / "dataset" / "README.md",
            ("project", "CC", "dataset", "eluent"),
            "Use to explain CC dataset categories, columns, eluents, and column types.",
        ),
        SourceSpec(
            "SERVICE-HPLC-MODEL",
            "Local HPLC enantioseparation retention-time model service",
            ROOT_DIR / "Services" / "Retention-Time-Prediction-for-Chromatographic-Enantioseparation-main" / "README.md",
            ("project", "HPLC", "enantioseparation", "service"),
            "Use as model card background for the local HPLC tool.",
        ),
        SourceSpec(
            "PROJECT-AGENT-ARCHITECTURE",
            "Chromatography Agent architecture and migration framing",
            ROOT_DIR / "README.md",
            ("project", "agent", "tool calling", "migration"),
            "Use to answer portfolio, migration, and architecture questions.",
        ),
    ]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(path: Path, max_pages: int) -> str:
    if not path.exists():
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = reader.pages[:max_pages]
        text = "\n".join(page.extract_text() or "" for page in pages)
        if text.strip():
            return clean_text(text)
    except Exception:
        pass

    try:
        import pdfplumber

        chunks = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:max_pages]:
                chunks.append(page.extract_text() or "")
        return clean_text("\n".join(chunks))
    except Exception:
        return ""


def extract_text(path: Path, max_pages: int, max_chars: int) -> str:
    if path.suffix.lower() == ".pdf":
        text = extract_pdf_text(path, max_pages)
    elif path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
    else:
        text = ""
    return clean_text(text)[:max_chars]


def relevant_excerpt(text: str, limit: int = 2200) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    ranked = []
    for paragraph in paragraphs:
        lower = paragraph.lower()
        score = sum(1 for keyword in KEYWORDS if keyword.lower() in lower)
        if score:
            ranked.append((score, paragraph))
    selected = [p for _, p in sorted(ranked, key=lambda item: item[0], reverse=True)[:4]]
    if not selected:
        selected = paragraphs[:4]
    excerpt = "\n\n".join(selected)
    return clean_text(excerpt)[:limit]


def write_doc(spec: SourceSpec, text: str) -> dict:
    excerpt = relevant_excerpt(text)
    status = "ok" if excerpt else "source_missing_or_empty"
    body = f"""---
doc_id: {spec.doc_id}
title: {spec.title}
source_path: {spec.source_path}
tags: {", ".join(spec.tags)}
status: {status}
---

# {spec.title}

## Source
{spec.source_path}

## Why It Matters
{spec.reuse}

## Extracted Evidence
{excerpt or "No extractable text was found. Keep this source as metadata only."}

## Project Reuse
- Supports citation-backed answers in the chromatography Agent.
- Helps explain why tool calling, RAG, and structured prediction services are separated.
- Can be used in interview discussion about context engineering and token-efficient retrieval.
"""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    doc_path = DOCS_DIR / f"{spec.doc_id}.md"
    doc_path.write_text(body, encoding="utf-8")
    return {
        "doc_id": spec.doc_id,
        "title": spec.title,
        "source_path": str(spec.source_path),
        "doc_path": str(doc_path.relative_to(ROOT_DIR)),
        "tags": list(spec.tags),
        "status": status,
    }


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 140) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectorizer = HashingVectorizer(
        n_features=EMBEDDING_DIM,
        alternate_sign=False,
        norm="l2",
        ngram_range=(1, 2),
    )
    return vectorizer.transform(texts).toarray().astype(float).tolist()


def build_chroma(manifest: list[dict]) -> None:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.get_or_create_collection(COLLECTION_NAME)

    ids = []
    docs = []
    metadatas = []
    for item in manifest:
        doc_path = ROOT_DIR / item["doc_path"]
        text = doc_path.read_text(encoding="utf-8", errors="ignore")
        for index, chunk in enumerate(chunk_text(text)):
            chunk_id = f"{item['doc_id']}::chunk-{index + 1}"
            ids.append(chunk_id)
            docs.append(chunk)
            metadatas.append({
                "doc_id": item["doc_id"],
                "title": item["title"],
                "source_path": item["source_path"],
                "doc_path": item["doc_path"],
                "chunk_id": chunk_id,
                "tags": ", ".join(item["tags"]),
            })

    if docs:
        collection.add(
            ids=ids,
            documents=docs,
            metadatas=metadatas,
            embeddings=embed_texts(docs),
        )


def main() -> int:
    specs = source_specs()
    manifest = []
    for spec in specs:
        text = extract_text(spec.source_path, spec.max_pages, spec.max_chars)
        manifest.append(write_doc(spec, text))

    MANIFEST_PATH.write_text(
        json.dumps({"collection": COLLECTION_NAME, "sources": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_chroma(manifest)
    print(f"Built {len(manifest)} knowledge docs.")
    print(f"Manifest: {MANIFEST_PATH}")
    print(f"Chroma: {CHROMA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
