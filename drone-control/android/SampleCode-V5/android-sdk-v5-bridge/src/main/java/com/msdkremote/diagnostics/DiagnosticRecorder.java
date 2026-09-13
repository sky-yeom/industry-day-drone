package com.msdkremote.diagnostics;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Android-free, bounded JSON encoder, queue and durable rotating writer. */
final class DiagnosticRecorder {
    static final String DIRECTORY_NAME = "field-diagnostics";
    static final String LOCK_BASENAME = "writer.lock";
    static final Limits DEFAULT_LIMITS = new Limits(256, 4, 2 * 1024 * 1024, 32 * 1024);

    static final class Limits {
        final int queueCapacity;
        final int fileCount;
        final int fileBytes;
        final int recordBytes;

        Limits(int queueCapacity, int fileCount, int fileBytes, int recordBytes) {
            if (queueCapacity < 1 || fileCount < 1 || recordBytes < 2048
                    || fileBytes < recordBytes) {
                throw new IllegalArgumentException("Invalid diagnostic limits");
            }
            this.queueCapacity = queueCapacity;
            this.fileCount = fileCount;
            this.fileBytes = fileBytes;
            this.recordBytes = recordBytes;
        }
    }

    interface Sink extends AutoCloseable {
        void write(byte[] record) throws IOException;
        @Override void close() throws IOException;
    }

    interface SinkFactory {
        Sink open() throws IOException;
    }

    interface FailureListener {
        void onFailure(String safeError);
    }

    private final Object enqueueLock = new Object();
    private final Limits limits;
    private final String processUuid;
    private final int pid;
    private final SinkFactory sinkFactory;
    private final FailureListener failureListener;
    private final ArrayBlockingQueue<byte[]> queue;
    private final Thread worker;
    private final CountDownLatch stopped = new CountDownLatch(1);
    private final AtomicBoolean fatalErrorReported = new AtomicBoolean();
    private final AtomicBoolean encodingErrorReported = new AtomicBoolean();
    private final AtomicLong queueDropped = new AtomicLong();
    private final AtomicLong unavailableDropped = new AtomicLong();
    private final AtomicLong writeDropped = new AtomicLong();
    private final AtomicLong writeFailures = new AtomicLong();
    private final AtomicLong encodingFailures = new AtomicLong();
    private final AtomicLong notificationFailures = new AtomicLong();
    private final AtomicLong truncatedRecords = new AtomicLong();
    private final AtomicLong recordsWritten = new AtomicLong();
    private final AtomicLong bytesWritten = new AtomicLong();
    private volatile boolean accepting = true;
    private volatile boolean ready;
    private volatile boolean started;
    private volatile String lastSafeError;
    private volatile long sequence;

    DiagnosticRecorder(SinkFactory sinkFactory, Limits limits, String processUuid, int pid,
                       FailureListener failureListener) {
        this.sinkFactory = sinkFactory;
        this.limits = limits;
        this.processUuid = shorten(processUuid, 36);
        this.pid = pid;
        this.failureListener = failureListener;
        queue = new ArrayBlockingQueue<>(limits.queueCapacity);
        worker = new Thread(this::runWriter, "field-diagnostics-writer");
        worker.setDaemon(true);
    }

    void start() {
        synchronized (enqueueLock) {
            if (started || !accepting) {
                return;
            }
            started = true;
            try {
                worker.start();
            } catch (RuntimeException error) {
                fail("thread_start", error);
                discardPending(false);
                stopped.countDown();
            }
        }
    }

    boolean record(String name, Map<String, Object> fields, long elapsedRealtime,
                   long currentTimeMillis, String threadName, long threadId) {
        // Serialization snapshots caller-owned data; no mutable SDK objects reach the writer.
        // This lock orders sequence numbers and offers, and is never held during disk I/O.
        synchronized (enqueueLock) {
            if (!accepting) {
                unavailableDropped.incrementAndGet();
                return false;
            }
            long nextSequence = ++sequence;
            if (queue.remainingCapacity() == 0) {
                queueDropped.incrementAndGet();
                return false;
            }
            Map<String, Object> envelope = new LinkedHashMap<>();
            envelope.put("seq", nextSequence);
            envelope.put("processUuid", processUuid);
            envelope.put("pid", pid);
            envelope.put("elapsedRealtime", elapsedRealtime);
            envelope.put("currentTimeMillis", currentTimeMillis);
            envelope.put("thread", shorten(threadName, 48));
            envelope.put("threadId", threadId);
            envelope.put("name", shorten(name, 96));
            envelope.put("queueDropped", queueDropped.get());
            envelope.put("fields", fields);
            String json;
            try {
                json = Json.encode(envelope, limits.recordBytes - 1);
            } catch (EncodingLimit limit) {
                json = omittedFields(envelope, limit.reason);
            } catch (RuntimeException error) {
                encodingFailures.incrementAndGet();
                lastSafeError = safeError("encode", error);
                notifyOnce(encodingErrorReported, lastSafeError);
                json = omittedFields(envelope, "fields_unavailable");
            }
            byte[] record = (json + "\n").getBytes(StandardCharsets.UTF_8);
            if (!queue.offer(record)) {
                queueDropped.incrementAndGet();
                return false;
            }
            return true;
        }
    }

