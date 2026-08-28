---
name: "mac-toolchain"
description: "이 프로젝트에서 파이썬 실행, 패키지·도구 설치, OCR, 엑셀 처리를 하기 전에 읽는다. 이 Mac의 Gatekeeper 격리 문제와 해결 절차."
---

# 이 Mac의 도구 체인

## 파이썬 (작동 표준)

- **`.agents/py`** → conda env `electerior` (Python 3.11.15)의 심볼릭 링크
  - 실체: `~/.anaconda3/envs/electerior/bin/python3.11`
  - 문서·스크립트는 항상 `.agents/py`를 쓴다. 환경을 갈아끼워도 링크만 고치면 된다
- 설치됨: openpyxl, pypdf, pyhwp(+olefile), requests, beautifulsoup4, lxml, xlrd, **numpy, pandas**
- 생성·복구: `bash .agents/scripts/setup_conda_env.sh` (건강검진) / `--force` (재생성)
- 구 `.agents/pyenv39`(Xcode 3.9 venv)는 2026-08-21까지 표준이었고 지금도 동작한다. 폴백용으로 남겨둠
- 구 `.agents/pyenv`(brew 3.14)는 brew 의존성 업그레이드로 파손됨. brew 파이썬은 재파손 위험 있음

### "컴파일 패키지 금지"는 오진이었다 (2026-08-21 정정)

이전 판에는 *"컴파일 패키지(numpy/pandas/Pillow/lxml) 설치 금지 - 시스템 파이썬의
라이브러리 서명 검증에 차단됨"* 이라고 적혀 있었다. **틀렸다.**

진짜 원인은 **quarantine 속성**이고, 벗기면 그냥 된다:

```bash
pip install numpy pandas          # 설치는 원래 됐다
xattr -dr com.apple.quarantine 대상디렉토리   # 이 한 줄이 빠져 있었을 뿐
```

- 벗기기 전 증상: `ImportError: ... library load disallowed by system policy`
  (numpy는 이걸 *"you should not try to import numpy from its source directory"* 라는
  **엉뚱한 메시지로 덮어쓴다.** 원인을 감추므로 속지 말 것 —
  `python -c "import numpy._core._multiarray_umath"`로 원본 에러를 봐야 한다)
- lxml·cryptography가 진작부터 잘 돌아가고 있었던 게 반증이었다. 그 `.so`들엔
  quarantine이 안 붙어 있었을 뿐이다
- Xcode python에는 library-validation entitlement가 없다
  (`codesign -d --entitlements -` 로 확인: `com.apple.application-identifier`뿐).
  즉 서드파티 `.so` 로딩 자체는 원래 허용된다

**교훈: "서명 검증에 차단"으로 단정하기 전에 `xattr`부터 확인한다.**

### conda: 새 env의 python 바이너리가 사라진다 (범인은 Gatekeeper 팝업)

이 Mac에서 `conda create -n 이름 python=3.11`을 하면 env는 만들어지는데
**`bin/python3.11`이 사라져 있다.** 실행하면 `Killed: 9`(SIGKILL) 후 파일이 증발하고,
휴지통에도 안 보인다.

**진단할 때 속지 말 것: "macOS가 자동으로 지운다"가 아니다.**
`conda create`는 내부적으로(.pyc 컴파일 등) 방금 푸른 python을 실행하고,
그순간 이 팝업이 뜼다:

```
"python3.11" Not Opened
Apple could not verify "python3.11" is free of malware ...
   [ Move to Trash ]   ← 파란색 기본 버튼(!)
   [ Done ]            ← 반드시 이걸 누른다
```

- **파란 강조 버튼이 `Move to Trash`라 무심코 Enter/클릭하면 바이너리가 삭제된다.**
  작업 중 팝업이 뜼면 사용자에게 **"Done을 누르라"고 즉시 알릴 것**
  (과거 python 바이너리·7zz·pdftotext가 이렇게 사라졌다)
- 팝업을 본 기억이 없는데 파일이 없을 수도 있다 — 이전 팝업에서 누른 결과가
  늦게 반영되거나 XProtect가 같은 바이너리를 기억해 차단한다
- 사라지는 건 **새로 풀린 python 바이너리 하나**다. `bzip2` 같은 다른 Mach-O 실행파일과
  `lib/libpython3.11.dylib`은 멀쩡히 살아남는다
