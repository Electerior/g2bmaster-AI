#!/usr/bin/env python3
"""독소조항 스캔: python risk_scan.py SPECDIR 공고번호 [키워드길이]"""
import sys, re, os
PATTERNS = {
 '지체상금': r'지체상금[^\n]{0,120}',
 '하자보증': r'하자[보담][증보][^\n]{0,110}',
 '하도급/공동': r'(하도급|공동수급|공동계약|재하도급)[^\n]{0,100}',
 '정품/채널': r'(정품공급확약|정품확인|정품증명|총판|공급자 ?확약|기술지원확약)[^\n]{0,100}',
 '단종': r'단종[^\n]{0,110}',
 '반출금지': r'(미반출|반출[^\n]{0,20}(불가|금지)|저장매체)[^\n]{0,100}',
 '보안': r'(보안각서|신원[^\n]{0,10}확인|보안위규|위약금|보안 ?서약)[^\n]{0,100}',
 '보증금': r'(입찰보증금|계약이행보증|하자보수보증금)[^\n]{0,100}',
 '실적/지역': r'(실적[^\n]{0,10}제한|소재지|본점[^\n]{0,20}소재|지역제한)[^\n]{0,100}',
 'SLA/방문': r'(\d+ ?시간 ?이내|\d+일 ?이내[^\n]{0,15}(방문|도착|조치)|현장 ?방문)[^\n]{0,90}',
 '교육': r'(교육[^\n]{0,10}(실시|지원|훈련)|사용자 ?교육)[^\n]{0,90}',
 '이관/철거': r'(이관|철거|폐기|재구성)[^\n]{0,100}',
 '선금/대금': r'(선금|기성|대가[^\n]{0,10}지급|대금[^\n]{0,10}지급)[^\n]{0,90}',
 '검수': r'(검수[^\n]{0,10}(조건|기준|입회)|시험가동)[^\n]{0,90}',
}
base, no = sys.argv[1], sys.argv[2]
lim = int(sys.argv[3]) if len(sys.argv)>3 else 3
t = open(os.path.join(base, no+'.txt'), encoding='utf-8', errors='ignore').read()
t = re.sub(r'\.[A-Za-z][\w-]*\s*\{[^}]*\}', '', t)
t = re.sub(r'[ \t]{2,}', ' ', t)
print(f"########## {no} ##########")
for label, pat in PATTERNS.items():
    hits = []
    seen = set()
    for m in re.finditer(pat, t):
        s = re.sub(r'\s+', ' ', m.group(0)).strip()
        key = s[:40]
        if key in seen: continue
        seen.add(key); hits.append(s)
        if len(hits) >= lim: break
    if hits:
        print(f"\n[{label}]")
        for h in hits: print("  ·", h[:170])
