# 마지막 시험의 참고 실행 코드

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
