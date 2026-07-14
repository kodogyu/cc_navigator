# Changelog

## 0.2.0 — branch `cc_navigator_chan`

`0.1.0`(master) 대비 아래 기능들을 추가합니다. (master 미변경)

### VSCode 세션 지원
- VSCode 확장(extension-hosted)으로 실행된 Claude 세션은 tmux pane이 없어 목록에
  안 떴으나, 이제 표시됩니다. tmux 대신 **claude 프로세스 PID**로 생존을 판정
  (`procstat.py`), PID 재사용까지 방어.
- 감지 신호: `CLAUDE_CODE_ENTRYPOINT=claude-vscode`.
- **세션 이름**: 트랜스크립트의 `ai-title`(Claude가 만든 세션 제목)을 읽어 헤드라인으로
  사용 — 같은 워크스페이스의 여러 세션도 구분됨. `<ide_opened_file>` 같은 합성 프롬프트는
  헤드라인에서 제외.
- **탭 단위 이동**: 확장의 URI 핸들러
  (`vscode://Anthropic.claude-code/open?session=<id>`)로 정확한 세션 탭으로 전환.
  올바른 워크스페이스 창을 먼저 띄운 뒤(검증 가능) 탭을 전환(best-effort).

### Ubuntu 독 통합
- 런처(`.desktop`)에 앱 전용 아이콘과 `StartupWMClass` 추가, 창에 고유
  `WM_CLASS`(`io.github.kodogyu.CcNavigator`) 설정 → 독에 고정한 아이콘이 실행 창과 연결됨.
- **단일 인스턴스**: flock 잠금으로, 이미 실행 중일 때 독 아이콘을 누르면 새 창을 띄우지
  않고 **기존 창을 포커스**하고 종료.

### 사용량 표시 (패널 하단)
- 5분마다 백그라운드로 갱신 (UI 비차단).
- **주간 사용량 %** (주황): Claude 구독 API `/api/oauth/usage`의 `seven_day.utilization`.
- **Token Usage 바** (초록): `ccusage`로 계산한 이번 주(월요일~오늘) 달러 사용액을 주간
  예산 대비 %로. 바 안 가운데에 `NN%  $XXX` 표시.

### 테마
- 선택 가능한 4가지 컬러 테마: **Midnight(민트) / Nord Dark / Graphite Terminal /
  Clean Light** (`themes.py`, 팔레트 → `.ccnav` 스코프 CSS). 파생 색조는 알파 오버레이라
  어떤 배경색에서도 동작.
- 설정에서 **테마 선택** + **배경색 / 진한 색(헤더)** 커스텀 오버라이드.
- **설정창 자체도 테마 적용** (헤더바, 체크박스, 슬라이더, 스핀버튼, 포커스 색 등) — 기존
  기본 라이트 GTK 테마 탈피.

### 기타
- 모든 변경에 테스트 추가/갱신 (`test_vscode`, `test_procstat`, `test_usage`,
  `test_themes` 신규 포함). 총 555개 통과.
- 모든 기능은 `.ccnav` 스코프 CSS로 이 패널에만 적용되어 다른 GTK 앱/시스템 테마에 영향 없음.
