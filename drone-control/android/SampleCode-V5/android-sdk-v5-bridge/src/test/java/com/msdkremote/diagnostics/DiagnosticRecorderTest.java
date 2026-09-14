package com.msdkremote.diagnostics;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Collections;
import java.util.concurrent.TimeUnit;
import static org.junit.Assert.*;

public class DiagnosticRecorderTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();

    private DiagnosticRecorder recorder(File folder, DiagnosticRecorder.Limits limits) {
        return new DiagnosticRecorder(() -> new DiagnosticRecorder.RotatingFiles(folder, limits),
                limits, "process-test", 123, error -> {});
    }

    @Test public void rotationIsBoundedAndRestartPreservesCompleteRecords() throws Exception {
        File directory = temporary.newFolder();
        DiagnosticRecorder.Limits limits = new DiagnosticRecorder.Limits(256, 4, 4096, 2048);
        DiagnosticRecorder first = recorder(directory, limits);
        for (int n = 0; n < 100; n++)
            assertTrue(first.record("first", Collections.singletonMap("n", n), n, n, "test", 1));
        first.start();
        first.stopAccepting();
        assertTrue(first.awaitStopped(10, TimeUnit.SECONDS));
        assertEquals(100L, first.status().get("recordsWritten"));
        long bytes = 0;
        for (int n = 0; n < 4; n++) {
            File file = new File(directory, DiagnosticRecorder.basename(n));
            assertTrue(file.length() <= 4096);
            bytes += file.length();
        }
        assertTrue(bytes <= 16384);
        File active = new File(directory, "field-0.jsonl");
        String before = readUtf8(active);
        DiagnosticRecorder second = recorder(directory, limits);
        second.record("second", Collections.emptyMap(), 1, 1, "test", 1);
        second.start();
        second.stopAccepting();
        assertTrue(second.awaitStopped(10, TimeUnit.SECONDS));
        String retained = readUtf8(active);
        assertTrue(retained.contains("\"name\":\"second\""));
        assertTrue(retained.startsWith(before)
                || readUtf8(new File(directory, "field-1.jsonl")).equals(before));
    }

    // android.jar has no Files.readString; readAllBytes keeps the same bytes.
    private static String readUtf8(File file) throws IOException {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    // android.jar has no String.repeat either.
    private static String repeated(char value, int count) {
        char[] buffer = new char[count];
        java.util.Arrays.fill(buffer, value);
        return new String(buffer);
    }

    @Test public void queueOverflowAndDiskFailureAreVisible() throws Exception {
        DiagnosticRecorder.Limits limits = new DiagnosticRecorder.Limits(1, 2, 4096, 2048);
        DiagnosticRecorder failed = new DiagnosticRecorder(
                () -> { throw new IOException("must-not-appear-in-status"); },
                limits, "process-test", 1, error -> {});
        assertTrue(failed.record("one", Collections.emptyMap(), 0, 0, "test", 1));
        assertFalse(failed.record("two", Collections.emptyMap(), 0, 0, "test", 1));
        assertEquals(1L, failed.status().get("queueDropped"));
        failed.start();
        assertTrue(failed.awaitStopped(10, TimeUnit.SECONDS));
        assertEquals("failed", failed.status().get("state"));
        assertEquals(1L, failed.status().get("writeFailures"));
        assertFalse(failed.status().toString().contains("must-not-appear"));
    }

    @Test public void oversizedFieldsProduceBoundedValidRecord() throws Exception {
        File directory = temporary.newFolder();
        DiagnosticRecorder recorder = recorder(directory, new DiagnosticRecorder.Limits(4, 2, 4096, 2048));
        recorder.record("large", Collections.singletonMap("data", repeated('x', 5000)), 0, 0, "test", 1);
        recorder.start();
        recorder.stopAccepting();
        assertTrue(recorder.awaitStopped(10, TimeUnit.SECONDS));
        byte[] bytes = Files.readAllBytes(new File(directory, "field-0.jsonl").toPath());
        assertTrue(bytes.length <= 2048);
        assertTrue(new String(bytes, StandardCharsets.UTF_8).contains("\"truncated\":true"));
        assertEquals('\n', bytes[bytes.length - 1]);
    }
}
