#!/usr/bin/env python3
"""
FastAPI server for Module A (Semantic Title Search) and Module B (LLM Spec Extraction).
Runs on localhost:8001 and proxies from the main Node.js server.
"""

import os
import sys
import urllib.parse
import re
from pathlib import Path
from typing import List, Optional, Literal, Dict, Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request, Depends
from pydantic import BaseModel, Field, validator
import uvicorn

from module_a.data_loader import SearcherManager
from module_b.llm_extractor import create_extractor
from module_b.data_loader import load_cpu_specs, load_gpu_specs
from module_b.hardware_schema import HardwareExtraction

# Authentication: Require internal secret header for all endpoints
def verify_internal_secret(request: Request):
    secret = request.headers.get('X-Internal-Secret')
    expected = os.getenv('INTERNAL_SECRET')
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

# Apply to all routes
app_dependencies = [Depends(verify_internal_secret)]

app = FastAPI(title="Module A & B API", version="1.0.0", dependencies=app_dependencies)


_search_manager: Optional[SearcherManager] = None


def get_search_manager() -> SearcherManager:
    global _search_manager
    if _search_manager is None:
        cpu_csv = ROOT / "cpu_specs_complete_new.csv"
        gpu_csv = ROOT / "gpu_specs_sanitized.csv"
        _search_manager = SearcherManager(cpu_csv, gpu_csv)
    return _search_manager


# === Request/Response Models ===

class SearchRequest(BaseModel):
    query: str
    type: Literal["cpu", "gpu", "both"] = "both"
    top_k: int = Field(default=5, ge=1, le=50)
    # hybrid = dense + BM25 정규화 점수 융합(기본). 나머지는 비교·진단용.
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"


class SearchResult(BaseModel):
    title: str
    score: float


class SearchResponse(BaseModel):
    results: List[SearchResult]
    query: str
    type: str


class ExtractRequest(BaseModel):
    spec_type: Literal["cpu", "gpu"]
    chunks: List[str] = Field(min_length=1)
    model: Optional[str] = None
    backend: Literal["ollama", "lms", "custom"] = "lms"
    base_url: Optional[str] = None


class ExtractResponse(BaseModel):
    cpu: Optional[dict] = None
    gpu: Optional[dict] = None
    is_sufficient_data: bool


class SpecsResponse(BaseModel):
    specs: List[dict]


# === Document-based Search Models ===

class FetchNoticesRequest(BaseModel):
    from_date: str
    to_date: str
    category: Literal["all", "물품", "용역", "공사"] = "물품"
    keywords: Optional[List[str]] = None
    page_no: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)


class NoticeItem(BaseModel):
    bidNtceNo: str
    bidNtceNm: str
    ntceInsttNm: str
    bidNtceDt: str
    bidClseDt: str
    presmptPrce: Optional[int] = None
    _source: str = "unknown"


class FetchNoticesResponse(BaseModel):
    notices: List[NoticeItem]
    total_count: int
    page_no: int
    per_page: int


class SpecDocumentSearchRequest(BaseModel):
    query: str
    from_date: str
    to_date: str
    category: Literal["all", "물품", "용역", "공사"] = "물품"
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    top_k: int = Field(default=10, ge=1, le=50)


class SpecDocumentResult(BaseModel):
    bidNtceNo: str
    bidNtceNm: str
    ntceInsttNm: str
    bidNtceDt: str
    bidClseDt: str
    presmptPrce: Optional[int] = None
    spec_text: str
    similarity_score: float
    source: str


class SpecDocumentSearchResponse(BaseModel):
    results: List[SpecDocumentResult]
    query: str
    total_notices_searched: int
    total_specs_found: int
    from_date: str
    to_date: str
    category: str


# === Helper functions ===

async def fetch_notices_from_g2b(from_date: str, to_date: str, category: str, keywords: Optional[List[str]] = None, page_no: int = 1, per_page: int = 50) -> dict:
    """Fetch notices from G2B API via the Node.js server proxy."""
    import httpx

    g2b_from = from_date.replace("-", "")
    g2b_to = to_date.replace("-", "")

    bid_type_map = {"물품": "01", "용역": "03", "공사": "02"}
    bid_type = bid_type_map.get(category, "01") if category != "all" else ""

    params = {
        "fromDate": g2b_from,
        "toDate": g2b_to,
        "pageNo": page_no,
        "numOfRows": per_page,
    }
    if bid_type:
        params["bidType"] = bid_type
    if keywords:
        params["andTerms"] = ",".join(keywords)

    base_url = os.environ.get("MAIN_SERVER_URL", "http://localhost:3000")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{base_url}/api/bid-announce"
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()

            url = f"{base_url}/api/bid-plan"
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
        return {"items": [], "totalCount": 0}
    except Exception:
        return {"items": [], "totalCount": 0}


