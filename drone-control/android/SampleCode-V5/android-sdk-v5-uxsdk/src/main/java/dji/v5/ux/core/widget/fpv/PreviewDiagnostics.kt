package dji.v5.ux.core.widget.fpv

/** Counter-only decoded-frame probe: does not retain/copy pixels or prove Surface rendering. */
internal class PreviewDiagnostics(private val clock: () -> Long) {
    private var probeEnabled = false
    private var probeGeneration = 0L
    private var probeStartedAt = -1L
    private var probeUntil = -1L
    private var frames = 0L
    private var firstFrameAt = -1L
    private var lastFrameAt = -1L
    private var oldCallbacks = 0L
    private var width = 0
    private var height = 0
    private var format: String? = null
    private var camera: String? = null

    @Synchronized fun startProbe(camera: String, durationMs: Long): Long {
        probeGeneration++; probeEnabled = true; probeStartedAt = clock()
        probeUntil = probeStartedAt + durationMs.coerceIn(1, 30000)
        frames = 0; firstFrameAt = -1; lastFrameAt = -1; width = 0; height = 0; format = null
        this.camera = camera
        return probeGeneration
    }
    @Synchronized fun stopProbe() { probeGeneration++; probeEnabled = false }
    @Synchronized fun frame(ticket: Long, width: Int, height: Int, format: String) {
        val now = clock()
        if (!probeEnabled || ticket != probeGeneration || now >= probeUntil) { oldCallbacks++; return }
        frames++; if (firstFrameAt < 0) firstFrameAt = now
        lastFrameAt = now; this.width = width; this.height = height; this.format = format
    }
    @Synchronized fun snapshot(): Map<String, Any?> {
        val now = clock()
        return linkedMapOf("probe_enabled" to probeEnabled, "probe_generation" to probeGeneration,
            "probe_camera" to camera, "probe_started_at_ms" to probeStartedAt, "probe_until_ms" to probeUntil,
            "decoded_frames" to frames, "first_decoded_frame_at_ms" to firstFrameAt,
            "decoded_frame_age_ms" to if (lastFrameAt < 0) -1 else now - lastFrameAt,
            "decoded_width" to width, "decoded_height" to height, "decoded_format" to format,
            "decoded_old_callback_drops" to oldCallbacks, "android_elapsed_ms" to now,
            "rendered_pixels_verified" to false,
            "probe_observer_effect" to "frame_listener_may_start_decoding_compare_probe_off_and_on")
    }
}
