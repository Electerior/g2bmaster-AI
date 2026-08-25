// 나라장터 개찰결과 수집기 — REPL에 그대로 붙여넣어 사용한다.
// 전제: page 가 나라장터 "개찰결과분류조회" 화면(입찰 > 입찰개찰/낙찰 > 개찰결과분류조회)에 있을 것.
// 검증: 2026-08-20, 비로그인 상태에서 R26BK01687990 수집 성공.

async function readBidders(p) {
  return await p.evaluate(() => {
    const norm = s => (s || '').trim().replace(/\s+/g, ' ');
    // 업무구분별로 그리드가 분리돼 있다: grdGods(물품) grdSrvc(용역) grdCstn(공사) …
    // 행이 실제로 들어있는 그리드를 찾아서 헤더명으로 컬럼을 매핑한다.
    for (const t of document.querySelectorAll('table[id*="_body_table"]')) {
      const ths = [...t.querySelectorAll('th')].map(x => norm(x.innerText));
      if (!ths.some(h => h.includes('사업자등록번호'))) continue;
      const rows = [...t.querySelectorAll('tr')]
        .map(tr => [...tr.querySelectorAll('td')].map(td => norm(td.innerText)))
        .filter(r => r.length === ths.length && r.some(c => c));
      if (!rows.length) continue;
      const col = n => ths.findIndex(h => h.includes(n));
      const ci = {
        rank: col('순위'), bizno: col('사업자등록번호'), company: col('업체명'),
        ceo: col('대표자명'), amount: ths.findIndex(h => /입찰금액|투찰금액/.test(h)),
        rate: col('투찰률'), qty: col('수량'), lot: col('추첨번호'),
        at: col('투찰일시'), memo: col('비고'),
      };
      return {
        grid: t.id,
        bidders: rows.map(r => ({
          rank: +r[ci.rank] || null, bizno: r[ci.bizno] || null, company: r[ci.company] || null,
          ceo: r[ci.ceo] || null, amount: Number((r[ci.amount] || '').replace(/[^\d]/g, '')) || null,
          rate: parseFloat(r[ci.rate]) || null, qty: r[ci.qty] || null, lot: r[ci.lot] || null,
          bid_at: r[ci.at] || null, memo: r[ci.memo] || null,
        })),
      };
    }
    return { grid: null, bidders: [] };
  });
}

async function collectOutcome(p, bidNo) {
  // 1) 목록 화면 확보
  let s = await snapshot(p, { interactive: true });
  if (!/textbox "입찰공고번호"/.test(s.tree)) {
    const lst = s.tree.match(/button "목록" \[ref=(e\d+)\]/)?.[1];
    if (lst) { await p.locator(lst).click(); await sleep(2500); s = await snapshot(p, { interactive: true }); }
  }
  // 2) 공고번호 검색 (차수 '-000' 없이 넣는다. 공고번호를 넣으면 개찰일자 필터가 자동 비활성 = 기간 무제한)
  const nb = s.tree.match(/textbox "입찰공고번호" \[ref=(e\d+)\]/)[1];
  const btn = [...s.tree.matchAll(/button "검색" \[ref=(e\d+)\]/g)].map(m => m[1]).pop();
  await p.locator(nb).fill(bidNo);
  await p.locator(nb).press('Tab');
  await sleep(400);
  await p.locator(btn).click();
  await sleep(3000);

  const s2 = await snapshot(p, { interactive: true });
  const lines = s2.tree.split('\n');
  const idx = lines.findIndex(l => l.includes(bidNo) && /^- text:/.test(l.trim()));
  // 결과 없음 = 아직 개찰 전이거나 미집행. 오류가 아니라 정상적인 pending 상태다.
  if (idx < 0) return { bid_no: bidNo, status: 'pending', note: '개찰결과 미등록(개찰 전 또는 미집행)' };
  const stRef = (lines[idx + 1] || '').match(/button "([^"]+)" \[ref=(e\d+)\]/);
  if (!stRef) return { bid_no: bidNo, status: 'unknown', raw: lines[idx] };
  const [, statusText, statusRef] = stRef;

  // 3) 상세 (개찰완료/유찰 버튼 클릭)
  await p.locator(statusRef).click();
  await sleep(3200);
  // 값은 전부 <input> 안에 있다 — innerText 는 빈 문자열로 나온다
  const detail = await p.evaluate(() => {
    const val = t => {
      const el = [...document.querySelectorAll('input')].find(i => (i.title || '') === t);
      return el ? el.value.trim() : null;
    };
    return {
      ref_no: val('참조번호'), opened_at: val('실제개찰일시'), title: val('입찰공고명'),
      org: val('공고기관'), demand_org: val('수요기관'),
    };
  });
  const bd = await readBidders(p);

  // 4) 예정가격 ("복수예비가격 및 예정가격" 보기 → 팝업)
  let est = {};
  const s3 = await snapshot(p, { interactive: true });
  const view = s3.tree.match(/button "보기" \[ref=(e\d+)\]/)?.[1];
  if (view) {
    await p.locator(view).click();
    await sleep(2200);
    est = await p.evaluate(() => {
      const g = t => {
        const el = [...document.querySelectorAll('input')].find(i => (i.title || '') === t);
        return el ? Number(el.value.replace(/[^\d]/g, '')) || null : null;
      };
      return { est_price: g('예정가격'), base_price: g('기초금액') };
    });
    const s4 = await snapshot(p, { interactive: true });
    const close = s4.tree.match(/button "닫기" \[ref=(e\d+)\]/)?.[1];
    if (close) { await p.locator(close).click(); await sleep(1200); }
  }

  // 5) 목록 복귀 (다음 공고번호 조회를 위해 반드시)
  const s5 = await snapshot(p, { interactive: true });
  const back = s5.tree.match(/button "목록" \[ref=(e\d+)\]/)?.[1];
  if (back) { await p.locator(back).click(); await sleep(2500); }

  return {
    bid_no: bidNo, status: statusText, ...detail, ...est,
    grid: bd.grid, bidders: bd.bidders,
    collected_at: new Date().toISOString(),
  };
}

// 사용 예 ─────────────────────────────────────────────
// const nos = ['R26BK01685088', 'R26BK01684606'];
// const results = [];
// for (const n of nos) { results.push(await collectOutcome(page, n)); }
// await fs.writeFile('/tmp/outcomes_in.json', JSON.stringify(results, null, 1));
// → 이후 python outcome_tracker.py merge /tmp/outcomes_in.json
