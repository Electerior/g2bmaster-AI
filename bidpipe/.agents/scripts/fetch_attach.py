#!/usr/bin/env python3
"""나라장터 첨부 일괄 다운로드: python fetch_attach.py OUTDIR 공고번호[:차수] ..."""
import sys, os, re, urllib.request, urllib.parse

BASE = "https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do"
def dl(no, ord_, seq, outdir):
    url = f"{BASE}?bidPbancNo={no}&bidPbancOrd={ord_}&fileType=&fileSeq={seq}&prcmBsneSeCd=01"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.g2b.go.kr/"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            cd = r.headers.get("Content-Disposition","")
    except Exception as e:
        return None
    if len(data) < 500: return None
    magic = data[:2].hex()
    ext = {"2550":"pdf","d0cf":"hwp","504b":"zip"}.get(magic)
    if ext is None: return None
    fn = ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    if m:
        fn = urllib.parse.unquote(m.group(1))
        try: fn = fn.encode('latin-1').decode('utf-8')
        except Exception: pass
    fn = re.sub(r'[/\\]', '_', fn).strip() or f"{no}_{seq}.{ext}"
    if ext == "zip" and not fn.lower().endswith(("hwpx","zip","docx","xlsx","pptx")): fn += ".hwpx"
    d = os.path.join(outdir, no)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{seq:02d}_{fn}")
    open(p,"wb").write(data)
    return (p, len(data), magic)

if __name__ == "__main__":
    outdir = sys.argv[1]
    for spec in sys.argv[2:]:
        no, _, ord_ = spec.partition(":")
        ord_ = ord_ or "000"
        got = 0
        for seq in range(1, 13):
            r = dl(no, ord_, seq, outdir)
            if r:
                got += 1
                print(f"OK {no} seq{seq} {r[1]:>9,}B {os.path.basename(r[0])}")
        if not got: print(f"NONE {no}")
