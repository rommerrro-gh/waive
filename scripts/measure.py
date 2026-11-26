"""Замер пропускной способности и задержек поиска на своём стенде."""
import argparse
import json
import math
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from itertools import cycle
from urllib.parse import urlencode
from urllib.request import urlopen

from evaluate import CASES


def request(base_url, query, semantic):
    args = urlencode({"q": query, "semantic": str(semantic).lower()})
    started = time.monotonic()
    try:
        with urlopen(f"{base_url.rstrip('/')}/search?{args}", timeout=8) as response:
            mode = json.load(response)["search_mode"]
    except (OSError, ValueError, KeyError) as error:
        mode = f"error:{getattr(error, 'code', None) or type(error).__name__}"
    return mode, (time.monotonic() - started) * 1000


def percentile(samples, fraction):
    return round(samples[math.ceil(len(samples) * fraction) - 1], 1) if samples else None


def benchmark(options):
    semantic = options.mode == "hybrid"
    queries = cycle(case[1] for case in CASES)
    for _ in range(options.warmup):
        request(options.url, next(queries), semantic)
    counts = Counter()
    latencies = []
    pending = deque()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        for sequence in range(options.requests):
            delay = started + sequence / options.rps - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if len(pending) >= options.workers:
                if not pending[0].done():
                    counts["skipped:busy"] += 1
                    continue
                mode, elapsed = pending.popleft().result()
                counts[mode] += 1
                if not mode.startswith("error:"):
                    latencies.append(elapsed)
            pending.append(pool.submit(request, options.url, next(queries), semantic))
        for future in pending:
            mode, elapsed = future.result()
            counts[mode] += 1
            if not mode.startswith("error:"):
                latencies.append(elapsed)
    duration = time.monotonic() - started
    latencies.sort()
    return {
        "requested_mode": options.mode,
        "target_rps": options.rps,
        "successful_rps": round(len(latencies) / duration, 2),
        "requests_scheduled": options.requests,
        "response_modes": dict(counts),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=("lexical", "hybrid"), default="hybrid")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--rps", type=float, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=15)
    options = parser.parse_args()
    if min(options.requests, options.workers) < 1 or not 0 < options.rps <= 100 or options.warmup < 0:
        parser.error("Нужны положительные число запросов и потоков, неотрицательный прогрев и RPS до 100")
    print(json.dumps(benchmark(options), ensure_ascii=False))
