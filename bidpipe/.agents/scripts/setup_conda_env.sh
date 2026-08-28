#!/bin/bash
# Electerior 분석 환경(conda) 생성·복구 스크립트
#
# 왜 이 스크립트가 필요한가:
#   이 Mac에서는 `conda create`가 만든 python 바이너리가 사라진다.
#   conda가 내부적으로 그 python을 실행하는 순간 Gatekeeper 팝업
#   ("python3.11" Not Opened / Apple could not verify ...)이 뜨는데,
#   파란 기본 버튼이 [Move to Trash]라 그게 눌리면 바이너리가 삭제된다.
#   pkgs 캐시의 원본을 `cp`로 복사해 넣으면 살아남는다 — 캐시 원본은 이미 자리를
#   잡아 검증을 통과한 파일이라 그 신뢰를 이어받기 때문이다.
#   자세한 배경은 .agents/skills/mac-toolchain/SKILL.md 참조.
#
# !! 실행 중 Gatekeeper 팝업이 뜨면 반드시 [Done]을 누를 것. [Move to Trash] 금지 !!
#
# 사용법:
#   bash .agents/scripts/setup_conda_env.sh          # 없으면 생성, 있으면 건강검진
#   bash .agents/scripts/setup_conda_env.sh --force  # 지우고 새로 생성
#
set -uo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/.anaconda3}"
CONDA="$CONDA_ROOT/bin/conda"
ENV_NAME="${ENV_NAME:-electerior}"
ENV_DIR="$CONDA_ROOT/envs/$ENV_NAME"
PYVER="${PYVER:-3.11}"
PKGS="openpyxl pypdf pyhwp requests beautifulsoup4 lxml xlrd numpy pandas"

say() { echo "[setup] $*"; }
die() { echo "[setup][ERROR] $*" >&2; exit 1; }

[ -x "$CONDA" ] || die "conda를 찾을 수 없다: $CONDA"

if [ "${1:-}" = "--force" ] && [ -d "$ENV_DIR" ]; then
  say "기존 env 제거: $ENV_NAME"
  "$CONDA" env remove -n "$ENV_NAME" -y >/dev/null 2>&1
  rm -rf "$ENV_DIR"
fi

# 1) env 생성 (없을 때만)
if [ ! -d "$ENV_DIR" ]; then
  say "env 생성: $ENV_NAME (python $PYVER)"
  say "!! Gatekeeper 팝업이 뜨면 [Done]을 누르세요. [Move to Trash]를 누르면 삭제됩니다 !!"
  "$CONDA" create -n "$ENV_NAME" "python=$PYVER" -y >/dev/null 2>&1 \
    || die "conda create 실패"
fi

# 2) 삭제된 python 바이너리 복구 (이 Mac의 핵심 우회)
#    env가 기대하는 정확한 패치버전을 conda-meta에서 읽어 그 pkgs 원본을 복사한다.
restore_python() {
  local meta full src
  meta=$(ls "$ENV_DIR"/conda-meta/python-${PYVER}.*.json 2>/dev/null | head -1)
  [ -n "$meta" ] || { say "conda-meta에서 python 항목을 못 찾음"; return 1; }
  full=$(basename "$meta" .json)                 # 예: python-3.11.15-h478e877_1
  src="$CONDA_ROOT/pkgs/$full/bin/python${PYVER}"
  [ -f "$src" ] || { say "pkgs 캐시 원본 없음: $src"; return 1; }
  say "python 바이너리 복구: $full"
  cp "$src" "$ENV_DIR/bin/python${PYVER}" || return 1
  xattr -dr com.apple.quarantine "$ENV_DIR/bin/python${PYVER}" 2>/dev/null
  return 0
}

PY="$ENV_DIR/bin/python${PYVER}"
if [ ! -f "$PY" ] || ! "$PY" -V >/dev/null 2>&1; then
  restore_python || die "python 바이너리 복구 실패"
fi
"$PY" -V >/dev/null 2>&1 || die "python이 여전히 실행되지 않는다"
say "python OK: $("$PY" -V 2>&1)"

# 3) 격리 속성 제거 (컴파일 확장 .so 로딩 차단 방지)
#    numpy/pandas/lxml의 .so는 quarantine이 붙어 있으면
#    'library load disallowed by system policy'로 import가 막힌다.
say "quarantine 제거"
xattr -dr com.apple.quarantine "$ENV_DIR" 2>/dev/null

# 4) 패키지 설치
#    PIP_REQUIRE_VIRTUALENV=true가 걸려 있고 conda env는 venv로 인식되지 않아
#    (sys.prefix == sys.base_prefix) 이 명령에 한해 해제한다. base 오염이 아니다.
say "패키지 설치: $PKGS"
PIP_REQUIRE_VIRTUALENV=false "$PY" -m pip install --quiet --upgrade $PKGS 2>&1 | tail -3
xattr -dr com.apple.quarantine "$ENV_DIR" 2>/dev/null

# 5) 검증
say "검증"
"$PY" - <<'PYCODE'
import sys
mods = ["openpyxl","pypdf","hwp5","olefile","requests","bs4","lxml.etree","xlrd","numpy","pandas"]
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {str(e)[:80]}")
print(f"  python {sys.version.split()[0]}")
print(f"  {len(mods)-len(bad)}/{len(mods)} 모듈 OK")
for b in bad:
    print(f"  FAIL {b}")
sys.exit(1 if bad else 0)
PYCODE
rc=$?

# 6) 깨진 심볼릭 링크(추가 삭제 흔적) 점검
broken=$(find "$ENV_DIR/bin" -type l ! -exec test -e {} \; -print 2>/dev/null | wc -l | tr -d ' ')
[ "$broken" != "0" ] && say "경고: 깨진 링크 $broken 개 (추가 바이너리 삭제 의심)"

if [ $rc -eq 0 ]; then
  say "완료. 사용: $PY"
else
  die "일부 모듈 import 실패"
fi
