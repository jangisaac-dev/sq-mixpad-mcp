# SQ MixPad MCP

Allen & Heath SQ 믹서의 원격 앱 SQ MixPad 1.6.1(macOS)을 AI가 조작할 수 있게 한다. 앱 사본에 작은 브리지를 넣어 앱의 내부 객체를 읽고 쓰고, 이를 MCP 서버로 감싸 Claude 같은 AI가 도구로 호출한다. 원본 앱은 건드리지 않는다.

Allen & Heath와 관계없는 개인 프로젝트다. MixPad 앱과 그 안의 QML은 이 저장소에 담지 않는다. 각자 설치한 MixPad의 사본에 브리지를 넣어 쓴다.

## 설치

필요한 것: SQ MixPad 1.6.1, Xcode Command Line Tools, [uv](https://docs.astral.sh/uv/). 아래 명령은 모두 이 저장소 폴더에서 실행한다.

1. 빌드용 Qt 6.5.0 받기(앱에 들어 있는 Qt와 같은 버전, `qt/`에 설치):
   `uvx --from aqtinstall aqt install-qt mac desktop 6.5.0 clang_64 --archives qtbase qtdeclarative -O qt`
2. MixPad 사본 만들기. 사본의 실행 파일만 ad-hoc으로 다시 서명한다. hardened runtime이 빠져야 브리지를 넣을 수 있다:
   `cp -R "/Applications/SQ MixPad 1.6.1.app" "SQ MixPad Bridge.app" && codesign --force --sign - "SQ MixPad Bridge.app"`
3. 브리지 빌드: `bash bridge/build.sh`
4. MCP 등록(Claude Code 예): `claude mcp add sq-mixpad -- uv run --script "$PWD/sq_mcp.py"`. 다른 AI 클라이언트에도 같은 명령을 stdio 서버로 등록한다.

## 사용

1. `bash run-bridge.sh`. 브리지가 들어간 사본 앱이 뜬다.
2. 앱에서 평소처럼 믹서(또는 Offline)를 고르고 Connect.
3. AI에서 `sq-mixpad` 도구를 쓴다.
4. 터미널에서 직접 확인할 때: `python3 sqctl.py '{"op":"singleton","uri":"ConnectionInterfaceSingleton","type":"ConnectionInterface"}'`

MCP 도구는 12개다.

| 도구 | 하는 일 |
|---|---|
| `sq_status` | 모드(offline/online), 세션 진입 여부(`connected`, 오프라인 세션도 true라 실기기 연결을 뜻하지 않는다), 현재·다음 씬, 앱 버전 |
| `sq_channels(kind, mix)` | input, aux, group, main, matrix, fx_send, fx_return, dca, mute_group, soft_key 중 한 종류의 채널 표기, 이름, 뮤트, 페이더(dB) |
| `sq_set_mute(channel, on)` | 뮤트를 켜거나 끄고 다시 읽은 값을 돌려준다. `channel`은 `Ip1`, `Aux3`, `MainLR` 같은 표기(대소문자·공백 무시) |
| `sq_set_fader(channel, db, mix)` | 페이더를 dB로 맞춘다. 최대 +10, -90 이하는 -inf. 입력·FX 리턴 같은 소스 채널은 `mix`(기본 `MainLR`, 그 밖에 Aux1–8, Grp1–4 등 앱의 믹스 버튼)로 보내는 레벨이고, Aux·MainLR 같은 출력 채널은 마스터 레벨이다 |
| `sq_select_channel(channel)` | EQ·게이트·컴프 등 처리를 다룰 채널을 선택하고 처리 드라이버 묶음(`channelDrivers`) 참조를 돌려준다 |
| `sq_recall_scene(number)` | 씬 호출(1부터) |
| `sq_root`, `sq_inspect`, `sq_find`, `sq_get`, `sq_set`, `sq_call` | 앱의 모든 객체를 직접 조회·변경하는 범용 도구 |

잘못된 채널·믹스, 해당 기능이 없는 채널, 없는 속성, 앱이 꺼진 경우는 원인을 담은 오류로 돌아온다. 앱이 5초 안에 답하지 않으면 "적용 여부 불명" 오류를 내고 다시 보내지 않는다.

## 앱 분석 결과

| 항목 | 내용 |
|---|---|
| 번들 | `com.allenheath.SQU.MixPad`, 1.6.1 (빌드 92418) |
| 실행 파일 | Mach-O x86_64 하나뿐이라 Apple Silicon에서는 Rosetta로 돈다. 심볼은 제거됐고 RTTI 클래스 이름은 남아 있다 |
| UI | Qt 6.5.0 + Qt Quick(QML 458개를 바이너리 리소스에 평문으로 내장) |
| 서명 | A&H Developer ID(7SGLXZYF29), hardened runtime, 별도 entitlement 없음 |
| 통신 | 자체 프로토콜 CMNet2(TCP 제어 + UDP 탐색·미터). 믹서를 찾을 때 각 네트워크 인터페이스에서 UDP 탐색을 한다 |
| 오프라인 모드 | 믹서 없이 앱 안의 오프라인 쇼를 편집한다. 처음 열면 SQ-7 템플릿을 쓴다 |
| 접근성(AX) | 창 안 요소가 0개라서 화면 읽기 기반 자동화는 픽셀 좌표로만 가능하다 |
| 버전 규칙 | MixPad와 SQ 펌웨어의 앞 두 자리(1.6)가 같아야 연결된다 |

검토한 제어 경로는 세 가지였다.

| 경로 | 범위 | 장점 | 한계 |
|---|---|---|---|
| **앱 내부 브리지 (채택)** | 앱 백엔드가 QML에 노출한 객체 전체(뮤트, 페이더, 센드, 처리, 라우팅, 씬, 소프트키 등). 실제로 확인한 범위는 아래 표 | 오프라인에서도 검증할 수 있고, 앱이 믹서와의 통신을 그대로 처리한다 | 앱이 실행 중이어야 한다. 앱을 업데이트하면 다시 빌드해야 한다 |
| 공식 SQ MIDI(TCP 51325) | 레벨, 뮤트, 팬, 할당, 씬, 소프트키 | 공식 문서가 있고 펌웨어가 바뀌어도 안정적이며 앱이 필요 없다 | EQ, 다이내믹스, 프리앰프는 다룰 수 없다. 실기기가 있어야 검증된다 |
| GUI 픽셀 클릭 | 화면에 보이는 것 | 아무것도 설치하지 않는다 | AX가 없어 느리고 깨지기 쉽다 |

## 제어 범위와 검증 상태

| 기능 | 브리지에서의 위치 | 상태 |
|---|---|---|
| 채널 뮤트 | `sq_set_mute` (`channel.muteDriver.value` 0/1) | 오프라인에서 화면 반영과 복구까지 확인 |
| 페이더(dB) | `sq_set_fader` (`faderDriver.floatValue`, 변환 `getFloatValueForDBValue(dB*256)`) | 오프라인에서 화면 반영과 복구까지 확인. 내부 값이 255단계라 일부 값은 반올림된다(-15 dB를 쓰면 -14.8 dB). 도구가 다시 읽은 값을 돌려준다 |
| 믹스별 센드 레벨 | `sq_set_fader(..., mix=)`. 소스 채널의 `faderDriver`는 선택된 믹스(`selected.mix`)로 가는 센드를 가리키므로, 도구가 믹스를 잠깐 선택했다가 원래대로 되돌린다 | 확인. Ip1을 Aux1으로 -10 dB 보낼 때 LR 레벨은 그대로였고 선택도 MainLR로 돌아왔다. Ip5–9에 서로 다른 값을 Aux1으로 보낸 5회도 모두 유지됐다. Aux1·MainLR 마스터는 믹스와 무관했다. Grp 페이더의 동작은 미확인 |
| 채널 목록과 상태 | `sq_channels`, `sq_status` | 오프라인에서 10종 모두 확인(입력 48, Aux 12, Grp 12, MainLR, MTX 6, FX Send 4, FX Return 8, DCA 8, Mute Grp 8, Soft Key 16) |
| MCP stdio 연결 | `uv run --script sq_mcp.py`를 stdio로 띄워 도구 목록, 상태, 뮤트, 페이더, 오류 응답 확인 | 확인. Claude Code 세션 안에서 도구로 부르는 것은 아직 확인하지 않았다 |
| 씬 호출 | `sq_recall_scene` (`sceneManager.recallScene(number-1)`) | 오프라인에서 확인. 빈 1번 슬롯에 씬을 저장(`storeScene(0, ...)`, 목록에 "Scene 1"로 표시)하고 Ip2를 뮤트한 뒤 `sq_recall_scene(1)`을 부르자 뮤트가 풀렸다. 시험 씬은 지웠다. 오프라인에서는 호출 뒤에도 `current_scene`이 "No Scene"으로 남았다 |
| 컴프 등 채널 처리 | `sq_select_channel` 후 `channelDrivers.compDriverHolder.thresholdDriver.value`(= dB×256 + 32768) 등을 `sq_set` | 컴프 threshold만 확인. Ip2를 바꾸고 Ip1이 그대로인지, 다시 선택했을 때 유지되는지, 복구까지 확인. 쓴 직후 선택을 바꾸면 값이 사라진다. 0초 대기에서는 유실됐고 0.3·0.5·1초 대기에서는 유지됐다(원인 미상, 경험값). 그래서 `sq_select_channel`은 바꾸기 전 1초를 기다린다 |
| 팬, PEQ, 게이트, 딜레이, 트림 | 범용 도구로 각 채널(`panDriver`, `sourceTrimDriver` 등)이나 `channelDrivers` 아래 드라이버 | 미검증. 객체 위치까지만 확인 |
| 라우팅, IO 패치, 소프트키, 라이브러리 | 범용 도구로 `ioPatchManager`, `softKeyManager`, `libraryManager` | 미검증 |
| 쇼 Push/Pull | `showManager` | 쓰지 말 것. Push는 콘솔의 현재 쇼 전체를, Pull은 MixPad의 오프라인 쇼를 덮어쓴다. 전송 중에는 다른 원격 연결이 끊긴다 |
| 실기기 온라인 조작 | 전부 | 미검증. 이번 작업은 실기기 없이 오프라인 모드로만 확인했다. 실기기에서는 사용자 권한(User Permissions) 제한, 상태 동기화, 재연결을 따로 확인해야 한다. QML이 하는 권한 검사(`isAllowedForCurrentUser`)는 브리지 직접 호출에서는 거치지 않으므로, 막힌 파라미터를 콘솔이 거부하는지도 확인이 필요하다 |

범용 도구로 새 기능을 찾을 때는 앱의 QML을 보면 빠르다. QML에서 `ConnectionInterface.` 뒤를 따라가면 어떤 객체와 속성을 쓰는지 보인다. 저장소에는 담지 않았으므로, 브리지가 떠 있을 때 아래 명령으로 `qrc-dump/`에 꺼낸다(앱 자체 파일 약 265개):

```
python3 -c 'import os,sqctl; os.makedirs("qrc-dump",exist_ok=True); [open("qrc-dump/"+p[2:],"w").write(sqctl.call({"op":"read","path":p})) for p in sqctl.call({"op":"qrc"}) if p.count("/")==1 and p.endswith((".qml",".js"))]'
```

## 구성 파일

| 파일 | 역할 |
|---|---|
| `bridge/sqbridge.cpp`, `bridge/build.sh` | 앱에 넣는 브리지(약 190줄). `~/.sqbridge.sock`(소유자만 접근)에서 줄 단위 JSON 요청을 받는다. 코드 실행 기능은 없고 객체 조회, 속성 읽기·쓰기, 공개 메서드 호출만 한다. 값이나 인자로 `{"id":N}`을 주면 그 객체를 넘기고, 오버로드는 `"get(int)"`처럼 시그니처로 고른다 |
| `SQ MixPad Bridge.app` | 설치 2단계에서 만드는 원본의 사본(저장소에 없음) |
| `run-bridge.sh` | `open -n --env DYLD_INSERT_LIBRARIES=...`로 사본을 실행한다. 이미 실행 중이면 거부한다(두 번째 인스턴스가 소켓을 가로채지 않도록). 로그는 `/tmp/sqbridge.log` |
| `sqctl.py` | 브리지 클라이언트(요청 1건 보내기, 응답 제한 5초) |
| `sq_mcp.py`, `test_sq_mcp.py` | MCP 서버와 단위 테스트(가짜 브리지 사용). 테스트: `uv run --no-project --with 'mcp>=2,<3' python -m unittest test_sq_mcp` |
| `qrc-dump/` | 위 명령으로 꺼낸 앱 QML·JS(저장소에 없음) |
| `qt/` | 설치 1단계에서 받는 빌드용 Qt 6.5.0(저장소에 없음). 다른 곳에 있으면 `QT_DIR=<경로>/6.5.0/macos bash bridge/build.sh` |

## 주의

- 앱이 실제 믹서에 연결돼 있으면 모든 변경이 콘솔에 즉시 반영된다. 예배·공연 중에는 AI에 맡길 범위를 정해 두고 쓴다.
- 객체 id는 앱 프로세스마다 다른 범위(pid×10⁶부터)로 매겨진다. 앱을 다시 켜면 이전 id는 `unknown or deleted id` 오류로 거부된다(확인함). 전용 도구는 매번 채널을 새로 찾는다.
- 도구가 믹스·채널 선택을 바꾸면 MixPad 화면에도 그대로 보인다. 사람이 MixPad를 함께 만지는 중이면 선택이 서로 엇갈릴 수 있다.
- MixPad를 업데이트하면 설치 2단계로 사본을 다시 만들고, 앱의 Qt 버전(`strings "SQ MixPad Bridge.app/Contents/Frameworks/QtCore.framework/QtCore" | grep -m1 "Qt 6"`)이 바뀌었으면 그 버전으로 1단계를 다시 한 뒤 `bridge/build.sh`를 실행한다.
- 브리지는 같은 사용자 계정의 프로세스라면 누구나 쓸 수 있다. 다른 계정이나 네트워크에서는 접근할 수 없다.
