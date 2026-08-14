#!/usr/bin/env python3
"""
FastAPI server for Module A (문서 내용 임베딩·유사도 검색) and Module B (LLM Spec Extraction).
Runs on localhost:8001. 백엔드가 §J 하드웨어 스펙 표면을 이쪽으로 프록시한다.

제목 의미검색(`/api/search/titles`·`/api/rank/titles`)은 폐지했다. 제목은 신호가
너무 얇았다 — 공고 제목에는 정작 필요한 사양이 한 글자도 없고 그건 첨부 본문에만 있다.
이제 Module A 는 **파일 내용**만 다룬다(`module_a/document_index.py`).
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

from module_a import model_registry
from module_a.document_index import DocumentIndex
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


@app.on_event("startup")
async def _warm_embedding_model() -> None:
    """임베딩 가중치를 미리 읽어 둔다.

    예열하지 않으면 첫 요청이 모델 적재(실측 약 4초)를 혼자 뒤집어쓴다. 백그라운드로
    돌리는 이유는 기동을 막지 않기 위해서다 — 헬스체크가 그동안 응답해야 한다.
    """
    model_registry.warmup_async()


# === Request/Response Models ===

class ExtractRequest(BaseModel):
    """두 방향을 한 표면에서 받는다.

    - `mode="datasheet"` — 제조사 스펙 문서 → 제품 하나의 값. `spec_type` 필수.
    - `mode="requirement"` — 조달 규격서 → 요구 품목과 조건. `spec_type` 무시.
    """

    mode: Literal["datasheet", "requirement"] = "datasheet"
    spec_type: Optional[Literal["cpu", "gpu"]] = None
    chunks: List[str] = Field(min_length=1)
    model: Optional[str] = None
    backend: Literal["ollama", "lms", "custom"] = "lms"
    base_url: Optional[str] = None
    #: 문서에 여러 제품이 있을 때 뽑을 제품명. datasheet 방향에서만 쓴다.
    target: Optional[str] = None


class ExtractResponse(BaseModel):
    mode: Literal["datasheet", "requirement"] = "datasheet"
    cpu: Optional[dict] = None
    gpu: Optional[dict] = None
    #: requirement 방향의 결과. datasheet 방향이면 None 이다.
    items: Optional[List[dict]] = None
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
    # 프로세스가 살아 있는 것과 임베딩이 쓸 수 있는 것은 다른 질문이다.
    # 모델을 못 읽어도 status 는 ok 다 — 나머지 표면은 정상으로 돈다.
    return {"status": "ok", "modules": ["module_a", "module_b"], "embedding": model_registry.status()}


# === Module A: 문서 내용 임베딩·유사도 검색 ===

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
        vecs = model_registry.encode(req.texts)
        return {
            "model": model_registry.DEFAULT_MODEL,
            "dim": int(vecs.shape[1]),
            "vectors": [[float(x) for x in v] for v in vecs],
        }
    except model_registry.ModelUnavailable as error:
        # 모델이 없다는 것과 인코딩이 깨졌다는 것은 다른 사건이다. 503 이라야
        # 호출부가 "잠시 없음"으로 읽고 재시도할 수 있다.
        raise HTTPException(status_code=503, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="embed failed")


class ChunkRequest(BaseModel):
    """파일 내용을 청크로 잘라 벡터까지 만든다.

    자르기와 인코딩을 한 번에 하는 이유는 **좌표** 때문이다. 호출부가 따로 자르면
    각 청크가 원문 어디였는지를 잃고, 그러면 검색 결과를 근거로 인용할 수 없다.
    """

    text: str
    document_id: str = ""
    chunk_chars: int = Field(default=700, ge=100, le=4000)
    overlap_chars: int = Field(default=120, ge=0, le=1000)


@app.post("/api/embed/document")
async def embed_document(req: ChunkRequest):
    try:
        index = DocumentIndex()
        index.add_document(
            req.text,
            document_id=req.document_id,
            chunk_chars=req.chunk_chars,
            overlap_chars=req.overlap_chars,
        )
        vectors = index._vectors
        return {
            "model": model_registry.DEFAULT_MODEL,
            "dim": index.dim,
            "chunks": [
                {**chunk.as_dict(), "vector": [float(x) for x in vectors[i]]}
                for i, chunk in enumerate(index.chunks)
            ],
        }
    except model_registry.ModelUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="embed document failed")


class DocumentSearchRequest(BaseModel):
    query: str
    text: str
    document_id: str = ""
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=-1.0, le=1.0)


@app.post("/api/search/document")
async def search_document(req: DocumentSearchRequest):
    """문서 하나 안에서 질의와 가장 가까운 대목을 찾는다.

    색인은 이 요청 안에서만 산다 — 상주시키면 다른 요청이 넣은 문서가 섞여 나온다.
    """
    try:
        index = DocumentIndex()
        index.add_document(req.text, document_id=req.document_id)
        hits = index.search(req.query, top_k=req.top_k, min_score=req.min_score)
        return {"query": req.query, "chunks": len(index), "results": [h.as_dict() for h in hits]}
    except model_registry.ModelUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="search document failed")


# === Module B: LLM Spec Extraction ===

@app.post("/api/extract/specs", response_model=ExtractResponse)
async def extract_specs(req: ExtractRequest):
    # datasheet 방향은 어느 스키마로 제약할지 알아야 한다. 없으면 400 이 맞다 —
    # 임의로 "cpu" 를 골라 주면 GPU 문서에서 빈 결과가 나오고, 그게 "스펙 없음"으로 읽힌다.
    if req.mode == "datasheet" and req.spec_type is None:
        raise HTTPException(status_code=400, detail="spec_type is required when mode=datasheet")

    try:
        extractor = create_extractor(req.backend, req.base_url)
        model_name = req.model or "gemma-4-e2b-it-qat"

        if req.mode == "requirement":
            requirement = extractor.extract_requirements(req.chunks, model_name)
            items = [item.model_dump() for item in requirement.items]
            return ExtractResponse(
                mode="requirement",
                items=items,
                is_sufficient_data=bool(items),
            )

        result = extractor.extract(req.spec_type, req.chunks, model_name, target=req.target)
        return ExtractResponse(
            mode="datasheet",
            cpu=result.cpu.model_dump() if result.cpu else None,
            gpu=result.gpu.model_dump() if result.gpu else None,
            is_sufficient_data=result.is_sufficient_data
        )
    except HTTPException:
        raise
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
                        # 예전에는 청크마다 **질의를 다시 인코딩**했다(청크 5개면 5번).
                        # 질의는 하나뿐이니 한 번이면 된다 — DocumentIndex 가 그렇게 한다.
                        index = DocumentIndex()
                        index.add_document(spec_text, document_id=bid_ntce_no)
                        hits = index.search(req.query, top_k=1)
                        max_score = hits[0].score if hits else 0.0

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