    private String omittedFields(Map<String, Object> envelope, String reason) {
        truncatedRecords.incrementAndGet();
        envelope.put("fields", null);
        envelope.put("fieldsOmitted", reason);
        envelope.put("truncated", true);
        return Json.encode(envelope, limits.recordBytes - 1);
    }

    Map<String, Object> status() {
        Map<String, Object> result = configuration(limits);
        result.put("enabled", ready && accepting);
        result.put("state", writeFailures.get() > 0 ? "failed"
                : !accepting ? "stopped" : ready ? "running" : started ? "starting" : "not_started");
        result.put("writerAlive", worker.isAlive());
        result.put("processUuid", processUuid);
        result.put("pid", pid);
        result.put("lastSequence", sequence);
        result.put("queueDepth", queue.size());
        result.put("queueDropped", queueDropped.get());
        result.put("unavailableDropped", unavailableDropped.get());
        result.put("writeDropped", writeDropped.get());
        result.put("writeFailures", writeFailures.get());
        result.put("encodingFailures", encodingFailures.get());
        result.put("errorNotificationFailures", notificationFailures.get());
        result.put("truncatedRecords", truncatedRecords.get());
        result.put("recordsWritten", recordsWritten.get());
        result.put("bytesWrittenThisProcess", bytesWritten.get());
        result.put("lastSafeError", lastSafeError);
        return result;
    }

    static Map<String, Object> configuration(Limits limits) {
        Map<String, Object> result = new LinkedHashMap<>();
        List<String> basenames = new ArrayList<>();
        for (int slot = 0; slot < limits.fileCount; slot++) {
            basenames.add(basename(slot));
        }
        result.put("directoryBasename", DIRECTORY_NAME);
        result.put("fileBasenames", basenames);
        result.put("lockFileBasename", LOCK_BASENAME);
        result.put("maxFiles", limits.fileCount);
        result.put("maxFileBytes", limits.fileBytes);
        result.put("maxTotalBytes", (long) limits.fileCount * limits.fileBytes);
        result.put("maxRecordBytes", limits.recordBytes);
        result.put("queueCapacity", limits.queueCapacity);
        return result;
    }

    // Only tests stop/drain the writer. Flight/lifecycle callbacks never wait for disk.
    void stopAccepting() {
        synchronized (enqueueLock) {
            accepting = false;
            if (!started) {
                discardPending(false);
                stopped.countDown();
            }
        }
    }

    boolean awaitStopped(long timeout, TimeUnit unit) throws InterruptedException {
        return stopped.await(timeout, unit);
    }

