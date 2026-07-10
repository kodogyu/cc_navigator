# cc_navigator

[English](README.md) · **한국어**

살아 있는 모든 Claude Code 세션을 한 줄씩 나열하고, 입력을 기다리는 세션을 강조
표시하며, 그 세션의 터미널로 **바로 이동**하거나 **답장을 곧장 입력**할 수 있는
항상 위에 떠 있는 패널입니다. 알림이 어느 세션에서 왔는지 찾으려고 수십 개의 창과
탭을 뒤질 필요가 없습니다.

```
┌─────────────────────────────┐
│ cc_navigator                │
├─────────────────────────────┤
│   "Session1 title"          │
│   working details …         │
├─────────────────────────────┤
│ ● "Session2 title"          │  ← 입력 대기 중, 강조 표시
│   permission_prompt …       │     [ 이동 ]  [ 답장 ▸ ]
├─────────────────────────────┤
│   "Session3 title"          │
│   working details …         │
└─────────────────────────────┘
```

## 문제

여러 개의 Claude Code 세션을 여러 터미널 창에 걸쳐 동시에 돌립니다. 그중 하나가
입력을 필요로 하면 데스크톱 알림이 뜨지만, *어느* 세션인지는 알려주지 않고, 그
세션에 닿으려면 창과 탭을 일일이 뒤지는 수밖에 없습니다. cc_navigator는 모든
세션에 한 줄, 하나의 배지, 그리고 그곳으로 가는 한 번의 클릭을 줍니다.

## 동작 방식

Claude Code는 세션 생명주기 이벤트마다 **훅(hook)** 을 실행합니다. 작은 shim
(`bin/cc-navigator-hook`)이 각 이벤트를 세션별 상태 파일로 기록합니다. 패널은 그
상태 파일들을 살아 있는 `tmux` 페인과 조인해서 무엇을 보여줄지 결정합니다.

- **생존 여부는 통보받는 게 아니라 파생됩니다.** "세션 종료" 이벤트는 없습니다.
  `tmux`에서 사라진 세션은 다음 갱신 때 패널에서 사라지고, 그 상태 파일은
  정리됩니다. 그래서 죽은 세션이 유령 행을 남기지 않습니다.
- **이동은 창을 제목으로 지정합니다.** `tmux`의 `set-titles-string`이 외부 X11 창
  제목에 `ccnav:<세션>`을 새기고, GNOME Shell이 그 제목을 가진 창을 활성화합니다.
- **답장은 한 줄을 주입합니다.** 세션의 페인에 `tmux send-keys -l`로 보내므로,
  텍스트가 문자 그대로 전달되고 셸이 절대 해석하지 않습니다.

### 전체 설계를 관통하는 하나의 규칙

> **API의 자기 보고를 믿지 마라. 한 채널로 실행하고, 다른 채널로 검증하라.**

이 프로젝트는 두 개의 GNOME API가 아무것도 하지 않으면서 성공을 보고하기 때문에
존재합니다. `gdbus`는 `Eval`이 `(false, 'ReferenceError…')`를 반환해도 종료
코드 `0`을 내고, `window.activate(0)`은 워크스페이스를 넘어 포커스를 옮기지
못하면서도 정상적으로 반환합니다. 그래서 이동 경로는 GNOME Shell `Eval`로
*실행*하고 그 효과를 `xprop`으로 *검증*합니다 — `Eval`이 아니라 `xprop`을
믿습니다. 이 원칙이 모든 모듈을, 그리고 테스트를 관통합니다. 각 작업은 **뮤테이션
테스트**로 검증합니다. 구현을 일부러 N가지로 망가뜨리고, 각각의 손상이 이름
붙은 테스트를 반드시 실패시켜야 합니다. 망가진 구현을 잡아내지 못하는 테스트
스위트는 증거가 아니기 때문입니다.

## 요구 사항

이것은 의도적으로 좁게 설계된 **X11 + GNOME + tmux** 도구입니다. 아래 환경에서
개발하고 검증했습니다.

| | |
|---|---|
| 디스플레이 서버 | **X11** (Wayland 아님) — 포커스를 `xprop`으로 `_NET_ACTIVE_WINDOW`를 읽어 검증 |
| 데스크톱 | **`Eval`이 잠기지 않은 GNOME Shell** — GNOME 41부터 차단됨. 3.36.9에서 개발 |
| 터미널 멀티플렉서 | **tmux ≥ 3.0** — 세션을 tmux 페인으로 지정 |
| 인터프리터 | **PyGObject가 있는 `/usr/bin/python3` ≥ 3.8** (`apt install python3-gi gir1.2-gtk-3.0`) |
| 추가 필요 | `gdbus`, `xprop` |

서드파티 파이썬 의존성은 없습니다 — 표준 라이브러리와 시스템 `gi` 바인딩뿐입니다.

GNOME `Eval`을 쓸 수 없어도 **앱은 여전히 실행됩니다**. 이동 버튼이 비활성화되고
상태 표시줄이 그 이유를 설명합니다. 세션 나열과 답장 입력은 그것 없이도 됩니다.

## 시작하기

