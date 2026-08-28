#!/usr/bin/env python3
"""규격 첨부 텍스트 추출: python extract_specs.py SPECDIR [공고번호...]"""
import sys, os, re, zipfile, subprocess, tempfile, glob, shutil
import xml.etree.ElementTree as ET


def _hwp5html():
    """hwp5html 실행경로.

    2026-08-21 사고: `.agents/py`(심볼릭 링크)로 실행하면 sys.executable이 링크 경로
    그대로라 os.path.dirname()이 `.agents/`를 가리켜 hwp5html을 못 찾았다.
    HWP 첨부 3개가 전부 `[HWP-FAIL ... No such file]`로 조용히 비어서 나왔다
    (파일은 생성되고 종료코드도 0이라 눈치채기 어렵다).
    → 링크를 풀고, conda env / venv / PATH 순으로 찾는다."""
    cands = []
    real = os.path.realpath(sys.executable)
    cands.append(os.path.join(os.path.dirname(real), 'hwp5html'))
    cands.append(os.path.join(os.path.dirname(sys.executable), 'hwp5html'))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .agents/
    cands.append(os.path.join(here, 'pyenv39', 'bin', 'hwp5html'))
    cands.append(os.path.join(here, 'pyenv', 'bin', 'hwp5html'))
    for c in cands:
        if os.path.exists(c):
            return c
    w = shutil.which('hwp5html')
    if w:
        return w
    raise FileNotFoundError('hwp5html 없음. 시도한 경로: ' + ', '.join(cands))


def from_hwp(p):
    d = tempfile.mkdtemp()
    try:
        subprocess.run([_hwp5html(),'--output',d,p],
                       check=True, capture_output=True, timeout=120)
        html = open(os.path.join(d,'index.xhtml'), encoding='utf-8', errors='ignore').read()
        html = re.sub(r'<(td|th)[^>]*>', ' | ', html)
        html = re.sub(r'</(tr|p|div)>', '\n', html)
        txt = re.sub(r'<[^>]+>', '', html)
        import html as H; txt = H.unescape(txt)
        return re.sub(r'\n{3,}', '\n\n', txt)
    except Exception as e:
        return f"[HWP-FAIL {e}]"

def from_hwpx(p):
    """HWPX(zip+XML) 본문 추출.

    주의(2026-08-21 사고): 예전 구현은 `el.text`만 읽었다. ElementTree에서
    `<hp:t>`가 자식 요소(markpenBegin/lineBreak/ctrl 등)를 가지면 `.text`는
    '첫 자식 앞'까지만 반환하고 나머지는 자식의 `.tail`에 들어간다.
    그 결과 규격서의 줄이 **경고 없이 통째로 사라졌다**
    (KAIST R26BK01690151: 'Memory: DDR5 ECC 64GB', 'Clock speed: 6400MHz',
     'L3 Cache: 256MB' 3줄 유실 → 메모리 속도 미지정으로 오판, 규격 미달 부품 선정).
    반드시 `''.join(el.itertext())`로 tail까지 회수할 것.
    """
    def _walk(el, out):
        tag = el.tag.split('}')[-1]
        if tag == 't':
            s = ''.join(el.itertext())   # 자식 요소의 tail까지 회수
            if s: out.append(s)
            return                      # t 내부는 더 파고들지 않는다
        if tag in ('p', 'tc', 'tr', 'lineBreak'):
            out.append('\n')
        for c in el:
            _walk(c, out)
    try:
        out=[]
        with zipfile.ZipFile(p) as z:
            for n in sorted(x for x in z.namelist() if re.match(r'Contents/section\d+\.xml', x)):
                _walk(ET.fromstring(z.read(n)), out)
        return re.sub(r'\n{3,}','\n\n',''.join(out))
    except Exception as e: return f"[HWPX-FAIL {e}]"


