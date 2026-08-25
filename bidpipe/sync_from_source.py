# -*- coding: utf-8 -*-
"""/Users/hanbin5/Documents/Electerior(살아있는 워크스페이스) → bidpipe/ 동기화 헬퍼.

소스 폴더는 계속 진화한다(매일 배치, 사고 교훈, 게이트 패치).
이 스크립트는 (1) scripts/skills/data/AGENTS.md/Format.xlsx 를 재복사하고
(2) 하드코딩 절대경로를 BIDPIPE_ROOT 유도블록으로 재배선한다(멱등).

실행:  .venv/bin/python bidpipe/sync_from_source.py
동기화 후엔 반드시 `python /tmp/bidpipe_regress2.py` (src vs new 셀 대조) 로 충실성 확인.

credentials/·pyenv/·py 심링크·dashboard 는 절대 복사하지 않는다(비밀·로컬환경).
"""
import glob, os, shutil, sys

SRC = next(g for g in glob.glob(os.path.expanduser("~/Documents/Elect*")))
HERE = os.path.dirname(os.path.abspath(__file__))          # bidpipe/
DEST = os.path.join(HERE, ".agents")

# (1) 재복사 대상 (credentials 제외)
COPIES = [
    (os.path.join(SRC, ".agents", "scripts"),  os.path.join(DEST, "scripts")),
    (os.path.join(SRC, ".agents", "skills"),   os.path.join(DEST, "skills")),
    (os.path.join(SRC, ".agents", "data", "price"),    os.path.join(DEST, "data", "price")),
    (os.path.join(SRC, ".agents", "data", "outcomes"), os.path.join(DEST, "data", "outcomes")),
    (os.path.join(SRC, "AGENTS.md"),  os.path.join(HERE, "AGENTS.md")),
    (os.path.join(SRC, "Format.xlsx"), os.path.join(HERE, "Format.xlsx")),
]

def copy_all():
    for src, dst in COPIES:
        if not os.path.exists(src):
            print(f"  skip (없음): {src}")
            continue
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        # __pycache__ 제거
        for root, dirs, _ in os.walk(dst):
            for d in list(dirs):
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d))
        print(f"  copy: {os.path.relpath(src, SRC)} -> {os.path.relpath(dst, HERE)}")

# (2) 경로 재배선 — 소스가 쓰는 고정 리터럴을 BIDPIPE_ROOT 유도블록으로 교체.
#     이미 교체된 파일은 리터럴이 사라져 멱등(다시 실행해도 no-op).
_ROOT_BLOCK = (
    '# 2026-08-25: BIDPIPE_ROOT 재배선 — g2bmaster-AI/bidpipe/.agents/scripts 에서 유도\n'
    'BIDPIPE_ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
    '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
)
S = SRC   # 소스 파일의 고정 리터럴 = 실제 폴더 경로 (회귀테스트로 동일성 검증됨)
REWRITES = [
    # (file, old, new)
    ("price_schema.py",
     f'ROOT = "{S}"',
     _ROOT_BLOCK.replace("BIDPIPE_ROOT = ", "ROOT = ") + 'ROOT = ROOT\n'
     .replace('ROOT = os.environ', 'ROOT = os.environ')
     # 간단화: ROOT 블록만
     ),
    ("patch_items.py",  f'BASE = "{S}"',
     'BASE = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'),
    ("opaque_bounds.py", f'ROOT = "{S}"',
     'ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'),
    ("outcome_tracker.py", f'ROOT = "{S}"',
     'ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'),
    ("apply_link_fixes.py", f'BASE = "{S}"',
     'BASE = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'),
    ("gen_analysis.py",
     f'FORMAT = "{S}/Format.xlsx"',
     '_BIDPIPE_ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
     'FORMAT = os.path.join(_BIDPIPE_ROOT, "Format.xlsx")'),
    ("gen_analysis.py",
     f'OUTDIR = os.environ.get("GEN_OUTDIR") or os.path.join(\n'
     f'    "{S}", datetime.date.today().strftime("%Y%m%d"))',
     'OUTDIR = os.environ.get("GEN_OUTDIR") or os.path.join(\n'
     '    _BIDPIPE_ROOT, "out", datetime.date.today().strftime("%Y%m%d"))'),
]
# price_schema.py 는 ROOT 블록이 다르다 — 위 REWRITES[0]의 new가 어색하므로 별도 처리.
REWRITES[0] = ("price_schema.py",
     f'ROOT = "{S}"',
     'ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(\n'
     '    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))')

def rewrite_paths():
    for fname, old, new in REWRITES:
        p = os.path.join(DEST, "scripts", fname)
        if not os.path.exists(p):
            print(f"  skip (없음): {fname}"); continue
        txt = open(p, encoding="utf-8").read()
        if old in txt:
            txt = txt.replace(old, new, 1)
            open(p, "w", encoding="utf-8").write(txt)
            print(f"  rewrite: {fname}")
        else:
            # 이미 재배선됐는지 확인(멱등 확인용)
            marker = "BIDPIPE_ROOT" if fname != "gen_analysis.py" else "_BIDPIPE_ROOT"
            if marker in txt:
                print(f"  ok(이미): {fname}")
            else:
                print(f"  WARN 리터럴도·블록도 없음: {fname}")

if __name__ == "__main__":
    print(f"SRC = {SRC}")
    print("[1/2] 재복사")
    copy_all()
    print("[2/2] 경로 재배선")
    rewrite_paths()
    # 잔여 하드코딩 검사
    bad = []
    for root, _, files in os.walk(DEST):
        for f in files:
            if f.endswith(".py"):
                t = open(os.path.join(root, f), encoding="utf-8").read()
                if S in t:
                    bad.append(os.path.relpath(os.path.join(root, f), HERE))
    if bad:
        print(f"FAIL — 여전히 {S} 포함: {bad}"); sys.exit(1)
    print("PASS — 동기화 완료, 잔여 하드코딩 경로 0")