    private void runWriter() {
        Sink sink = null;
        String phase = "open";
        boolean inFlight = false;
        try {
            sink = sinkFactory.open();
            ready = true;
            while (accepting || !queue.isEmpty()) {
                byte[] record = queue.poll(100, TimeUnit.MILLISECONDS);
                if (record == null) {
                    continue;
                }
                inFlight = true;
                phase = "write";
                sink.write(record);
                recordsWritten.incrementAndGet();
                bytesWritten.addAndGet(record.length);
                inFlight = false;
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            fail("interrupted", error);
        } catch (IOException | RuntimeException error) {
            fail(phase, error);
        } finally {
            ready = false;
            discardPending(inFlight);
            if (sink != null) {
                try {
                    sink.close();
                } catch (IOException | RuntimeException error) {
                    fail("close", error);
                }
            }
            stopped.countDown();
        }
    }

    private void discardPending(boolean inFlight) {
        synchronized (enqueueLock) {
            accepting = false;
            writeDropped.addAndGet(queue.size() + (inFlight ? 1L : 0L));
            queue.clear();
        }
    }

    private void fail(String phase, Exception error) {
        accepting = false;
        ready = false;
        writeFailures.incrementAndGet();
        lastSafeError = safeError(phase, error);
        notifyOnce(fatalErrorReported, lastSafeError);
    }

    private void notifyOnce(AtomicBoolean reported, String safeError) {
        if (failureListener != null && reported.compareAndSet(false, true)) {
            try {
                failureListener.onFailure(safeError);
            } catch (RuntimeException error) {
                notificationFailures.incrementAndGet();
            }
        }
    }

    static String safeError(String phase, Throwable error) {
        return phase + ":" + error.getClass().getName();
    }

    static String basename(int slot) {
        return "field-" + slot + ".jsonl";
    }

    private static String shorten(String text, int length) {
        if (text == null) {
            return "unknown";
        }
        return text.length() <= length ? text : text.substring(0, length);
    }

    static final class RotatingFiles implements Sink {
        private final File directory;
        private final Limits limits;
        private RandomAccessFile lockFile;
        private FileLock directoryLock;
        private FileOutputStream output;
        private long currentSize;

        RotatingFiles(File directory, Limits limits) throws IOException {
            this.directory = directory;
            this.limits = limits;
            if (!directory.isDirectory() && !directory.mkdirs() && !directory.isDirectory()) {
                throw new IOException("Cannot create diagnostic directory");
            }
            try {
                // A separate zero-byte lock prevents two Android processes corrupting the slots.
                lockFile = new RandomAccessFile(new File(directory, LOCK_BASENAME), "rw");
                directoryLock = lockFile.getChannel().tryLock();
                if (directoryLock == null) {
                    throw new IOException("Diagnostic directory already in use");
                }
                lockFile.setLength(0);
                for (int slot = 0; slot < limits.fileCount; slot++) {
                    File file = file(slot);
                    if (file.exists()) {
                        if (!file.isFile()) {
                            throw new IOException("Diagnostic slot is not a file");
                        }
                        if (slot == 0 || file.length() > limits.fileBytes) {
                            repairTail(file);
                        }
                    }
                }
                openActive();
            } catch (IOException | RuntimeException error) {
                try {
                    close();
                } catch (IOException cleanupError) {
                    error.addSuppressed(cleanupError);
                }
                throw error;
            }
        }

        @Override
        public void write(byte[] record) throws IOException {
            if (record.length > limits.recordBytes || record.length == 0
                    || record[record.length - 1] != '\n') {
                throw new IOException("Invalid diagnostic record size or terminator");
            }
            if (currentSize + record.length > limits.fileBytes) {
                rotate();
            }
            output.write(record);
            // FileOutputStream is unbuffered; sync each record, exclusively on the worker.
            output.getFD().sync();
            currentSize += record.length;
        }

        private File file(int slot) {
            return new File(directory, basename(slot));
        }

        private void openActive() throws IOException {
            File active = file(0);
            currentSize = active.length();
            output = new FileOutputStream(active, true);
        }

        private void rotate() throws IOException {
            output.close();
            output = null;
            File oldest = file(limits.fileCount - 1);
            if (oldest.exists() && !oldest.delete()) {
                throw new IOException("Cannot remove oldest diagnostic slot");
            }
            for (int slot = limits.fileCount - 2; slot >= 0; slot--) {
                File source = file(slot);
                if (source.exists() && !source.renameTo(file(slot + 1))) {
                    throw new IOException("Cannot rotate diagnostic slot");
                }
            }
            openActive();
        }

        private void repairTail(File file) throws IOException {
            try (RandomAccessFile data = new RandomAccessFile(file, "rw")) {
                long originalLength = data.length();
                long end = Math.min(originalLength, limits.fileBytes);
                byte[] block = new byte[4096];
                long completeLength = 0;
                while (end > 0) {
                    long start = Math.max(0, end - block.length);
                    int length = (int) (end - start);
                    data.seek(start);
                    data.readFully(block, 0, length);
                    for (int index = length - 1; index >= 0; index--) {
                        if (block[index] == '\n') {
                            completeLength = start + index + 1;
                            break;
                        }
                    }
                    if (completeLength > 0) {
                        break;
                    }
                    end = start;
                }
                if (completeLength != originalLength) {
                    data.setLength(completeLength);
                    data.getFD().sync();
                }
            }
        }

        @Override
        public void close() throws IOException {
            IOException failure = null;
            if (output != null) {
                try {
                    output.close();
                } catch (IOException error) {
                    failure = error;
                }
                output = null;
            }
            if (directoryLock != null) {
                try {
                    directoryLock.release();
                } catch (IOException error) {
                    if (failure == null) failure = error;
                    else failure.addSuppressed(error);
                }
                directoryLock = null;
            }
            if (lockFile != null) {
                try {
                    lockFile.close();
                } catch (IOException error) {
                    if (failure == null) failure = error;
                    else failure.addSuppressed(error);
                }
                lockFile = null;
            }
            if (failure != null) {
                throw failure;
            }
        }
    }

    private static final class EncodingLimit extends RuntimeException {
        final String reason;

        EncodingLimit(String reason) {
            super(null, null, false, false);
            this.reason = reason;
        }
    }

    private static final class Json {
        private final StringBuilder out = new StringBuilder();
        private final int maxBytes;
        private int nodes;

        private Json(int maxBytes) {
            this.maxBytes = maxBytes;
        }

        static String encode(Object value, int maxBytes) {
            Json json = new Json(maxBytes);
            json.value(value, 0);
            return json.out.toString();
        }

        private void append(char character) {
            if (out.length() >= maxBytes) {
                throw new EncodingLimit("record_size_limit");
            }
            out.append(character);
        }

        private void append(String text) {
            if (text.length() > maxBytes - out.length()) {
                throw new EncodingLimit("record_size_limit");
            }
            out.append(text);
        }

        private void string(String text) {
            append('"');
            for (int index = 0; index < text.length(); index++) {
                char character = text.charAt(index);
                if (character == '"' || character == '\\') {
                    append('\\');
                    append(character);
                } else if (character < 0x20 || character > 0x7e) {
                    // ASCII-only JSON makes the byte cap exact, including surrogate pairs.
                    append("\\u");
                    for (int shift = 12; shift >= 0; shift -= 4) {
                        append("0123456789abcdef".charAt((character >> shift) & 15));
                    }
                } else {
                    append(character);
                }
            }
            append('"');
        }

        private void value(Object value, int depth) {
            if (++nodes > 1024 || depth > 8) {
                throw new EncodingLimit("structure_limit");
            }
            if (value == null) {
                append("null");
            } else if (value instanceof String) {
                string((String) value);
            } else if (value instanceof Boolean || value instanceof Byte
                    || value instanceof Short || value instanceof Integer || value instanceof Long) {
                append(value.toString());
            } else if (value instanceof Float || value instanceof Double) {
                double number = ((Number) value).doubleValue();
                append(Double.isNaN(number) || Double.isInfinite(number) ? "null" : value.toString());
            } else if (value instanceof Map) {
                append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                    if (!(entry.getKey() instanceof String)) {
                        throw new EncodingLimit("unsupported_key");
                    }
                    String key = (String) entry.getKey();
                    if (key.length() > 256) {
                        throw new EncodingLimit("structure_limit");
                    }
                    if (!first) append(',');
                    first = false;
                    string(key);
                    append(':');
                    value(sensitiveKey(key) ? "[redacted]" : entry.getValue(), depth + 1);
                }
                append('}');
            } else if (value instanceof List) {
                append('[');
                boolean first = true;
                for (Object element : (List<?>) value) {
                    if (!first) append(',');
                    first = false;
                    value(element, depth + 1);
                }
                append(']');
            } else {
                // Never call SDK objects', byte arrays' or exceptions' toString().
                string("[unsupported]");
            }
        }

        private static boolean sensitiveKey(String key) {
            String normalized = key.toLowerCase(Locale.ROOT).replace("_", "").replace("-", "");
            return normalized.contains("token") || normalized.contains("secret")
                    || normalized.contains("password") || normalized.contains("credential")
                    || normalized.contains("authorization") || normalized.contains("serial")
                    || normalized.contains("latitude") || normalized.contains("longitude")
                    || normalized.contains("gps") || normalized.contains("coordinate")
                    || normalized.contains("image") || normalized.contains("screenshot")
                    || normalized.contains("base64") || normalized.equals("location")
                    || normalized.equals("lat") || normalized.equals("lon")
                    || normalized.equals("lng") || normalized.equals("payload")
                    || normalized.equals("accessory") || normalized.equals("intent")
                    || normalized.equals("extras");
        }
    }
}
