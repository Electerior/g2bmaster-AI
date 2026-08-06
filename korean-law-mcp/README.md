# Korean Law MCP FastAPI

이 디렉터리는 [chrisryugj/korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp)의 HTTP MCP 계약을 참고해, `g2bmastersopen` 확약서 흐름에 필요한 도구만 FastAPI로 내장한 구현입니다.

지원 도구:

- `search_law`
- `search_admin_rule`
- `search_administrative_rule` — 기존 앱 호환 별칭
- `review_illegality` — 경쟁 제한·부당특약 위험 신호 선별

```powershell
python -m venv .venv-law
.\.venv-law\Scripts\python -m pip install -r korean-law-mcp\requirements.txt
$env:LAW_OC='국가법령정보 OC 키'
.\.venv-law\Scripts\python -m uvicorn app:app --app-dir korean-law-mcp --host 127.0.0.1 --port 8000
```

웹앱 설정:

```dotenv
LAW_MCP_ENABLED=1
LAW_MCP_URL=http://127.0.0.1:8000/mcp
LAW_OC=...
```

사용자 설정에 따라 법률 검토를 켜거나 끌 수 있습니다.

```powershell
# 저장소에서 실행
npm run law-mcp -- --on
npm run law-mcp -- --off
npm run law-mcp -- --status

# npm link 후 전역 명령으로 실행
law-mcp --on
law-mcp --off
law-mcp --status
```

명령은 저장소 루트 `.env`의 `LAW_MCP_ENABLED` 값만 변경합니다. 실행 중인 Node 서버에는 환경변수가 이미 로드되어 있으므로 설정 변경 후 서버를 재시작해야 합니다.

Swagger UI는 `http://127.0.0.1:8000/docs`, 상태 확인은 `/healthz`에서 제공됩니다.

## 범위와 출처

전체 upstream 42개 API를 Python으로 복제하지 않고 이 애플리케이션이 사용하는 도구 경계만 구현했습니다. 검색 필드와 MCP 응답 규약은 upstream v4.9.1, commit `860cfcbce9c01c664766ec1badca8d4468b87488`을 기준으로 했습니다.

Upstream은 MIT License이며 저작권은 Chris에게 있습니다. 이 구현도 원 저작권과 허가 고지를 유지합니다. 법령 데이터는 국가법령정보 공동활용 API에서 실시간 조회합니다.
