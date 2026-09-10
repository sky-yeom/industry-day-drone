# 마지막 시험의 참고 실행 코드

**카메라 인식·좌우 이동 단독 시험:** [새 배치 6→3→2→1→2→3→6 실행 안내](../docs/STANDALONE_TAG_SHUTTLE.md).
`START_TAG_SHUTTLE.ps1`에서 계획/카메라/비행을 구분합니다. Speech·Azure 없이 실행하며
바닥 0과 벽 출발 태그 6을 별도로 취급합니다. 기존 counted trial은 아래 기록대로 유지합니다.

별도 COEX 무태그 왕복의 PC 실행기를 구현했습니다. [내일 실행 방법](../docs/COEX_QUICKSTART_20260910.md), [상세 설계](../docs/COEX_TAGLESS_PLAN_20260910.md), [설정](profiles/coex_tagless_left_return_1m_1p8m.json)을 참고하세요. 기본 실행은 기체에 연결하지 않고 계획만 출력합니다. 실기는 명시적 실행 옵션이 필요하며 현장 비행 정확도는 아직 검증하지 않았습니다.

`counted_trial_23132_skip1_repeat_check.py`는 기존 supervised trial의 소스를 저장소 상대 경로로 옮긴 것입니다. `bounded_sonar_climb.py`에는 그 시험에서 실제 사용한 소나 함수2개만 AST를 바꾸지 않고 추출했습니다. 별도 과거 왼쪽70cm 검사/CLI는 추출하지 않았습니다.

- 정지 방문2→3→1→3→2, 처음과 마지막2↔3 이동에서는 ID1 통과.
- 소나 표시1.4m, 상승8초, 기울기 최대1.5°, 구간45초.
- 원시 센서 기록 유지, PC 장애물 거리 중단 제외, 기체 회피 설정 변경 없음.
- 이 코드는 Speech Tool이나 새 실기 adapter에 연결돼 있지 않습니다.
- 실제 기체 동작은 `--execute`와 현장 검증된 비공개 `pc/config.local.json`이 필요합니다. 실행 지시 없이 import/계획 확인만으로 이륙하지 않습니다.

기체 연결 없이 계획 문구 확인:

```powershell
python -B trials/counted_trial_23132_skip1_repeat_check.py --host 127.0.0.1
```

이식 과정에서는 경로/import와 참고 코드 구성을 정리했으며 새 비행을 하지 않았습니다. 원본 로그/사진은 공개하지 않았습니다. 과거 trial이 현재 새 service의 실기 준비 완료를 뜻하지 않습니다. reusable adapter로 옮길 구체 변경은 [드론 Tool 제어 계획](../docs/fix_ready_20260907/DRONE_TOOL_CONTROL_IMPLEMENTATION_PLAN_20260908.md)에 있습니다.
