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

현재 source build ID는 **`5.18-connectivity.20260910.5`**, versionCode **20260910**입니다. FC/query/video/PC 변경, 기본 OFF 복구와 watchdog을 구현했으며 bridge 63개와 UX 10개 단위 테스트가 통과했습니다. 자동 SDK 복구 활성화는 수동 sample SDK 경로 전체의 배타성이 확인되기 전까지 막혀 있습니다. [현재 연결 안내](../docs/CONTROL_INTEGRATION_20260910.md)와 [검증 기록](../docs/IMPLEMENTATION_VALIDATION_20260910.md)을 따릅니다.

DJI sample/UX 원본의 attribution을 보존합니다. [DJI upstream license](DJI-LICENSE.txt)는 sample의 MIT 조건과 SDK의 별도 EULA를 구분합니다. SDK 바이너리는 재배포하지 않았습니다.
