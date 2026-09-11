# 영상 활성화·휴대폰 검은 미리보기 수정 명세 — 2026-09-07

상태: **구현 준비 완료 / 코드 미적용 / 빌드·설치·실기 검증 미실행**. 비행 중인 기체·휴대폰·SDK에 접속하거나 명령을 보내지 않고 소스와 기존 기록만 검토했다. 이 문서는 영상 범위의 적용 순서, 함수 동작, 테스트와 배포 기준을 고정한다. FC 조회·OA 구독 복구는 별도 담당 문서와 통합한다.

## 1. 관측과 수정 목표

- 사용자 관측의 최종 정정은 **버튼은 보이고 영상은 검정, 모터는 꺼져 있으며 RC 스틱만 조작하자 영상이 살아남**이다. 실제 기체 이동·모터 시동·앱 전체 정지라고 바꾸어 기록하지 않는다. 당시 Activity 이름과 검정 화면 발생 시각은 미확인이다.
- 과거 `camera_age_ms=6~22`는 Android 압축 영상 callback이 왔다는 증거다. 휴대폰 디코딩/표시가 정상이라는 증거는 아니다. PC 클라이언트가 없을 때 `delivered_frames=0`은 정상일 수 있다.
- 재현 가능한 소스 결함은 두 가지다. `AvailableCameraListener`가 늦게 도착한 `enabled=false`를 저장만 하고 다음 재등록에서 지워 활성화 요청을 놓친다. `FPVWidget`은 파괴된 Surface 참조를 남겨 이후 크기 0의 Surface를 등록할 수 있다. **어느 것이 사용자의 실제 검정 화면 원인인지는 아직 미확정**이다.
- 목표는 스틱 조작 없이 영상 구독과 유효 Surface가 준비되는 즉시 시작하고, 실패 계층을 raw→PC 전송→PC decode 및 phone decode→phone Surface로 구분하는 것이다. 비행 제어와 자동 스틱/모터 입력은 추가하지 않는다.

## 2. 수정할 실제 파일

아래 `B`는 `drone-control/android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livevideo/`, `U`는 `drone-control/android/SampleCode-V5/android-sdk-v5-uxsdk/src/main/java/dji/v5/ux/core/widget/fpv/`다. 빌드 기준은 v2 소스이며 `drone-control/android/app`의 과거 복사본을 빌드 대상으로 삼지 않는다.

