# Android bridge source snapshot

`SampleCode-V5/android-sdk-v5-bridge/`는 실제 사용한 `com.msdkremote` bridge의 source/test/build.gradle입니다. 별도 예전 `13_DRONE/android/app` 또는 미연결 `android/bridge` 복사본을 섞지 않았습니다.

함께 포함한 overlay:

- `android-sdk-v5-as/settings.gradle`: :bridge module 등록.
- `android-sdk-v5-sample/build.gradle`: module dependency, 외부 API key/operator token 주입과 현재 build metadata.
- `MSDKManagerVM.kt`: SDK/product lifecycle와 PcBridge 연결.
- `FPVWidget.kt`, `FPVWidgetModel.kt`: 영상 수정 계획이 대상으로 삼는 현재 UX source.

전체 DJI SDK나 sample 프로젝트, SDK 바이너리, signing key, API key는 포함하지 않았습니다. 이 디렉터리는 독립 Android 앱 프로젝트가 아닌 source overlay입니다. 기준 sample은 [DJI Mobile-SDK-Android-V5 a48aa4e](https://github.com/dji-sdk/Mobile-SDK-Android-V5/tree/a48aa4e7811d824c27abfa973f5655579bfb8a77), aircraft/provided/networkImp5.18.0입니다. sample root에 같은 상대 경로로 overlay를 적용하는 구조입니다. 필요한 sample 나머지 파일·Gradle wrapper·의존성은 기준 upstream에서 확보합니다.

JDK17, Android SDK35/최소24, 기존 Gradle 설정 및 로컬 signing/API properties를 맞춘 뒤 `android-sdk-v5-as`에서 `:bridge:testDebugUnitTest`와 `:sample:assembleDebug`를 검증해야 합니다. 값은 local Gradle 설정으로 관리하며 sample build.gradle에 정의된 `AIRCRAFT_API_KEY`, `OPERATOR_ARM_TOKEN`, map/signing property를 코드에 박아 넣지 않습니다. 이 게시 과정에서는 overlay APK를 빌드하거나 기체에 설치하지 않았습니다.

현재 source build ID는 `5.18-telemetry-age.20260906.4`입니다. [계획된 FC/query/video 수정](../docs/fix_ready_20260907/APPLY_ORDER.md)이 이 snapshot에 이미 적용된 것은 아닙니다. 계획 적용 시 새 APK/PC/profile 버전을 함께 검증합니다.

DJI sample/UX 원본의 attribution을 보존합니다. [DJI upstream license](DJI-LICENSE.txt)는 sample의 MIT 조건과 SDK의 별도 EULA를 구분합니다. SDK 바이너리는 재배포하지 않았습니다.
