import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import app
from fastapi.testclient import TestClient


LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch><law><법령명한글>국가를 당사자로 하는 계약에 관한 법률</법령명한글>
<법령ID>001234</법령ID><법령일련번호>123456</법령일련번호>
<현행연혁코드>현행</현행연혁코드><시행일자>20260611</시행일자></law></LawSearch>"""

ADMIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AdmRulSearch><admrul><행정규칙명>정부 입찰·계약 집행기준</행정규칙명>
<행정규칙일련번호>2100000276688</행정규칙일련번호><발령일자>20260401</발령일자>
<소관부처명>기획재정부</소관부처명></admrul></AdmRulSearch>"""


class KoreanLawFastApiTest(unittest.TestCase):
    def test_mcp_http_contract(self):
        client = TestClient(app.app)
        initialized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(initialized.status_code, 200)
        self.assertEqual(initialized.json()["result"]["protocolVersion"], app.MCP_PROTOCOL_VERSION)

        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        self.assertTrue({"search_law", "search_administrative_rule", "review_illegality"} <= names)

    def test_search_and_review_contract(self):
        async def run():
            async def fake_request(target, *_args, **_kwargs):
                return ADMIN_XML if target == "admrul" else LAW_XML

            with patch.object(app, "_law_request", new=AsyncMock(side_effect=fake_request)):
                law = await app.call_tool("search_law", {"query": "국가계약법", "display": 5, "apiKey": "test"})
                self.assertIn("국가를 당사자로 하는 계약에 관한 법률", law)

                reviewed = await app.call_tool(
                    "review_illegality",
                    {"text": "당사는 다른 입찰에 참가하지 않을 것을 확약한다.", "apiKey": "test"},
                )
                payload = json.loads(reviewed)
                self.assertEqual(payload["verdict"], "review_needed")
                self.assertEqual(payload["findings"][0]["id"], "other_bid_ban")
                self.assertTrue(payload["citations"][0]["verified"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