| 파일·함수 | 정확한 변경 |
|---|---|
| [AvailableCameraListener.java:40](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livevideo/AvailableCameraListener.java#L40) `ensureBound`, callback, `bind`, `detach` | listener 설치와 영상 enable 조정을 분리. 원하는 상태와 실제 보고 상태를 분리. camera/manager/세대에 맞는 비동기 callback 처리 및 제한된 재시도 |
| 새 `B/StreamActivationPolicy.java` | Android/SDK 없는 순수 Java 상태 전이와 시간 제한. fake clock으로 테스트 |
| [FrameBuffer.java:35](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livevideo/FrameBuffer.java#L35) `diagnostics`, `addFrame`, `nextKeyFrame` | 현재 camera 세대의 첫 raw/keyframe과 누적 통계 구별. 원래 reader 세대와 혼합하지 않음 |
| [VideoServerManager.java:49](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livevideo/VideoServerManager.java#L49) `ensureSdkBinding` | 영상 바인딩/전송의 회복 사유·차단 사유를 구조화. ground 조건과 SDK reset 범위를 확대하지 않음 |
| [DeliveryHealth.java:26](../../android/SampleCode-V5/android-sdk-v5-bridge/src/main/java/com/msdkremote/livevideo/DeliveryHealth.java#L26) | 기존 3초 감시·10초 간격은 이번 패치에서 유지. keyframe 대기와 실제 socket write 정지를 다른 사유로 표기 |
| [FPVWidget.kt:83](../../android/SampleCode-V5/android-sdk-v5-uxsdk/src/main/java/dji/v5/ux/core/widget/fpv/FPVWidget.kt#L83), `onAttachedToWindow`, `onDetachedFromWindow`, `updateCameraStream` | 유효 Surface만 등록, 파괴 시 참조 종료, 동일 binding 중복 등록 방지, detach 시 반드시 해제 |
| [FPVWidgetModel.kt:171](../../android/SampleCode-V5/android-sdk-v5-uxsdk/src/main/java/dji/v5/ux/core/widget/fpv/FPVWidgetModel.kt#L171) `put/removeCameraStreamSurface`, `inCleanup` | 등록에 사용한 manager를 보존해 동일 소유자에게 해제. 성공 여부를 widget에 반환 |
| 새 `U/SurfaceBindingPolicy.kt` | Surface identity/유효성/크기/camera/manager/attached 입력을 받는 순수 상태 전이. UI thread에서만 SDK effect 실행 |
| 새 `U/PreviewDiagnostics.kt` | 폰 Surface 상태와 선택적으로 활성화하는 decoded-frame probe의 제한된 통계. UX→bridge 의존성은 추가하지 않음 |
| [vision.py:68](../../pc/drone_nav/vision.py#L68) `TcpVideoStream._decode`, `diagnostics` | PC socket bytes/packet/decode의 세대별 첫 수신·마지막 진전 시각을 추가. 현재 5초 decode timeout은 이번 패치에서 변경하지 않음 |

기준 SHA256: `AvailableCameraListener.java=70F417AF6F12E9DF343A13BC906BDD2BBBEB0F1EC23545CC3986AA507ED4B5AC`, `FPVWidget.kt=A0E3A01071E643E75ABA548B0B5A56E86DF47163E8C247B1A24BB5D8CC61127E`, `FPVWidgetModel.kt=F2807203C1BF861E0B17C07E8D38875B9CBD8816BF7FF82268F38E1C06D5DB0A`. 적용 직전 달라져 있으면 기존 변경을 보존하며 현재 함수에 반영하고 이 해시로 덮어쓰지 않는다.

## 3. 영상 활성화 상태 전이 — 구현 결정

`enabled` 하나를 아래 상태로 바꾼다. 카메라 선택은 기존 MAIN 우선 정책을 유지하되, 상태 map 조회는 고정 `LEFT_OR_MAIN`이 아니라 **실제 선택 camera**로 한다.

```text
desiredEnabled: running 동안 true
reportedEnabled: Boolean?  // 보고 없음은 null, false와 구분
reportedStates: Map<camera, Boolean> // current manager 세대 내부에서만 사용
bindingGeneration, managerIdentity, selectedCamera
receiverRegistered, availableListenerRegistered
activationAttemptCount, lastActivationAtMs, nextActivationAtMs
enableReportAtMs, firstRawAtMs, lastRawAtMs, firstKeyframeAtMs
lastActivationError, activationState
```

`activationState`는 `STOPPED`, `WAIT_MANAGER`, `WAIT_CAMERA`, `WAIT_ENABLE_REPORT`, `WAIT_RAW`, `STREAMING`, `STALLED` 중 하나다. 등록 성공을 `STREAMING`으로 표시하지 않는다. 실제 해당 세대 raw 수신이 필요하다.

1. 모든 lifecycle/enable SDK effect는 하나의 video worker에서 순서대로 수행한다. `ScheduledThreadPoolExecutor(1)`를 `AvailableCameraListener`가 소유하고 thread 이름을 `video-binding`으로 고정한다. `start/ensure/stop/recover`는 work를 enqueue한다. process lifetime worker이며 Product disconnect에서는 SDK listener만 해제한다. 앱 종료용 `close()`에서 executor를 종료한다. 일반 `stopListener()`는 재시작 가능한 상태다.
2. `AtomicLong requestedEpoch`를 둔다. `stopListener()` 또는 새 Product/manager 세대 진입 시 enqueue 이전에 증가시켜 늦은 raw callback을 즉시 무효화한다. worker state와 요청의 ticket을 대조한다. raw callback은 SDK byte 배열을 callback 안에서 `Frame`으로 복사한 뒤 ticket을 `FrameBuffer.addFrame(frame, cameraGeneration)` 안에서도 검사한다. SDK가 반환 뒤 재사용하는 byte 배열을 지연 queue에 넣지 않는다.
3. 동일 manager·camera에서 raw가 없다는 이유만으로 매번 available listener를 detach하지 않는다. 현재 등록 상태를 유지한 채 `reconcileActivation(now)`를 수행한다. manager 교체/명시 disconnect/선택 camera 변경 시에만 기존 listener 소유자를 교체한다.
4. available-list callback에서 MAIN 우선 camera를 선택한 후 receiver를 한 번 등록한다. callback 전에 enabled map이 오면 그 map을 해당 세대 안에 저장한다. camera 선택 시 map에서 해당 camera 상태를 읽고 reconcile한다. 빈 available list이면 현재 camera 사용 가능 상태를 해제하고 `WAIT_CAMERA`로 기록한다. 예전 camera raw를 새 상태의 정상 증거로 사용하지 않는다.
5. `onCameraStreamEnableUpdate`는 ticket과 manager를 검사한 뒤 map을 복사해 저장하고 **그 callback 처리 안에서 reconcile을 예약**한다. 등록 직후 즉시 실행된 callback과 늦은 callback 모두 동일하게 처리한다. callback 안에서 SDK enable을 재귀 호출하지 않는다.
6. camera가 실제 available list에 있고 `desiredEnabled=true`이며 report가 false이면 `enableStream(selected,true)`를 호출한다. report가 null이고 새 raw도 없으면 해당 세대 첫 활성화 시도는 허용한다. 명령 직후 true로 덮어쓰지 않는다. `enableStream`은 void이므로 반환은 적용 확인이 아니다.
7. 연속 시도 사이 최소 간격은 3→6→12→24→30초, 이후 30초다. false callback 반복이나 중복 ensure 호출도 이 제한을 통과해야 한다. SDK 호출 전에 attempt/next time을 저장해 동기 callback으로 중복 호출되는 것을 막는다. 실제 raw가 들어와 `STREAMING`이 되면 다음 장애를 위한 간격을 3초로 초기화한다.
8. reported true이고 raw가 없으면 `WAIT_RAW`, 3초가 지나면 `STALLED`로 표기한다. enable false/true 토글이나 decoder keepalive 변경으로 확대하지 않는다. 기존 지상 한정 delivery recovery가 적용될 조건이면 그 사유를 통해 listener 재등록만 요청한다. manager identity가 실제 바뀌면 시간 backoff보다 새 세대 바인딩이 우선이다.
9. teardown은 ticket 무효화→등록했던 manager에서 receiver/available listener 각각 제거→현재 camera/report/현재 세대 raw 통계를 reset 순서다. 한 remove가 예외여도 다른 remove를 시도한다. 타 모듈의 listener를 제거하거나 `enableStream(false)`를 보내지 않는다. 진단 snapshot은 immutable 값을 `AtomicReference`에 publish해 SDK worker를 STATUS 호출로 막지 않는다.

핵심 pseudo-diff:

```java
// Before: callback은 enabled 값만 저장하고 다음 ensure의 detach가 값을 지움.
onEnableReport(ticket, sourceManager, states) {
    worker.execute(() -> {
        if (!isCurrent(ticket, sourceManager)) return;
        reportedStates = new EnumMap<>(states); // 구현에서는 enum class로 빈 map 생성 후 putAll
        reportedEnabled = selectedCamera == null ? null : reportedStates.get(selectedCamera);
        policy.onReport(reportedEnabled, now());
        apply(policy.reconcile(now()));
    });
}
// apply(ENABLE)는 current ticket을 재확인하고 manager.enableStream(selectedCamera, true).
// enable의 반환으로 reportedEnabled=true 또는 rawAt=now를 기록하지 않는다.
```

상태 policy는 `LongSupplier clock`과 효과 enum(`NONE`, `ENABLE`, `REBIND_RECEIVER`)을 받아 Android 없이 테스트한다. callback race를 재현하는 adapter 테스트에는 직접 실행 executor와 수동 drain executor를 각각 넣는다. recovery 요청의 최종 ground 검사는 기존 `VideoServerManager`에서 수행하고, policy가 임의로 ground를 추정하지 않는다.

## 4. FrameBuffer 세대와 전송 진단

`readerGeneration`은 PC socket writer 교체용으로 그대로 둔다. 별도 `cameraGeneration`과 `beginCameraGeneration(ticket)`을 추가한다. begin 함수는 buffer lock 안에서 현재 camera 세대의 raw/keyframe 시각과 카운터를 미수신 상태로 초기화하고 queue를 비운다. lifetime counters는 보존한다. `addFrame(frame,ticket)`은 같은 lock 안에서 camera ticket을 검사한 뒤에만 새 raw 시각/프레임을 갱신한다. 오래된 callback 폐기 횟수는 따로 기록한다.

`nextKeyFrame()`은 이미 들어온 queue를 비우고 다음 I-frame을 기다리는 함수다. **기체에 I-frame 요청을 보내는 함수가 아니다.** 기존 이름은 호출부 변경을 줄이기 위해 유지하되 주석과 diagnostics에 `keyframe_request_sent=false`를 추가한다. SDK에 검증되지 않은 keyframe 요청 API를 만들어 넣지 않는다.

`FrameBuffer.delivered_frames`는 queue dequeue이며 실제 socket write는 `VideoServer`의 socket counters다. PC 디코딩은 `vision.py`의 독립 단계다. 이 세 수치를 같은 성공 지표로 취급하지 않는다. 현재 reader 세대 교체, 8 MB 단일-frame 상한과 큰 I-frame 보존 회귀 동작을 그대로 지킨다.

## 5. 휴대폰 Surface lifecycle — 구현 결정

현재 SDK는 같은 Surface의 반복 put 호출에서 camera/크기/scale을 갱신할 수 있다. 따라서 크기가 유효할 때 update마다 remove→put 할 필요는 없다. **Surface나 manager의 소유자가 바뀔 때만 기존 binding 해제**, 동일 소유자의 camera/크기 변경은 put update를 사용한다. [DJI ICameraStreamManager 공식 문서](https://developer.dji.com/api-reference-v5/android-api/Components/IMediaDataCenter/ICameraStreamManager.html)

1. widget에 `surfaceGeneration`, `attached`, `surface: Surface?`, `boundDescriptor`를 둔다. descriptor는 Surface identity, manager identity, camera, width, height, scale, generation으로 구성한다. SDK 등록에 성공한 뒤에만 저장한다.
2. `surfaceCreated`: 기존 참조와 다르면 이전 binding 해제, generation 증가, 새 holder.surface 저장. 크기를 0으로 시작하고 아직 put하지 않는다.
3. `surfaceChanged`: 현재 holder의 Surface를 저장하고 width/height를 갱신한 뒤 reconcile. callback의 holder가 현재 holder와 맞지 않는 지연 이벤트면 폐기한다.
4. `surfaceDestroyed`: **등록했던 Surface**를 remove한 뒤 finally에서 surface=null, width=0, height=0, descriptor=null, generation 증가. SDK remove 실패는 event에 남기되 Java/Kotlin 참조를 살아 있는 Surface처럼 재사용하지 않는다. SurfaceView 소유 Surface에 직접 `release()`를 호출하지 않는다.
5. `onDetachedFromWindow`: attached=false, generation 증가, bound Surface 해제, 그 뒤 widgetModel.cleanup. SurfaceHolder callback 자체는 현재처럼 widget 생성에서 한 번 등록하고 동일 widget reattach 때 중복 등록하지 않는다. detach에서 surface 참조도 비우되 reattach 시 holder.surface.isValid 및 holder.surfaceFrame 양수 크기로 실제 Surface를 다시 얻는다.
6. `onAttachedToWindow`: attached=true, model setup 이후 실제 holder 상태를 재평가한다. Surface가 유효하고 양수 크기면 바인딩, 아니면 surfaceChanged를 기다린다.
7. `updateCameraStream` 시작에서 main thread인지 검사하고 아닐 경우 현재 surfaceGeneration을 캡처해 post한다. 실행 시 ticket이 다르면 폐기한다. 조건 `attached && surface!=null && surface.isValid && width>0 && height>0 && camera!=UNKNOWN`을 통과해야 put한다. 불충족이면 기존 binding을 해제하고 즉시 return한다.
8. `FPVWidgetModel.putCameraStreamSurface`는 현 manager로 등록하고 실제 owner manager를 binding record에 저장한다. `removeCameraStreamSurface`는 다시 전역 manager를 조회하지 않고 그 record의 manager를 사용한다. `inCleanup`도 이를 해제한다. remove는 멱등 함수이며 이중 호출은 무해해야 한다.
9. 같은 descriptor이면 SDK 호출을 생략한다. put RuntimeException 발생 시 descriptor를 성공으로 저장하지 않고 error와 attempt를 기록한다. 새 valid callback 또는 명시 화면 복귀에서 다시 평가한다. 무제한 onLayout put 반복을 만들지 않는다.

```kotlin
private fun updateCameraStream() {
    // main thread / generation gate는 이 함수 진입부에서 처리
    val candidate = surface
    if (!attached || candidate == null || !candidate.isValid || width <= 0 || height <= 0 ||
        widgetModel.getCameraIndex() == ComponentIndexType.UNKNOWN) {
        clearBoundSurface()
        return
    }
    val wanted = descriptor(candidate, width, height, widgetModel.getCameraIndex())
    if (wanted == boundDescriptor) return
    if (boundDescriptor?.ownerDiffersFrom(wanted) == true) clearBoundSurface()
    if (widgetModel.tryPutCameraStreamSurface(candidate, width, height, SCALE)) {
        boundDescriptor = wanted
    }
}
```

프로젝트에서 실제 검정 화면이 Main 정보 화면인지 FPVWidget을 쓰는 화면인지 확인하는 Activity/fragment identity를 먼저 남긴다. FPVWidget을 쓰지 않는 화면에 이 patch 효과를 주장하지 않는다. `CameraStreamDetailVM`의 별도 Surface 화면은 실제 재현 화면으로 확인될 경우 같은 owner/validity helper를 적용한다. 현재 사용자 화면이 미확인이므로 모든 sample 화면을 일괄 수정하지 않는다.

## 6. 원인 계층을 구분할 최소 진단

모든 Android event는 `SystemClock.elapsedRealtime()`와 `process_session_id`, camera/connection/surface 세대를 포함한다. Android timestamp와 PC monotonic을 직접 빼지 않는다. PC log에 수신 시각과 원본 Android 시각을 함께 기록한다. 상태 변화를 event로 기록하고 주기 통계는 1 Hz, raw/decoded callback마다 파일을 쓰지 않는다. 인증값/원시 영상 픽셀은 상태 로그에 넣지 않는다.

| 경로 | 추가/보존할 필드와 의미 |
|---|---|
| activation | desired/reported, report age, manager identity, camera, generation, attempt/next time, last SDK exception |
| raw | current_generation raw frames/bytes/age, keyframe count/age, codec/width/height, old_callback_drops; lifetime 합계와 구분 |
| Android socket | client connected, socket generation, written frames/bytes/age, current write age, recovery reason 및 `ground_not_fresh` 차단 사유 |
| PC decode | generation, connected_at, first_byte, last_byte, parsed_packets, first_decode, last_decode, codec, reconnect reason, snapshot generation/age |
| phone UI | activity/fragment/widget identity, attached/visible, surface generation/valid/width/height, bind attempts/success/remove/error, last main-thread event |
| optional phone decode | probe_enabled, probe generation/frames/age/width/height/format, first-frame latency; raw와 폰 표시 사이의 독립 증거 |

폰 decode probe는 `ICameraStreamManager.addFrameListener(camera, YUV420_888, listener)`와 `removeFrameListener(listener)`를 사용한다. 실제 local sample signature는 `onFrame(ByteArray, offset, length, width, height, FrameFormat)`이다. callback에서는 counter·시각만 갱신하고 배열을 저장하지 않는다. main camera/FPV 화면에서 진단 기능을 켠 30초 동안만 붙이며 default는 OFF다. probe 추가 자체가 decoder 동작에 영향을 줄 수 있으므로 **probe OFF 상태 재현과 ON 상태 결과를 별도 표시**한다. 증거 확보를 위해 keepalive를 무조건 켜거나 probe를 영구 등록하지 않는다.

`surface bind success`도 실제 픽셀 표시 확인은 아니다. 1차 패치에 자동 'rendered=true'를 추가하지 않는다. 실제 화면 표시 판정은 같은 시각의 지상 사용자 관측/화면 기록을 사용한다. 향후 PixelCopy 같은 캡처를 추가하면 성공 callback도 최신 카메라 장면이라는 검증과 별개로 표기해야 한다.

## 7. 의미 있는 회귀 테스트 — 구현 직후 실행할 목록

bridge는 기존 JUnit 4.13.2를 사용한다. 새 `StreamActivationPolicyTest.java`와 adapter executor fake 테스트를 `android-sdk-v5-bridge/src/test/java/com/msdkremote/livevideo/`에 둔다. Surface policy는 Android 객체 대신 identity/validity 입력을 받아 순수 Kotlin JVM 테스트로 만들고, uxsdk build.gradle에 `testImplementation 'junit:junit:4.13.2'` 한 줄을 추가한다. 실제 SurfaceHolder 처리는 지상 장치 검증으로 별도 확인한다.

| 테스트 | 입력/이벤트 순서 | 반드시 검증할 결과 |
|---|---|---|
| A1 핵심 재현 | addListener 반환→늦은 false report→ensure | enable 정확히 1회, available listener는 반복 detach되지 않음; 기존 코드에서 누락되는 순서 |
| A2 동기 callback | addListener 호출 중 false callback | 재귀/중복 enable 없음; direct executor와 queued executor 모두 동일 |
| A3 camera 상태 순서 | enabled map 먼저→available list MAIN 또는 다른 camera | 실제 선택 camera의 보고 상태 사용, 다른 camera false로 MAIN을 잘못 enable하지 않음 |
| A4 report 없음 | camera available, report null, raw 없음 | 1회 enable 후 3초 이전 반복 없음; 3/6/12/24/30초 backoff |
| A5 성공 의미 | enable 반환→true report→raw 없음→첫 raw | 반환/true만으로 STREAMING이 아님, 첫 current raw에서만 전환 |
| A6 재연결 | old manager callback 지연→new manager 바인딩→old raw/report 도착 | old callback discard; old frame으로 new camera age가 fresh가 되지 않음 |
| A7 teardown | remove receiver가 예외→remove available | 두 번째 remove도 실행, state stopped, 이후 callback 무효 |
| A8 정지 race | raw callback 복사 중 stop/new generation | buffer lock 내 ticket 재검사로 old frame 삽입 차단 |
| F1 buffer | reader 교체/큰 I-frame/oversize | 기존 FrameBufferTest 유지 통과, cameraGeneration과 readerGeneration 독립 |
| S1 파괴 재현 | create→change(1920,1080)→destroy→camera source update | destroy 뒤 put 0회, remove 1회, surface 참조 null |
| S2 크기 없음 | created 또는 change(0,0), invalid Surface | put 0회; 후속 valid 양수 change에서 1회 |
| S3 중복 update | 동일 descriptor 반복, 크기 변경, camera 변경 | 동일은 put 0 추가; 동일 Surface/manager의 크기·camera 변경만 update |
| S4 manager 교체 | old manager에 bind→global manager 교체→cleanup | old manager에서 remove, 새 manager에 잘못 remove하지 않음 |
| S5 lifecycle | detach→late posted update→reattach valid holder | old generation update 무효; reattach 새 binding 한 번 |
| P1 PC decode | bytes 수신하지만 packet/decode 없음 | byte는 fresh, decode는 absent로 구별; 5초 timeout 숨기지 않음 |
| P2 PC reconnect | 연결1 decode→연결2 no frame | snapshot clear, old first_decode를 새 연결 성공으로 표시하지 않음 |
| D1 관측 영향 | decode probe OFF/ON/OFF | listener 제거·세대 폐기 확인, 항상 OFF 실기 기준도 남김 |

## 8. 적용·빌드·패키지·롤백 순서

1. 현재 시험 종료 후 독립 지상·모터 정지 확인. live runner/SDK control client 상태를 확인한 뒤 변경 작업을 시작한다. 문서 작성 현재에는 이를 수행하지 않았다.
2. authoritative 파일과 앱 버전/runner exact-build 검사 파일을 별도 백업하고 해시 manifest 작성. FC/OA 담당과 `PcBridge`, `VideoServerManager`, version bump 등 공유 파일 소유권을 합의한다. video worker는 다른 담당의 FC worker를 reset하지 않는다.
3. 진단 필드→activation policy+adapter→Surface policy+model owner→PC decode 진단 순서로 적용한다. 기능별 diff와 테스트 결과를 보존한다. 이미 고친 `.4` telemetry age ordering을 되돌리지 않는다.
4. JDK 17, 기존 `drone-control/android/SampleCode-V5/android-sdk-v5-as`에서 `gradlew.bat :bridge:testDebugUnitTest :uxsdk:testDebugUnitTest :sample:assembleDebug --console=plain`. Python 변경에는 기존 vision 테스트와 새 P1/P2 fake socket/decoder 테스트만 먼저 실행한다. 캐시로 가능한 패치에 SDK/Gradle/라이브러리 버전 업그레이드를 섞지 않는다.
5. 새 APK는 기존 서명으로 빌드하고 현재 설치값보다 높은 versionCode와 새 구분 가능한 versionName을 중앙 릴리스 담당이 하나만 부여한다. APK 해시/서명/manifest metadata와 source hash를 저장한다. runner의 `.4` exact-build gate는 새 확인된 버전으로만 갱신하고 임의 버전 허용으로 넓히지 않는다.
6. PC에 연결된 폰에 지상에서 설치하고 PID/process_session_id/실제 설치 버전을 검증한다. '앱 화면 재실행'과 프로세스 교체를 로그로 구분한다. SDK 등록/FC 직접 GET/ground 상태/영상 각 단계 검증 전 비행 성공 판정을 하지 않는다.
7. 되돌릴 기준 패키지는 `drone-control/trials/artifacts/packages/sample-telemetry-age.20260906.4.apk`, SHA256 `AAED5A27A0C6E1140C9B80E57ADE157A1F34E0B5FCA6592C7653AB3826785762`. 이전 source 및 runner gate도 같은 baseline 조합으로 되돌린다. Android가 버전 downgrade를 거부하면 자동 uninstall/data 삭제를 하지 않는다. 보관한 기준 소스로 더 높은 rollback version을 빌드하는 방법을 사용하고 실제 설치 version을 다시 확인한다.

## 9. 지상 acceptance — 코드 통과와 실제 해결을 분리

실제 영상이 보이는 FPV 화면을 먼저 특정하고 센서를 가리지 않은 상태에서 모터 OFF로 검증한다. 앱은 자동 RC 입력/시동/이륙을 하지 않는다.

- **냉시작 3회:** 앱 프로세스 강제 종료 후 시작. 아무 스틱도 조작하지 않는 30초 구간 동안 available/enable/raw/Surface/실제 폰 표시 시간을 기록한다. 첫 영상 10초 이내를 초기 목표로 두되, 초과 시 층별 latency와 실패로 기록한다. 10초 목표는 DJI 보장치가 아니다.
- **화면 lifecycle 10회:** FPV 화면 진입/이탈·화면 잠금/복귀 후 invalid put=0, old manager remove 오류=0, 실제 영상 복귀 확인. 한 번 표시됐다는 이유로 lifecycle 완료 처리하지 않는다.
- **PC 영상 접속 5회:** PC client 있음/없음 양쪽에서 폰 영상을 확인. PC 접속 없이 delivered=0은 실패가 아니다. 연결2가 연결1 I-frame을 소비하거나 old snapshot을 재사용하지 않아야 한다.
- **지상 대기 15분 3회:** 아무 스틱도 조작하지 않는 구간을 포함하고 FC와 영상 지표를 동시에 기록한다. FC 실패가 재발해도 raw 정상/phone black 여부를 별도 보고한다. video patch 통과를 FC handler 장애 해결로 주장하지 않는다.
- **검정 재현 시:** 최소 10초 무입력 대기와 동일 길이의 비교 구간을 기록한다. 사용자의 수동 스틱 관측이 있을 경우 Android 시각·motor=false·실제 raw/decode/Surface 변화와 연결해 보존한다. 시간이 지나 초기화된 것을 스틱 원인으로 단정하지 않는다. 자동 스틱 입력을 테스트 도구로 구현하지 않는다.
- **합격 조건:** A/S/P 회귀 통과와 실제 폰 표시 복귀를 모두 만족하며, 이전 세대 callback 반영·invalid Surface 등록·중복 listener 누적이 0이다. raw callback만 정상인 결과는 '폰 검정 해결' 합격이 아니다. 재현 실패만으로 원인 확정도 하지 않는다.

공식 문서 확인일 2026-09-07. `addReceiveStreamListener`, `addFrameListener`, `put/removeCameraStreamSurface`, `enableStream`과 `setKeepAliveDecoding`의 구분은 [DJI ICameraStreamManager](https://developer.dji.com/api-reference-v5/android-api/Components/IMediaDataCenter/ICameraStreamManager.html)를 사용했다. local source의 frame listener 예시는 CameraStreamDetailVM.kt:196 (local reference; not published: `CameraStreamDetailVM.kt`)이다. `setKeepAliveDecoding(true)`는 이 수정 명세에 포함하지 않는다.