- `xattr -dr`로 격리를 먼저 벗겨도, `codesign --force --sign -`로 재서명해도 소용없다
  (재서명은 오히려 파일을 다시 써서 상황을 악화시킨다)
- **우회법: pkgs 캐시의 원본을 `cp`로 복사한다.** 캐시 원본은 이미 자리를 잡은 파일이라
  그 신뢰를 이어받아 살아남고, 정상 실행된다
  ```bash
  # env가 기대하는 정확한 패치버전을 conda-meta에서 읽어야 한다
  ls ~/.anaconda3/envs/ENV/conda-meta/python-3.11.*.json   # → python-3.11.15-h478e877_1
  cp ~/.anaconda3/pkgs/python-3.11.15-h478e877_1/bin/python3.11 ~/.anaconda3/envs/ENV/bin/
  xattr -dr com.apple.quarantine ~/.anaconda3/envs/ENV
  ```
  버전을 대충 맞추면 stdlib과 인터프리터가 어긋난다(3.11.15 env에 3.11.14를 넣는 사고)
- 이 절차는 `setup_conda_env.sh`가 자동으로 한다. 손으로 하지 말 것
- `conda-meta` 기준 누락 파일 검사에서 `.pyc`만 빠진 건 무해하다(런타임 재생성)
- **기존 conda base(3.13.9)는 멀쩡하다.** 오래전에 자리잡아 검증을 통과한 상태다.
  즉 문제는 "conda"가 아니라 **"방금 생성된 실행 바이너리"**다

### pip 안전장치

환경변수 `PIP_REQUIRE_VIRTUALENV=true`가 걸려 있다(사용자가 base 오염을 막으려 건 것).
conda env는 `sys.prefix == sys.base_prefix`라 **venv로 인식되지 않아** 여기 걸린다.

```bash
PIP_REQUIRE_VIRTUALENV=false .agents/py -m pip install 패키지
```

env 안에 설치하는 것은 base 오염이 아니므로 이 해제는 안전하다. 다만 **base(`~/.anaconda3/bin/python`)에는 설치하지 말 것** — 사용자의 로보틱스 env들이 물려 있다.

## 설치된 도구

- OCR: `/opt/homebrew/bin/tesseract` (kor 데이터 설치됨) + `pdftoppm`(poppler)
- LibreOffice 26.2.5 (`/Applications/LibreOffice.app`) - **헤드리스 변환 불가**(아래 "GUI 앱" 절)
- 압축: `7zz`(sevenzip), Keka.app
- 한컴오피스 HWP.app (HWP 수동 확인용)

## macOS 격리(Gatekeeper) 문제 - 중요

이 Mac은 CLI가 만든 파일에 격리 속성이 전파되어 실행/로딩이 차단된다.

- pip/brew로 새로 받은 실행파일·dylib은 **실행하기 전에** 격리 해제:
  ```
  chmod u+w 파일 && xattr -d com.apple.quarantine 파일   # brew는 읽기전용 설치라 chmod 필수
  xattr -dr com.apple.quarantine ~/.anaconda3/envs/electerior   # conda env 전체
  xattr -dr com.apple.quarantine .agents/pyenv39                # 구 venv
  ```
- **`pip install` 뒤에는 항상 `xattr -dr`를 붙인다.** 컴파일 확장(.so)이 든 패키지는
  이걸 안 하면 import 시점에 `library load disallowed by system policy`로 막힌다
- **Gatekeeper 팝업이 뜨면 사용자에게 "Done"을 누르라고 안내. "Move to Trash"를 누르면 파일이 삭제된다** (python 바이너리, 7zz, pdftotext가 실제로 삭제된 사고 이력 있음)
- brew install이 의존성으로 python 등을 업그레이드하면 기존 venv 심볼릭 링크가 깨질 수 있음 - brew 설치 후 venv 동작 확인
- 샌드박스에서 hdiutil(DMG 마운트)·osascript(Finder 제어) 불가. DMG는 7zz로 추출
- brew 캐시 파일명의 해시는 URL 해시(파일 내용 아님) - 무결성은 공식 .sha256과 대조
- **PATH에 `/opt/homebrew/bin`이 없다.** `pdftoppm: command not found`는 미설치가 아니라 PATH 문제다.
  절대경로를 쓰거나 `export PATH="/opt/homebrew/bin:$PATH"`를 앞에 붙인다
- `timeout`(GNU coreutils) 없음. 백그라운드 실행 + `kill -0` 폴링으로 직접 만든다

