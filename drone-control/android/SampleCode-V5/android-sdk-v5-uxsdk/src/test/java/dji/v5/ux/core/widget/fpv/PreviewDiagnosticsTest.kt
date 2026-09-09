package dji.v5.ux.core.widget.fpv

import org.junit.Assert.*
import org.junit.Test

class PreviewDiagnosticsTest {
    private var now = 0L
    private val diagnostics = PreviewDiagnostics { now }
    @Test fun defaultOffDoesNotClaimDecodedFramesOrRenderedPixels() {
        val snapshot = diagnostics.snapshot()
        assertEquals(false, snapshot["probe_enabled"]); assertEquals(0L, snapshot["decoded_frames"])
        assertEquals(false, snapshot["rendered_pixels_verified"])
    }
    @Test fun decodedCountersExpireWithinThirtySecondsAndIgnoreOldGeneration() {
        val first = diagnostics.startProbe("LEFT_OR_MAIN", 60000)
        diagnostics.frame(first, 1280, 720, "YUV420_888")
        assertEquals(1L, diagnostics.snapshot()["decoded_frames"])
        assertEquals(0L, diagnostics.snapshot()["first_decoded_frame_at_ms"])
        now = 30000; diagnostics.frame(first, 1280, 720, "YUV420_888")
        assertEquals(1L, diagnostics.snapshot()["decoded_frames"])
        diagnostics.stopProbe(); val second = diagnostics.startProbe("FPV", 1000)
        diagnostics.frame(first, 1280, 720, "YUV420_888")
        assertEquals(0L, diagnostics.snapshot()["decoded_frames"])
        diagnostics.frame(second, 640, 360, "YUV420_888")
        assertEquals(1L, diagnostics.snapshot()["decoded_frames"])
        diagnostics.stopProbe(); diagnostics.frame(second, 640, 360, "YUV420_888")
        assertEquals(3L, diagnostics.snapshot()["decoded_old_callback_drops"])
    }
}
