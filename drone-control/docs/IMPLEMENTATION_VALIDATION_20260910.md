# Implementation validation — 2026-09-10

The old flight snapshot was first pushed as `e48d13f` on `feat/drone` under
WhoAmI125. Latest dashboard `f416ae5` was merged as `cea2c79`; the following checks
cover the subsequent connectivity and tool integration implementation.

## Checks performed

| Check | Result |
|---|---|
| PC complete unit suite | **141 passed** |
| Relay complete suite, including real loopback HTTP service with fake aircraft | **105 passed** |
| COEX controller/transport/runner regressions | **58 passed** |
| Historical tool-contract starter | **11 passed** |
| Android bridge Gradle unit tests | **63 passed**, zero failures/errors/skips |
| Android UX Gradle unit tests | **10 passed**, zero failures/errors/skips |
| `:sample:assembleDebug` | Passed against SDK 5.18.0, SDK35, JDK17 |
| Next production build, including TypeScript | Passed with Next 16.4.0-canary.3 |
| ESLint | Passed; local Python virtual environments excluded |
| APK signature | Verified APK v2 signature, one signer |
| Dependency audit | 0 reported vulnerabilities after compatible js-yaml/sharp patches |

PC/relay validation used Python 3.12.10; frontend used Node 24.19.0. The freshly
installed PC vision environment contains PyAV 18.1.0, NumPy 2.5.3, OpenCV
5.0.0.93 and pupil-apriltags 1.0.4.post11. Previous tested private environments
and original Android source were preserved. The existing npm lock contains public
Microsoft mirror URLs; npm 12 installation used per-command `--allow-remote=all`
with lockfile integrity checks. No global npm configuration was changed.

The dependency audit exposed two existing transitive vulnerabilities. Only
compatible versions were updated: js-yaml 4.3.1→4.3.2, sharp 0.35.3→0.35.4 and its
matching platform/libvips packages. React, Next and the scenario are unchanged.

## Important regression coverage

- Lost/duplicate/late SDK callbacks, retained physical read budgets and connection
  epoch isolation; registration/cleanup reentrancy for perception and video.
- Wrong/missing ACKs invalidate current telemetry, while last-known evidence is
  explicitly separate. Raw OA sentinel values and callback age are not free space.
- Same request cannot start two missions; changed arguments conflict. Service
  restart cannot replay unfinished work. Shutdown cannot admit a new mission.
- Caller lease expiration and cancellation remain latched, including late reads
  and cancellation while logging before a network write. Unsent messages retain
  contiguous cleanup sequences. A timed-out write is never replayed.
- Ground/motors-off evidence is repeated just before takeoff and checked again
  at the write boundary; changed or expired evidence blocks the write.
- Motion requires actual finite velocity/yaw and fresh FC values. Ascent completion
  requires a fresh vertical-speed hold, rather than a momentary displayed height.
- Camera detection/encoding delays cannot turn old frames or velocity into fresh
  capture evidence. Captures require the correct tag, actual arrival and distinct
  bounded PNG frames. Reflected secrets are removed from logs and API snapshots.
- The relay waits for actual visit/capture evidence, preserves caller/request
  identity after uncertain replies, continues its lease during vision analysis,
  stops on abort/closure/deadline/errors, and separates RC landing from scoring.

## Hardware and external-service status

This implementation turn did **not** run a drone flight or issue a paid Azure
Voice Live/vision call. The HTTP integration tests connected only to loopback
servers and fake aircraft/vision adapters. The last ADB inventory had no connected
phone, so the newly built APK has not yet been installed or ground-tested.

The output APK is local under
`android/artifacts/com.ms.voice-5.18-connectivity.20260910.5.apk` (Git-ignored).
Application ID is `com.ms.voice`, versionCode `20260910`, versionName
`5.18-connectivity.20260910.5`. APK SHA-256 is recorded in the adjacent local
checksum file; the signed binary and private properties are not pushed.

Actual SDK recovery, camera startup without stick movement and physical mission
completion remain hardware verification items. Automatic SDK recovery stays OFF;
the new code does not claim to repair DJI firmware internals or disable its safety
behavior. The new live profile and .5 COEX compatibility have only offline tests
in this turn. Preserve supervised RC takeover and confirm the measured tag layout
before selecting live mode.