async def fetch_spec_document(bid_ntce_no: str, bid_ntce_sq_no: str = "") -> str:
    """Fetch spec document text for a notice."""
    import httpx

    base_url = os.environ.get("MAIN_SERVER_URL", "http://localhost:3000")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/api/extract-spec-text",
                json={"bidNtceNo": bid_ntce_no, "bidNtceSqNo": bid_ntce_sq_no}
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("text", "")
    except Exception:
        pass
    return ""


@app.get("/health")
async def health():
    return {"status": "ok", "modules": ["module_a", "module_b"]}


# === Module A: Semantic Title Search ===

class EmbedRequest(BaseModel):
    texts: List[str]


@app.post("/api/embed")
async def embed_texts(req: EmbedRequest):
    """텍스트를 임베딩 벡터로. 적재 때 문서 쪽을, 조회 때 질의문 하나를 인코딩한다.

    문서 임베딩을 DB 에 저장해 두면 조회할 때마다 코퍼스를 다시 인코딩하지 않아도 된다
    (인코딩이 유사도 검색 비용의 대부분이다).
    """
    if not req.texts:
        return {"model": "", "dim": 0, "vectors": []}
    try:
        from module_a.title_semantic_searcher import TitleSemanticSearcher
        s = TitleSemanticSearcher()
        vecs = s.model.encode(req.texts, show_progress_bar=False, normalize_embeddings=True)
        return {
            "model": s.model_name,
            "dim": int(vecs.shape[1]),
            "vectors": [[float(x) for x in v] for v in vecs],
        }
    except Exception:
        raise HTTPException(status_code=500, detail="embed failed")


class RankRequest(BaseModel):
    query: str
    titles: List[str]
    top_k: int = Field(default=50, ge=1, le=500)
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"


@app.post("/api/rank/titles")
async def rank_titles(req: RankRequest):
    """임의의 제목 목록을 질의와의 유사도로 정렬한다.

    /api/search/titles 는 CSV 로 미리 만든 색인을 쓰지만, 공고 검색은 매번 다른
    결과 집합을 다루므로 그때그때 색인을 세운다. 임베딩 인코딩이 비용의 대부분이라
    목록이 길면 느리다 — 호출부에서 상한을 둔다.
    """
    if not req.titles:
        return {"results": []}
    try:
        from module_a.title_semantic_searcher import TitleSemanticSearcher
        searcher = TitleSemanticSearcher()
        searcher.build_index(req.titles)
        hits = searcher.search(req.query, top_k=min(req.top_k, len(req.titles)), mode=req.mode)
        # 원본 인덱스를 함께 돌려준다 — 호출부는 제목이 아니라 공고 객체를 재정렬해야 한다.
        pos = {}
        for i, t in enumerate(req.titles):
            pos.setdefault(t.strip(), i)
        return {"results": [
            {"index": pos.get(title, -1), "title": title, "score": float(score)}
            for title, score in hits
        ]}
    except Exception:
        raise HTTPException(status_code=500, detail="rank failed")