## GUI 앱은 전부 막혔다 (2026-08-20 실측)

앱이 설치돼 있어도 **에이전트 셸에서는 띄울 수 없다.** 같은 원인(샌드박스)이라
"다른 오피스 앱으로 우회"가 안 된다. 재시도하지 말 것.

| 시도 | 결과 |
|---|---|
| `soffice --headless --convert-to pdf` | `Abort trap: 6` (`-env:UserInstallation`으로 프로파일 새로 줘도 동일) |
| `qlmanage -t` (Quick Look) | `sandbox initialization failed: Operation not permitted` |
| `open -a "Microsoft Excel"` | `Unable to find application named` (LaunchServices 차단, 앱은 존재) |
| `osascript -e 'tell application "Microsoft Excel"…'` | `-1728` / System Events `-10827` |
| `pgrep` | `sysmond service not found` |

`osascript -e 'return 1+1'`은 되므로 "osascript가 죽었다"가 아니라 **앱 실행·AppleEvents가**
막힌 것이다.

### 대신 쓰는 경로: xlsx → HTML → 브라우저

`.agents/scripts/xlsx_render.py`가 openpyxl로 **서식까지**(병합·테두리·배경·정렬·열너비·
숫자서식·하이퍼링크) 읽어 HTML을 만든다. 렌더/캡처는 Playwright가 한다.

```bash
.agents/py .agents/scripts/xlsx_render.py "20260820/파일.xlsx" -o /세션tmp/x.html
```
```js
const h = await fs.readFile('/세션tmp/x.html', 'utf8');
await page.goto('data:text/html;charset=utf-8,' + encodeURIComponent(h), { waitUntil: 'load' });
const png = await page.screenshot({ fullPage: true, type: 'png' });      // 이미지
const pdf = await page.pdf({ format: 'A4', landscape: true, printBackground: true }); // PDF
```

주의점:
- **`page.goto('file://…')`와 `page.setContent()`는 이 REPL에 없다.** `data:` URL로 넣는다
- 같은 탭에 `document.write`를 반복하면 문서가 누적돼 같은 표가 여러 번 찍힌다. `goto`로 갈아끼운다
- `page.setViewportSize()`도 없다. 폭은 HTML/CSS에서 잡는다
- 하이퍼링크가 `<a>`로 살아 있어 "링크가 실제로 박혀 있다"는 증거로도 쓸 수 있다
- PDF가 꼭 필요하면 `page.pdf()`를 쓴다 (부산대 워크북 A4 가로 = 526KB 생성 확인)

## Rust (2026-08-21 실측)

설치돼 있지만 **PATH에 없다.** `mac-toolchain`의 "PATH에 `/opt/homebrew/bin`이 없다"와 같은 원인.

```bash
export PATH="/opt/homebrew/bin:$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH"
cargo --version   # cargo 1.98.0 / rustc 1.98.0
```

- `~/.cargo/bin`은 **비어 있다.** rustup은 brew(`/opt/homebrew/bin/rustup`), 실체 툴체인은
  `~/.rustup/toolchains/stable-aarch64-apple-darwin/bin/`에 있다. 여기를 직접 PATH에 넣는 게 확실하다
- crates.io 인덱스는 **User-Agent를 줘야 200**이다(`curl -A "cargo/1.0" https://index.crates.io/config.json`).
  UA 없이 403이 떠도 "네트워크 차단"으로 단정하지 말 것. cargo 자체는 UA를 붙이므로 정상 동작한다

### 에이전트 셸에서 만든 실행 바이너리는 전부 SIGKILL된다

**이게 Rust의 핵심 제약이다.** Aside CLI가 생성한 모든 실행 파일에 격리 속성이 찍힌다:

```
com.apple.quarantine: 0081;6a87d8d5;Aside;
```

실행하면 `Killed: 9` (exit 137). **파이프에 물리면 exit code가 `head`의 것으로 덮여
`exit=0`처럼 보이니 속지 말 것** — 파이프 없이 실행해서 137을 확인해야 한다.

```bash
xattr -dr com.apple.quarantine target/debug/바이너리   # 이러면 그냥 실행된다
```

- rustc뿐 아니라 **`cc`로 만든 C 바이너리도 동일**하다. 언어 문제가 아니라 파일 생성 경로 문제다
- `CARGO_TARGET_DIR`을 `/tmp`, `~/`, 프로젝트 내부 어디로 바꿔도 **똑같이 찍힌다**(전역 스탬프).
  경로를 옮겨서 우회하려 하지 말 것 — 3곳 실측으로 확인했다

