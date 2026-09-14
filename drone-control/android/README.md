# Android bridge source snapshot

`SampleCode-V5/android-sdk-v5-bridge/`는 실제 사용한 `com.msdkremote` bridge의 source/test/build.gradle입니다. 별도 예전 `13_DRONE/android/app` 또는 미연결 `android/bridge` 복사본을 섞지 않았습니다.

함께 포함한 overlay:

- `android-sdk-v5-as/settings.gradle`: :bridge module 등록.
- `android-sdk-v5-sample/build.gradle`: module dependency, 외부 API key/operator token 주입과 현재 build metadata.
- `MSDKManagerVM.kt`: SDK/product lifecycle와 PcBridge 연결.
- `FPVWidget.kt`, `FPVWidgetModel.kt` 및 Surface 정책/테스트: 영상 표시 수명주기 수정.
- `android-sdk-v5-uxsdk/build.gradle`: 순수 Surface 정책 단위 테스트 의존성.

전체 DJI SDK나 sample 프로젝트, SDK 바이너리, signing key, API key는 포함하지 않았습니다. 이 디렉터리는 독립 Android 앱 프로젝트가 아닌 source overlay입니다. 기준 sample은 [DJI Mobile-SDK-Android-V5 a48aa4e](https://github.com/dji-sdk/Mobile-SDK-Android-V5/tree/a48aa4e7811d824c27abfa973f5655579bfb8a77), aircraft/provided/networkImp5.18.0입니다. sample root에 같은 상대 경로로 overlay를 적용하는 구조입니다. 필요한 sample 나머지 파일·Gradle wrapper·의존성은 기준 upstream에서 확보합니다.

JDK17, Android SDK35/최소24, 기존 Gradle 설정 및 로컬 signing/API properties를 맞춘 뒤 `android-sdk-v5-as`에서 `:bridge:testDebugUnitTest :uxsdk:testDebugUnitTest :sample:assembleDebug`를 실행합니다. 값은 local Gradle 설정으로 관리하며 `AIRCRAFT_API_KEY`, `OPERATOR_ARM_TOKEN`, map/signing property를 코드에 박아 넣지 않습니다. 2026-09-10에는 원본 v2를 변경하지 않는 별도 전체 sample 복사본에 overlay를 적용해 APK를 빌드했습니다. 휴대폰 설치·실기 확인 상태는 검증 기록에서 별도로 확인합니다.

현재 source build ID는 **`5.18-connectivity.20260913.3`**, versionCode **20260913**입니다. APK의 versionName, telemetry의 `bridge_build_id`, PC의 `BUILD_ID`, 개인 site 설정의 `expected_bridge_build_id`를 같은 값으로 맞춰야 합니다. 이전 `.6` APK를 유지한 채 PC 설정만 바꾸면 실제 출발이 거절됩니다. 이 문서와 코드가 어긋나면 현장에서 원인을 찾기 어려우므로 `test_tool_live_boundary.py`가 두 값을 함께 검사합니다.

이번 빌드는 SDK/USB/FC 진단 기록을 추가하며, `.6`의 첫 기체 연결 후 구독 설치와 모터 상태 200ms 조회를 유지합니다. TCP 연결·SDK 등록 성공과 실제 기체 연결은 별개입니다. 실제 기체는 `bridge_health.product_connected`로 확인하며, 미확인 값을 연결 성공으로 표시하지 않습니다. 자동 SDK 복구와 자동 출발·재개는 활성화하지 않습니다. [첫 연결 실기 점검](../docs/STANDALONE_TAG_SHUTTLE.md)과 [기존 구현 검증 기록](../docs/IMPLEMENTATION_VALIDATION_20260910.md)을 구분해서 확인합니다.

DJI sample/UX 원본의 attribution을 보존합니다. [DJI upstream license](DJI-LICENSE.txt)는 sample의 MIT 조건과 SDK의 별도 EULA를 구분합니다. SDK 바이너리는 재배포하지 않았습니다.

새 PC에서는 저장소 루트의 `scripts\build-android.ps1 -PrivateProperties <외부 build.local.properties>`로
고정 upstream과 overlay를 함께 준비합니다. properties에는 `DJI_API_KEY`, `OPERATOR_ARM_TOKEN`,
절대 경로 `STORE_FILE`, `STORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`가 필요합니다.
Git에 값을 넣지 않으며 키·서명 정보는 별도 로컬 인계 묶음에서 가져옵니다.

private `files/field-diagnostics`에는 최대 4개×2MiB JSONL이 남습니다.
`scripts\collect-phone-diagnostics.ps1 -Serial <ADB ID>`로 OneDrive 밖에 수집합니다.
SDK 오류 최초 전이·회복과 init/destroy 호출 위치를 기록하지만 SDK 내부 장애의 근본 해결을
주장하지 않습니다. 원본 logcat과 진단 자료를 공개 업로드하지 마세요.
