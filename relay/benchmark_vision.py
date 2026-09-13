"""Preview or measure real AzureVision requests for the existing monitor PNGs."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import struct
import time

from .camera import FixtureCamera, SCENARIO
from .vision import AzureVision, SYSTEM_PROMPT, VisionError


class TimedAzureVision(AzureVision):
    def __init__(self):
        super().__init__()
        self.http_seconds = None
        self.http_requests = 0

    async def _request(self, payload, headers):
        self.http_requests += 1
        started = time.perf_counter()
        try:
            return await super()._request(payload, headers)
        finally:
            self.http_seconds = time.perf_counter() - started


async def benchmark(*, execute, repeats, search_prompt):
    camera = FixtureCamera()
    frames = [await camera.capture(f"monitor-{index}") for index in (1, 2, 3)]
    people = {person["monitorId"]: person for person in SCENARIO["people"]}
    contexts = {
        monitor: {"monitor_id": monitor, "label": person["label"], "report": person["clue"]}
        for monitor, person in people.items()
    }
    provider = TimedAzureVision()
    error = provider.readiness()
    if error:
        raise VisionError(error)
    preview = {
        "operation": "real AzureVision analysis; no flight commands",
        "endpoint": provider.endpoint.rstrip("/") + "/openai/v1/chat/completions",
        "deployment": provider.deployment,
        "reasoning_effort": provider.reasoning_effort,
        "max_completion_tokens": provider.max_completion_tokens,
        "requests": repeats * len(frames),
        "system_prompt": SYSTEM_PROMPT,
        "search_prompt": search_prompt,
        "scene_contexts": contexts,
        "images": [{
            "path": f"public/monitors/{frame.monitor_id}.png",
            "bytes": len(frame.image_bytes),
            "size": list(struct.unpack_from(">II", frame.image_bytes, 16)),
            "sha256": hashlib.sha256(frame.image_bytes).hexdigest(),
        } for frame in frames],
    }
    print(json.dumps({"preview": preview}, ensure_ascii=False), flush=True)
    if not execute:
        return 0
    results = []
    for repeat in range(1, repeats + 1):
        for frame in frames:
            provider = TimedAzureVision()
            started = time.perf_counter()
            record = {"repeat": repeat, "image": frame.monitor_id}
            try:
                record["evidence"] = await provider.analyze(
                    frame, search_prompt=search_prompt, scene_context=contexts[frame.monitor_id])
                record["ok"] = True
            except VisionError as exc:
                record.update(ok=False, error=str(exc))
            record.update(total_seconds=round(time.perf_counter() - started, 3),
                          http_seconds=round(provider.http_seconds, 3)
                          if provider.http_seconds is not None else None,
                          http_requests=provider.http_requests)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    successful = [row["total_seconds"] for row in results if row["ok"]]
    summary = {
        "attempts": len(results),
        "successes": len(successful),
        "failures": len(results) - len(successful),
        "timing_scope": "AzureVision.analyze: authentication + upload + model response + validation; excludes image read",
        "http_timing_scope": "HTTP upload + response + validation, not server-only inference time",
        "mean_seconds": round(statistics.mean(successful), 3) if successful else None,
        "min_seconds": min(successful) if successful else None,
        "max_seconds": max(successful) if successful else None,
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
    return 0 if len(successful) == len(results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Send the previewed images to the configured Azure deployment")
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--search-prompt", required=True, help="The participant's confirmed search criteria")
    args = parser.parse_args()
    return asyncio.run(benchmark(execute=args.execute, repeats=args.repeats, search_prompt=args.search_prompt))


if __name__ == "__main__":
    raise SystemExit(main())
