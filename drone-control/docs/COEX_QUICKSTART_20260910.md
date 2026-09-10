# 코엑스 무태그 왕복 실행

**폰 앱 재설치 없이 사용할 PC 실행기를 준비했다.** 기존 앱 build `5.18-telemetry-age.20260906.4`와 비공개 PC 설정의 기존 확인 토큰을 사용한다. 이 작업에서 앱 설치·기체 연결·실제 비행은 하지 않았다. 현재 검증은 모의 기체 및 가짜 소켓에 한정한다.

동작: 이륙 → 하방 기준 SDK 표시 1.8m에서 안정화 → 좌측 약 1m → 2초 호버링 → 우측으로 추정 출발점 복귀 → 3초 호버링 → VS 해제/RC 인계 → 사용자가 RC로 착륙.

최대 수평 명령 0.20m/s, 상승 0.18m/s다. 거리·복귀는 SDK 실제 속도를 적분한 추정값이다. 시간 경과만으로 이동 성공을 만들지 않는다. AprilTag가 없어도 실행할 수 있지만 이 장소의 실제 거리 정확도는 감독 비행으로 확인해야 한다.

## 내일 이 PC에서 실행

휴대폰–RC-N2 USB 연결, 기존 시험 앱 실행, PC–휴대폰 네트워크 연결 후 **현재 폰 IP**를 사용한다. 지난 hotspot IP를 고정하지 않았다. RC 조종자가 기체를 직접 보고 조작할 수 있고, 센서가 가려져 있지 않으며 1.8m 높이와 좌우 이동 구역이 확보된 상태에서 실행한다.

PowerShell에서 아래 폴더로 이동한다.

```powershell
cd C:\dev\publish\industry-day-drone-feat-drone-control\drone-control\trials
```

먼저 이륙하지 않는 지상 연결 검사. 꺾쇠 부분을 실제 IP로 바꾼다.

```powershell
.\START_COEX.ps1 -PhoneIp '<현재 폰 IP>'
```

검사가 통과한 뒤 1회 왕복을 실행한다. 실행기 내부에서도 이륙 직전 상태를 다시 검사한다.

```powershell
.\START_COEX.ps1 -PhoneIp '<현재 폰 IP>' -Execute
```

`-Execute`는 현장 구역·RC 준비를 확인한 감독 비행 지시다. 프로그램을 여는 것만으로 이륙하지 않는다. 휴대폰 연결만으로 자동 시작하지도 않는다. **RC 스틱 개입 뒤에는 재-arm·재이륙·자동 재개하지 않는다.** 정상 복귀 후에도 자동 착륙 명령을 보내지 않는다.

이 PC의 기존 Python은 `C:\dev\13_DRONE\.venv\Scripts\python.exe`, 비공개 설정은 `C:\dev\13_DRONE\pc\config.local.json`이다. 실행기가 이 경로를 사용하며 비밀 토큰은 터미널 인자나 로그에 노출하지 않는다. 다른 PC에서는 `-ConfigPath`로 실제 설정을 지정하고 Python3.11 이상을 준비한다. 센서/제어의 필수 코드에는 새 pip 패키지가 없다. 선택적 영상 기록은 기존 PyAV/OpenCV를 사용한다.

PowerShell 스크립트 실행이 제한된 환경에서는 Python으로 직접 같은 명령을 실행할 수 있다. 전역 실행 정책을 바꿀 필요가 없다.

```powershell
C:\dev\13_DRONE\.venv\Scripts\python.exe -B .\coex_tagless_left_return.py --host '<현재 폰 IP>' --execute --site-ready
```

## 화면과 결과

화면에 `CLIMB`, `LEFT`, `BRAKE_LEFT`, `RIGHT`, `FINAL_HOVER`, `DONE`과 높이·부호 있는 좌우 추정값을 표시한다. 왼쪽은 음수다. RC 반환 확인 문구가 나오면 RC로 착륙한다. 최대 60초 동안 읽기 전용 조회로 착륙·실제 모터 정지를 확인하고 종료한다.

JSON 결과는 `route_completed_estimated`, `rc_handover_confirmed`, `landing_confirmed`를 각각 보고한다. 수평 timeout·센서 불명·SDK ACK 성공만으로 완료를 표시하지 않는다. 오류/확인 미완료는 exit code2, 지상 검사 통과 또는 왕복·인계·착륙 확인 완료는0이다. `Ctrl+C`도 정지/인계 처리로 들어간다.

**현재 앱에는 명령 시간 watchdog이 없다.** PC가 오류를 감지하면 연결이 유효한 경우 zero → VS 해제를 시도하고 제어 소켓을 닫는다. 완전한 Wi-Fi 단절에서는 이 메시지나 TCP 종료가 폰에 도달하지 못할 수 있으므로 RC 조종자가 즉시 인계해야 한다. 이번 PC 스크립트는 기존 앱 통신 오류나 앱 watchdog을 수정한 것이 아니다. 이 한계를 해결하는 앱 수정은 별도 재설치 작업이다.

## 로그

`drone-control/pc/logs/coex/` 아래 실행별 JSONL과 `.summary.json`을 저장한다. 전방·후방을 포함한 OA 전체 배열, 상하방 raw 값, height·velocity·yaw·age·SDK 오류, 요청/ACK와 sequence, 단계별 변위 추정, 중단/RC 반환/착륙 확인을 남긴다. 제어 연결 전 FC GET이 실패해도 오류를 기록한다. 토큰은 마스킹한다.

가능하면 같은 폴더의 `시각-camera/`에 1Hz 관측 이미지와 영상 오류를 기록한다. 영상은 위치 제어 입력이 아니며 영상이 없어도 센서/제어 검사는 동작한다. `--no-video`로 영상 기록을 생략할 수 있다. 70cm 같은 PC 장애물 거리 중단 조건은 없다. 거리 raw 값은 기록하며 기체의 회피 설정은 바꾸지 않는다.

## 기체 없이 확인

`drone-control` 폴더에서 아래 명령들은 기체에 접속하지 않는다.

```powershell
C:\dev\13_DRONE\.venv\Scripts\python.exe -B trials\coex_tagless_left_return.py
C:\dev\13_DRONE\.venv\Scripts\python.exe -B trials\coex_tagless_left_return.py --simulate
C:\dev\13_DRONE\.venv\Scripts\python.exe -B -m unittest discover -s trials\tests -v
```

전체 모의 실행은 명령 지연·속도 관성을 가진 합성 모델이다. 그 모델에서는 약35초에 왕복과 가상 RC 착륙이 완료됐으며, 이 숫자는 실제 비행 시간이나 거리 정확도를 보증하지 않는다. 모의 모드는 `physical_execution=false`와 `execution_mode=simulation`을 명시한다.