@app.post("/api/search/titles", response_model=SearchResponse)
async def search_titles(req: SearchRequest):
    try:
        manager = get_search_manager()
        if req.type == "cpu":
            searcher = manager.cpu_searcher
        elif req.type == "gpu":
            searcher = manager.gpu_searcher
        else:
            searcher = manager.combined_searcher

        results = searcher.search(req.query, req.top_k, mode=req.mode)
        return SearchResponse(
            results=[SearchResult(title=t, score=s) for t, s in results],
            query=req.query,
            type=req.type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# === Module B: LLM Spec Extraction ===

@app.post("/api/extract/specs", response_model=ExtractResponse)
async def extract_specs(req: ExtractRequest):
    try:
        extractor = create_extractor(req.backend, req.base_url)
        model_name = req.model or "gemma-4-e2b-it-qat"
        result = extractor.extract(req.spec_type, req.chunks, model_name)

        return ExtractResponse(
            cpu=result.cpu.model_dump() if result.cpu else None,
            gpu=result.gpu.model_dump() if result.gpu else None,
            is_sufficient_data=result.is_sufficient_data
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# === Module B: Full Specs from CSV ===

@app.get("/api/specs/cpu", response_model=SpecsResponse)
async def get_cpu_specs():
    try:
        cpu_csv = ROOT / "cpu_specs_complete_new.csv"
        specs = load_cpu_specs(cpu_csv)
        return SpecsResponse(specs=[s.model_dump() for s in specs])
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/specs/gpu", response_model=SpecsResponse)
async def get_gpu_specs():
    try:
        gpu_csv = ROOT / "gpu_specs_sanitized.csv"
        specs = load_gpu_specs(gpu_csv)
        return SpecsResponse(specs=[s.model_dump() for s in specs])
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# === Document-based Spec Search Endpoints ===

@app.post("/api/specs/fetch-notices", response_model=FetchNoticesResponse)
async def fetch_notices(req: FetchNoticesRequest):
    """Fetch notices in date range and category for spec document search."""
    try:
        data = await fetch_notices_from_g2b(
            req.from_date, req.to_date, req.category,
            req.keywords, req.page_no, req.per_page
        )
        items = data.get("items", [])
        total = data.get("totalCount", 0)

        notices = []
        for item in items:
            notices.append(NoticeItem(
                bidNtceNo=item.get("bidNtceNo", ""),
                bidNtceNm=item.get("bidNtceNm", ""),
                ntceInsttNm=item.get("ntceInsttNm", ""),
                bidNtceDt=item.get("bidNtceDt", ""),
                bidClseDt=item.get("bidClseDt", ""),
                presmptPrce=item.get("presmptPrce"),
                _source=item.get("_source", "unknown"),
            ))

        return FetchNoticesResponse(
            notices=notices,
            total_count=total,
            page_no=req.page_no,
            per_page=req.per_page,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/specs/search-documents", response_model=SpecDocumentSearchResponse)
async def search_spec_documents(req: SpecDocumentSearchRequest):
    """Search hardware specs within spec documents of notices in date range."""
    try:
        data = await fetch_notices_from_g2b(
            req.from_date, req.to_date, req.category, None, 1, 200
        )
        items = data.get("items", [])
        total_notices = len(items)

        if not items:
            return SpecDocumentSearchResponse(
                results=[],
                query=req.query,
                total_notices_searched=0,
                total_specs_found=0,
                from_date=req.from_date,
                to_date=req.to_date,
                category=req.category,
            )

        spec_results = []
        specs_found = 0

        manager = get_search_manager()
        combined_searcher = manager.combined_searcher

        batch_size = 10
        for i in range(0, min(len(items), 50), batch_size):
            batch = items[i:i+batch_size]

            for item in batch:
                bid_ntce_no = item.get("bidNtceNo", "")
                bid_ntce_sq_no = item.get("bidNtceSqNo", "")

                spec_text = await fetch_spec_document(bid_ntce_no, bid_ntce_sq_no)

                if spec_text and len(spec_text) > 50:
                    specs_found += 1
                    try:
                        chunks = [spec_text[j:j+500] for j in range(0, len(spec_text), 400)]
                        max_score = 0
                        for chunk in chunks[:5]:
                            if len(chunk) > 20:
                                query_emb = combined_searcher.model.encode([req.query], normalize_embeddings=True)
                                chunk_emb = combined_searcher.model.encode([chunk], normalize_embeddings=True)
                                score = float((query_emb @ chunk_emb.T).flatten()[0])
                                max_score = max(max_score, score)

                        if max_score >= req.min_score:
                            spec_results.append(SpecDocumentResult(
                                bidNtceNo=bid_ntce_no,
                                bidNtceNm=item.get("bidNtceNm", ""),
                                ntceInsttNm=item.get("ntceInsttNm", ""),
                                bidNtceDt=item.get("bidNtceDt", ""),
                                bidClseDt=item.get("bidClseDt", ""),
                                presmptPrce=item.get("presmptPrce"),
                                spec_text=spec_text[:1000],
                                similarity_score=max_score,
                                source=item.get("_source", "unknown"),
                            ))
                    except Exception:
                        continue

        spec_results.sort(key=lambda x: x.similarity_score, reverse=True)
        spec_results = spec_results[:req.top_k]

        return SpecDocumentSearchResponse(
            results=spec_results,
            query=req.query,
            total_notices_searched=total_notices,
            total_specs_found=specs_found,
            from_date=req.from_date,
            to_date=req.to_date,
            category=req.category,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    port = int(os.environ.get("MODULE_SERVER_PORT", 8001))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")