### 빌드 스크립트가 있는 crate는 빌드가 막힌다

cargo는 `build.rs`를 **컴파일한 직후 곧바로 실행**한다. 그 사이에 격리가 찍히므로 SIGKILL이다.

- 백그라운드 `xattr -dr` 루프(0.1~0.2초)를 돌리면 **일부는 이긴다** —
  proc-macro2·quote·syn·serde·serde_json·tokio-macros는 이렇게 통과했다
- 그러나 **`libc`는 6회 재시도 전부 실패**했다. 실패한 빌드 스크립트는 캐시되지 않아
  매 시도마다 새로 컴파일되고, 그래서 매번 새 격리 파일이 된다. 재시도로 수렴하지 않는다
- 즉 **axum/tokio 같은 일반적인 의존성 트리는 이 셸에서 빌드할 수 없다**

**대응:**

1. **의존성 없는(std-only) Rust를 쓴다.** build.rs가 없으니 레이스 자체가 없고,
   최종 바이너리만 `xattr -dr` 하면 실행된다. 검증까지 에이전트가 할 수 있는 유일한 경로다
2. 의존성이 꼭 필요하면 **사용자가 자기 터미널에서 `cargo build`를 한 번 돌린다.**
   격리를 찍는 주체가 Aside이므로 Terminal.app에서는 안 찍힌다.
   단 이 경우 **에이전트는 빌드 결과를 검증할 수 없다** — 사용자에게 명시할 것

### 뚫는 방법 (2026-08-21 실증, dashboard/ 에서 확인)

**사람이 Terminal.app에서 최초 1회만 빌드하면 그 뒤로는 에이전트도 재빌드할 수 있다.**
격리를 찍는 주체가 Aside라 사용자 터미널에서는 안 찍히고, 한 번 성공하면 의존성이
`target/`에 캐시되어 `build.rs`가 다시 돌지 않기 때문이다.

- 실측: axum 0.8.9 + tokio 1.53.1, 62 크레이트. 최초 빌드는 사용자, 이후 소스 수정
  재빌드는 에이전트가 **1.7초**에 성공했다
- 검증 못 한 코드를 넘기는 위험은 `cargo generate-lockfile` + `cargo fetch`로 줄인다.
  둘 다 build.rs를 돌리지 않아 **에이전트가 실행할 수 있고**, 버전을 고정해두면 사용자
  빌드가 설계 시점 API와 어긋나지 않는다 (이 방식으로 axum 코드가 한 번에 컴파일됐다)
- **`Cargo.toml`을 건드리면 다시 막힌다.** 의존성·`[profile]` 변경은 지문을 깨서 모든
  `build.rs`를 재실행시킨다(`strip = true` 한 줄 삭제 → `mime_guess`에서 SIGKILL).
  소스 파일만 고칠 것
- `strip = true`는 이 툴체인에서 `rust-objcopy`가 `libLLVM.dylib`를 못 찾아 실패하지만
  경고일 뿐 빌드는 된다. 지우면 위 문제가 생기니 그대로 둔다

### npm 네이티브 바이너리도 같은 문제 (esbuild)

`npm install esbuild`는 postinstall에서 `esbuild --version`으로 바이너리를 검증하는데
그게 SIGKILL돼 **설치 자체가 실패**한다.

```bash
npm install --ignore-scripts esbuild
xattr -dr com.apple.quarantine node_modules     # 이 뒤엔 정상 실행된다
```

다운로드된 바이너리는 재생성되지 않으므로 이 방법이 영구히 통한다(cargo build.rs와 다른 점).

### 사용자 터미널에는 node/npm이 없다

이 Mac의 node는 **Aside 런타임(`~/.aside/runtime/bin/node`)에만** 있다.
사용자가 돌릴 빌드 스크립트는 npm 단계를 선택적으로 만들고 번들 산출물을 미리 만들어 둔다.

또한 **사용자 셸에서 `~`가 `/home/<user>`로 풀리는 경우가 있다**(실제 홈은 `/Users/<user>`).
스크립트에서는 `$HOME`/`~` 대신 `eval echo ~"$(id -un)"`으로 홈을 구하고,
사용자에게 안내할 때는 절대경로를 준다.