def hwpx_dropped_by_old_parser(p):
    """구 파서가 놓쳤던 문자열만 뽑아 반환(감사용)."""
    lost=[]
    try:
        with zipfile.ZipFile(p) as z:
            for n in sorted(x for x in z.namelist() if re.match(r'Contents/section\d+\.xml', x)):
                for el in ET.fromstring(z.read(n)).iter():
                    if el.tag.split('}')[-1] != 't': continue
                    full=''.join(el.itertext()); old=el.text or ''
                    if full != old:
                        d=full[len(old):].strip()
                        if d: lost.append(d)
    except Exception as e:
        lost.append(f"[SCAN-FAIL {e}]")
    return lost

def from_pdf(p):
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or '') for pg in PdfReader(p).pages)
    except Exception as e: return f"[PDF-FAIL {e}]"

def from_xlsx(p):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=True); out=[]
        for ws in wb:
            out.append(f"## sheet {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells=[str(c) for c in row if c not in (None,'')]
                if cells: out.append(" | ".join(cells))
        return "\n".join(out)
    except Exception as e:
        try:
            import zipfile as Z
            out=[]
            with Z.ZipFile(p) as z:
                shared=[]
                if 'xl/sharedStrings.xml' in z.namelist():
                    r=ET.fromstring(z.read('xl/sharedStrings.xml'))
                    for si in r: shared.append(''.join(t.text or '' for t in si.iter() if t.tag.endswith('}t')))
                for n in sorted(x for x in z.namelist() if x.startswith('xl/worksheets/sheet')):
                    r=ET.fromstring(z.read(n))
                    for row in r.iter():
                        if not row.tag.endswith('}row'): continue
                        vals=[]
                        for c in row:
                            t=c.get('t'); v=c.find('{*}v')
                            if v is None or v.text is None: continue
                            vals.append(shared[int(v.text)] if t=='s' and int(v.text)<len(shared) else v.text)
                        if vals: out.append(" | ".join(vals))
            return "\n".join(out)
        except Exception as e2:
            # 구형 .xls(OLE2)는 openpyxl도 zipfile도 못 읽는다. 나라장터 첨부에 아직 흔하다
            # (2026-08-25 국민대 R26BK01693176 규격서가 .xls라 규격 전문이 통째로 비어 있었다).
            try:
                import xlrd
                wb = xlrd.open_workbook(p, ignore_workbook_corruption=True)
                out = []
                for sh in wb.sheets():
                    out.append(f"## sheet {sh.name}")
                    for r in range(sh.nrows):
                        cells = []
                        for c in range(sh.ncols):
                            v = sh.cell_value(r, c)
                            if v in (None, ''):
                                continue
                            if isinstance(v, float) and v == int(v):
                                v = int(v)
                            cells.append(str(v).strip())
                        if cells:
                            out.append(" | ".join(cells))
                return "\n".join(out)
            except Exception as e3:
                return f"[XLSX-FAIL {e} / {e2} / xlrd:{e3}]"

def extract(p):
    l=p.lower()
    if l.endswith('.hwp'): return from_hwp(p)
    if l.endswith(('.hwpx',)): return from_hwpx(p)
    if l.endswith('.pdf'): return from_pdf(p)
    if l.endswith(('.xlsx','.xls')): return from_xlsx(p)
    if l.endswith('.zip'):
        out=[]
        d=tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(p) as z: z.extractall(d)
        except Exception as e: return f"[ZIP-FAIL {e}]"
        for f in sorted(glob.glob(d+'/**/*', recursive=True)):
            if os.path.isfile(f):
                out.append(f"\n===== [zip] {os.path.basename(f)} =====\n"+extract(f))
        return "\n".join(out)
    return "[SKIP]"

# ── 누락 감지: 열거번호 연속성 검사 (2026-08-21 추가) ────────────────────────
# 배경(실사고): HWPX 파서가 <hp:t>의 tail 텍스트를 버려 규격서 3줄이 조용히 사라졌다.
#   '나. 고성능 CPU' 의 3) L3 Cache: 256MB
#   '다. 메인 메모리' 의 1) Memory: DDR5 ECC 64GB / 2) Clock speed: 6400MHz
# 결과 텍스트만 봐서는 알 수 없어 '메모리 속도 미지정'으로 오판했고, 규격 미달인
# DDR5-5600을 선택해 마진이 7.5% → 11.0%로 부풀었다.
# → 추출물은 항상 자가검증한다. 소스가 HWPX든 HWP든 PDF/OCR이든 동일하게 동작.
#
# 규칙: 한글 항목머리(가./나./다. …) 아래 아라비아 열거는 1)부터 연속이어야 한다.
#       첫 항목이 1)이 아니거나 중간이 건너뛰면 그 줄이 사라진 것이다.
_ORD = '가나다라마바사아자차카타파하'
# 항목머리는 줄머리의 '가./나./다' + 공백. `\s*`를 쓰면 앞 빈 줄까지 먹어 구간이 어긋난다.
_HEAD = re.compile(r'(?m)^[ \t]*([' + _ORD + r'])[.)]?[ \t]+(?=\S)')
# 열거 항목은 '줄머리'에 온다. 그냥 (\d{1,2})\) 로 잡으면 전화번호 1588-0800) ·
# 날짜 8. 25) 까지 열거로 세어 오탐이 쏟아진다.
_NUM = re.compile(r'(?m)^[ \t]*(\d{1,2})\)')
# 단, 머리줄에 붙어 나오는 첫 항목은 예외 (예: "가. 1) Tower형 워크스테이션")
_NUM_INLINE = re.compile(r'^[ \t]*[' + _ORD + r'][.)]?[ \t]+(\d{1,2})\)')


def check_numbering(txt, label=''):
    """항목머리별 아라비아 열거의 시작·연속성 검사. 누락 의심 구간을 문자열 리스트로 반환."""
    heads = list(_HEAD.finditer(txt))
    # 항목머리는 가→나→다 순서로 이어져야 한다. 순서가 안 맞는 매치는 오탐이라 버린다.
    keep, last = [], None
    for m in heads:
        i = _ORD.index(m.group(1))
        if last is None or i == last + 1 or i == 0:   # 첫 매치는 무조건 채택(부분 발췌 대응)
            keep.append(m)
            last = i
    heads, warns = keep, []

    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(txt)
        seg = txt[h.start():end]
        if len(seg) > 3000:                 # 항목머리가 아닌 산문 오탐 방지
            continue
        lines = seg.splitlines()
        nums = []
        m0 = _NUM_INLINE.match(lines[0] if lines else '')
        if m0:
            nums.append(int(m0.group(1)))
        nums += [int(m.group(1)) for m in _NUM.finditer(seg)]
        nums = [n for n in nums if n > 0]
        if not nums:
            continue
        title = ' '.join(lines[0].split())[:40]
        # 항목이 1개뿐이어도 그게 1)이 아니면 앞이 사라진 것이다.
        # (KAIST 사고가 정확히 이 형태였다: '다. 메인 메모리 : 8개' 아래 3) 한 줄만 남음)
        if nums[0] != 1:
            warns.append(f"[{label}] '{title}' 열거가 {nums[0]})부터 시작 "
                         f"— 1)~{nums[0] - 1}) 누락 의심")
        for a, b in zip(nums, nums[1:]):
            if b not in (a, a + 1):
                warns.append(f"[{label}] '{title}' 열거 {a}) → {b}) 로 건너뜀 — 중간 누락 의심")
    return warns


def audit_extraction(txt, label=''):
    """추출 직후 호출. 경고를 stdout으로 낸다(stderr로만 내면 보고 경로에서 증발한다)."""
    w = check_numbering(txt, label)
    for line in w:
        print("  [!] 누락의심 " + line)
    return w


if __name__=='__main__':
    base=sys.argv[1]; nos=sys.argv[2:] or sorted(os.listdir(base))
    for no in nos:
        d=os.path.join(base,no)
        if not os.path.isdir(d): continue
        outp=os.path.join(base, no+'.txt'); buf=[]
        for f in sorted(os.listdir(d)):
            buf.append(f"\n########## {f} ##########\n"+extract(os.path.join(d,f)))
        txt = "\n".join(buf)
        open(outp,'w',encoding='utf-8').write(txt)
        print(f"{no}: {len(txt):,} chars -> {outp}")
        # 추출 직후 자가검증 (규격 줄이 조용히 사라지는 사고 재발 방지)
        audit_extraction(txt, no)
