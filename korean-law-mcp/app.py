"""FastAPI subset of chrisryugj/korean-law-mcp used by g2bmastersopen."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote

import httpx
from defusedxml import ElementTree as ET
from fastapi import FastAPI, Header, HTTPException, Response

VERSION = "1.0.0"
MCP_PROTOCOL_VERSION = "2025-06-18"
LAW_API_BASE = os.getenv("LAW_API_BASE", "https://www.law.go.kr/DRF").rstrip("/")
LAW_REFERER = os.getenv("LAW_REFERER", "https://www.law.go.kr/")

app = FastAPI(
    title="Korean Law MCP FastAPI",
    version=VERSION,
    description="g2bmastersopen에 필요한 법령·행정규칙 검색 및 확약서 위험 신호 검토",
)

TOOLS = [
    {
        "name": "search_law",
        "description": "국가법령정보센터에서 현행 법령명을 검색합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "display": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "apiKey": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_administrative_rule",
        "description": "국가법령정보센터에서 행정규칙을 검색합니다. search_admin_rule의 호환 별칭입니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "display": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "knd": {"type": "string"},
                "apiKey": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_admin_rule",
        "description": "국가법령정보센터에서 행정규칙을 검색합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "display": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "knd": {"type": "string"},
                "apiKey": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "review_illegality",
        "description": "확약서의 경쟁 제한·부당특약 위험 신호를 관련 법령 검색 결과와 함께 반환합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "context": {"type": "string"},
                "apiKey": {"type": "string"},
            },
            "required": ["text"],
        },
    },
]

RISK_RULES = [
    (
        "exclusive_supply",
        "제조사·공급사 독점 조건",
        re.compile(r"(특정|해당)\s*(제조사|공급사).{0,80}(독점|단독|유일)|타\s*(제조사|공급사).{0,40}(배제|금지)", re.I | re.S),
        "특정 업체만 참여·공급할 수 있도록 하는 조건은 경쟁 제한 및 부당특약 가능성을 확인해야 합니다.",
    ),
    (
        "other_bid_ban",
        "다른 입찰 참가 제한",
        re.compile(r"(타|다른|유사)\s*(입찰|공고|사업).{0,60}(참가하지|응찰하지|제안하지|참가|응찰|제안).{0,30}(금지|제한|불가|않)|중복\s*(입찰|참가).{0,20}(금지|제한)", re.I | re.S),
        "해당 계약 이행과 무관하게 다른 입찰 참가까지 제한하면 필요한 범위를 넘는 경쟁 제한인지 확인해야 합니다.",
    ),
    (
        "blanket_waiver",
        "포괄적 권리 포기",
        re.compile(r"(일체의?\s*(이의|민원|소송|청구).{0,30}(제기하지|포기)|어떠한\s*이의도\s*제기하지)", re.I | re.S),
        "계약상대자의 권리를 포괄적으로 포기시키는 조항은 계약상 이익을 부당하게 제한하는지 확인해야 합니다.",
    ),
]

LAW_BASES = [
    {
        "law": "국가를 당사자로 하는 계약에 관한 법률",
        "article": "제5조제3항·제4항",
        "heading": "계약의 원칙·부당한 특약등",
        "url": "https://www.law.go.kr/법령/국가를당사자로하는계약에관한법률/제5조",
    },
    {
        "law": "지방자치단체를 당사자로 하는 계약에 관한 법률",
        "article": "제6조제3항",
        "heading": "계약의 원칙·부당한 특약등",
        "url": "https://www.law.go.kr/법령/지방자치단체를당사자로하는계약에관한법률/제6조",
    },
]


def _api_key(explicit: str = "", header_key: str = "") -> str:
    key = explicit or header_key or os.getenv("LAW_OC") or os.getenv("KOREAN_LAW_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="LAW_OC가 필요합니다. 국가법령정보 공동활용에서 OC 키를 발급받아 설정하세요.")
    return key


def _nodes(xml_text: str, tag: str) -> list[ET.Element]:
    try:
        return list(ET.fromstring(xml_text).iter(tag))
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail=f"국가법령정보 응답 XML 파싱 실패: {exc}") from exc


def _value(node: ET.Element, tag: str) -> str:
    child = node.find(f".//{tag}")
    return "".join(child.itertext()).strip() if child is not None else ""


async def _law_request(target: str, query: str, display: int, api_key: str, knd: str = "") -> str:
    params = {
        "OC": api_key,
        "type": "XML",
        "target": target,
        "query": query,
        "display": str(max(1, min(display, 100))),
    }
    if knd:
        params["knd"] = knd
    headers = {"Referer": LAW_REFERER, "User-Agent": "g2bmastersopen-korean-law-fastapi/1.0"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.get(f"{LAW_API_BASE}/lawSearch.do", params=params)
                response.raise_for_status()
                if not response.text.strip() or "<html" in response.text.lower():
                    raise ValueError("빈 응답 또는 HTML 오류 페이지")
                return response.text
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2**attempt))
        raise HTTPException(status_code=502, detail=f"국가법령정보 검색 실패: {last_error}")


def _law_fallback_queries(query: str) -> list[str]:
    if re.search(r"확약|입찰|경쟁|부당\s*특약", query):
        return [
            "국가를 당사자로 하는 계약에 관한 법률",
            "지방자치단체를 당사자로 하는 계약에 관한 법률",
        ]
    return [query]


def _admin_fallback_queries(query: str) -> list[str]:
    if re.search(r"확약|입찰|경쟁|부당\s*특약", query):
        return ["정부 입찰·계약 집행기준", "지방자치단체 입찰 및 계약집행기준"]
    return [query]


async def search_law(query: str, display: int, api_key: str) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for resolved in _law_fallback_queries(query):
        xml_text = await _law_request("law", resolved, display, api_key)
        for node in _nodes(xml_text, "law"):
            name = _value(node, "법령명한글") or "알 수 없음"
            mst = _value(node, "법령일련번호")
            key = mst or name
            if key in seen:
                continue
            seen.add(key)
            status = _value(node, "현행연혁코드")
            law_id = _value(node, "법령ID")
            effective = _value(node, "시행일자")
            url = f"https://www.law.go.kr/법령/{quote(name)}"
            rows.append(f"{len(rows) + 1}. {name} [{status or '상태 미표시'}]\n   - 법령ID: {law_id}\n   - MST: {mst}\n   - 시행일: {effective}\n   - URL: {url}")
            if len(rows) >= display:
                break
        if len(rows) >= display:
            break
    return f"법령 검색어: {query}\n\n" + ("\n\n".join(rows) if rows else "[NOT_FOUND] 관련 법령을 찾지 못했습니다.")


async def search_admin_rule(query: str, display: int, api_key: str, knd: str = "") -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for resolved in _admin_fallback_queries(query):
        xml_text = await _law_request("admrul", resolved, display, api_key, knd)
        for node in _nodes(xml_text, "admrul"):
            name = _value(node, "행정규칙명") or "알 수 없음"
            seq = _value(node, "행정규칙일련번호")
            key = seq or name
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                f"{len(rows) + 1}. {name}\n"
                f"   - 행정규칙일련번호: {seq}\n"
                f"   - 발령일자: {_value(node, '발령일자')}\n"
                f"   - 소관부처: {_value(node, '소관부처명')}"
            )
            if len(rows) >= display:
                break
        if len(rows) >= display:
            break
    return f"행정규칙 검색어: {query}\n\n" + ("\n\n".join(rows) if rows else "[NOT_FOUND] 관련 행정규칙을 찾지 못했습니다.")


def _excerpt(text: str, start: int, length: int, radius: int = 140) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - radius): min(len(text), start + length + radius)]).strip()


async def review_illegality(text: str, context: str, api_key: str) -> dict[str, Any]:
    findings = []
    for rule_id, title, pattern, reason in RISK_RULES:
        match = pattern.search(text)
        if not match:
            continue
        findings.append(
            {
                "id": rule_id,
                "title": title,
                "category": "competition_or_unfair_term",
                "severity": "high",
                "verdict": "review_needed",
                "reason": reason,
                "counterpoint": "계약 목적 달성에 객관적으로 필요한 최소 범위이고 관련 법령상 근거가 있으면 허용될 수 있습니다.",
                "excerpt": _excerpt(text, match.start(), len(match.group(0))),
                "basis": [{**basis, "verified": False} for basis in LAW_BASES],
            }
        )
    law_text, admin_text = await asyncio.gather(
        search_law("입찰 경쟁 부당 특약", 5, api_key),
        search_admin_rule("입찰 경쟁 부당 특약", 5, api_key),
    )
    verified = "[NOT_FOUND]" not in law_text
    for finding in findings:
        for basis in finding["basis"]:
            basis["verified"] = verified
    verdict = "review_needed" if findings else "no_finding"
    return {
        "verdict": verdict,
        "engine": "fastapi-rules+law-api",
        "model": "",
        "engineNote": "규칙은 위험 신호만 선별하며 최종 위법 판단을 확정하지 않습니다.",
        "summary": f"{len(findings)}개 위험 신호를 찾았습니다." if findings else "등록된 경쟁 제한·부당특약 위험 신호를 찾지 못했습니다.",
        "findings": findings,
        "citations": [{**basis, "verified": verified} for basis in LAW_BASES],
        "sources": {"laws": law_text, "administrativeRules": admin_text},
        "context": context[:500],
        "disclaimer": "자동 검토 결과이며 법률 자문이 아닙니다. 적용 법령과 사실관계는 담당자 또는 법률 전문가가 확인해야 합니다.",
    }


async def call_tool(name: str, arguments: dict[str, Any], header_key: str = "") -> str:
    api_key = _api_key(str(arguments.get("apiKey") or ""), header_key)
    query = str(arguments.get("query") or "").strip()
    display = max(1, min(int(arguments.get("display") or 20), 100))
    if name == "search_law":
        if not query:
            raise HTTPException(status_code=422, detail="query가 필요합니다.")
        return await search_law(query, display, api_key)
    if name in {"search_admin_rule", "search_administrative_rule"}:
        if not query:
            raise HTTPException(status_code=422, detail="query가 필요합니다.")
        return await search_admin_rule(query, display, api_key, str(arguments.get("knd") or ""))
    if name == "review_illegality":
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text가 필요합니다.")
        result = await review_illegality(text[:50000], str(arguments.get("context") or ""), api_key)
        return json.dumps(result, ensure_ascii=False)
    raise HTTPException(status_code=404, detail=f"지원하지 않는 도구: {name}")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "korean-law-mcp-fastapi", "version": VERSION, "lawOcConfigured": bool(os.getenv("LAW_OC") or os.getenv("KOREAN_LAW_API_KEY"))}


@app.post("/tools/{name}")
async def rest_tool(name: str, body: dict[str, Any], x_law_oc: str = Header(default="")) -> dict[str, str]:
    return {"text": await call_tool(name, body, x_law_oc)}


@app.post("/mcp")
async def mcp(body: dict[str, Any], response: Response, x_law_oc: str = Header(default="")) -> Any:
    request_id = body.get("id")
    method = body.get("method")
    if method == "notifications/initialized":
        response.status_code = 202
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "korean-law-fastapi", "version": VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = body.get("params") or {}
        try:
            text = await call_tool(str(params.get("name") or ""), params.get("arguments") or {}, x_law_oc)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}}
        except HTTPException as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"isError": True, "content": [{"type": "text", "text": str(exc.detail)}]},
            }
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"지원하지 않는 method: {method}"}}