```sh
git clone https://github.com/kodogyu/cc_navigator.git
cd cc_navigator
./run-tests                    # 기대 결과: Ran 217 tests / OK
./bin/cc-navigator-doctor      # 이 머신을 점검하고 무엇을 고쳐야 하는지 정확히 출력
```

**doctor를 먼저 실행하세요.** 설정 파일을 보고 추측하지 않습니다 — 정말로 중요한
그 하나의 실패(아래 참고)를 개인 tmux 소켓에서 재현하고 판정을 보고한 다음,
`~/.tmux.conf`와 `~/.claude/settings.json`에 어떤 줄을 추가해야 하는지 정확히
알려줍니다.

doctor를 통과하면:

1. 다섯 개의 훅(`SessionStart`, `UserPromptSubmit`, `Notification`, `Stop`,
   `PreToolUse`)을 `~/.claude/settings.json`에 추가하고, 각각을 **절대 경로**로
   `<저장소>/bin/cc-navigator-hook`을 가리키게 합니다.
2. 프로젝트마다 tmux 세션 하나를, 각각 자기 터미널 창에 붙여 실행합니다.
3. `./bin/cc-navigator &`

등록된 세션이 없으면 패널은 아무 일도 하지 않습니다 — 훅이 첫 상태 파일을 쓰기
전까지 tmux 호출을 **한 번도** 하지 않으므로, 쓰지 않는 동안에는 비용이 0입니다.

## ⚠️ doctor가 잡으려고 존재하는 tmux 지뢰

tmux 3.0a에서, 플래그에 **`-g`, `-q`, `-s` 중 무엇도 없는** `set` 계열의
`~/.tmux.conf` 줄(`set`, `setw`, `set-option`, `set-window-option`)은 설정 로드
시점에 서버를 조용히 손상시킵니다. 그 서버는 이후 정상적으로 동작합니다 — 페인을
나열하고 창을 전환하고 — 무언가가 `send-keys`로 **공백**을 보내는 순간까지는요.
그 순간 서버가 **세그폴트**하며, 그 안의 모든 Claude Code 세션을 함께 데려갑니다.
방아쇠는 공백이 들어간, 당신이 입력하는 첫 답장입니다.

`bin/cc-navigator-doctor`는 당신의 설정을 일회용 서버에 로드하고 공백을 보내서 이
문제를 감지합니다. 그래서 cc_navigator가 당신의 진짜 tmux를 건드리기 전에 알 수
있습니다. 해결책은 `-g`를 추가하는 것입니다.

```diff
-set mode-keys vi
+setw -g mode-keys vi
```

## 프로젝트 구조

```
bin/
  cc-navigator            런처 — 앱을 exec하고 오류를 표면화
  cc-navigator-hook       Claude Code 훅 shim — 모든 것을 삼키고 항상 exit 0
  cc-navigator-doctor     사전 요건 점검기
src/ccnav/
  paths.py       상태 디렉터리 (모드 0700)
  hookstate.py   훅 이벤트 → (상태, 이유); 순수 함수
  statestore.py  원자적 쓰기 / read_all / prune — 파일시스템의 유일한 소유자
  hook.py        shim의 로직
  proc.py        서브프로세스를 띄우는 유일한 지점; 타임아웃으로 제한
  tmuxctl.py     tmux 질의와 동작
  gnome.py       창을 제목으로 활성화한 뒤, xprop으로 실제로 일어났음을 증명
  model.py       상태 파일과 살아 있는 tmux 페인을 조인 → 행; 순수 함수
  ui.py          오버레이 창; 포매팅은 위젯 위의 순수 함수
  doctor.py      사전 요건 점검, 세그폴트 재현 포함
  app.py         배선 — 블로킹 작업은 GTK 메인 스레드에서 절대 돌지 않음
```

설계, 계획, 그리고 작업별 엔지니어링 기록은 [`docs/`](docs/) 아래에 있습니다 —
[`docs/superpowers/sdd/implementation-log.md`](docs/superpowers/sdd/implementation-log.md)
부터 읽으세요.

## 현재 상태

코어는 완성됐고 전체 스위트가 초록입니다(217개 테스트). end-to-end 사슬 — 훅 →
상태 파일 → 살아 있는 tmux 조인 → `ccnav:<세션>`으로 주소가 붙은 대기 행 하나 —
을 개인 소켓에서 한 번 검증했습니다.

아직 진행 중:

- **크리티컬 경로의 조용한 실패 두 건 수정**: 답장 전송이 실패했는데 UI는 성공을
  보고하는 문제, 그리고 죽은 것이 아니라 느린 tmux 때문에 살아 있는 세션의 상태
  파일이 정리되는 문제. 지금 다루는 중입니다.
- **실제 tmux 통합 테스트와 spike 보관.**
- 실제 데스크톱에서 아직 검증되지 않음: GNOME 이동 활성화와 답장 상자 포커스 복원
  — 둘 다 실제 화면 위의 창이 필요합니다.

이것은 특정 데스크톱 하나를 위해 만든 개인 도구이며, 패키징된 애플리케이션이
아닙니다. 요구 사항 섹션은 일반화의 출발점이 아니라 단단한 경계입니다.
