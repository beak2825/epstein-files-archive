#!/usr/bin/env python3
"""Identify native counterparts of DOJ PDF placeholders using HEAD only."""

import argparse
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx


MEDIA_EXTENSIONS = """mp4 mov mp3 avi m4a m4v wav wmv opus amr vob ts 3gp
mpg mpeg webm mkv aac flac aiff wma ogg mts m2ts flv asf aif 3g2 alac
ac3 dts dv mxf ra rm rmvb xvid mid midi avchd swf vid""".split()
TERMINAL = {"found", "missing"}
FIELDS = ["pdf_url", "status", "extension", "native_url", "size_bytes",
          "size_source", "content_type", "last_modified", "etag", "checked_at_utc"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_pdf(value):
    parts = urlsplit(value.strip())
    path = unquote(parts.path)
    if (parts.scheme != "https" or parts.netloc.lower() not in
            {"www.justice.gov", "justice.gov"} or parts.query or parts.fragment or
            not re.fullmatch(r"/epstein/files/DataSet \d+/EFTA\d+\.pdf", path, re.I)):
        raise ValueError(f"Expected a DOJ dataset PDF URL: {value!r}")
    return urlunsplit(("https", "www.justice.gov", quote(path, safe="/"), "", ""))


def load_urls(path):
    """Accept a JSON URL array, DOJ search response, CSV, or one URL per line."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [hit["_source"]["ORIGIN_FILE_URI"] for hit in data["hits"]["hits"]]
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError("JSON input must be a URL array or DOJ search response")
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as stream:
            data = [row["pdf_url"] for row in csv.DictReader(stream)]
    else:
        data = path.read_text(encoding="utf-8").splitlines()
    return list(dict.fromkeys(normalize_pdf(value) for value in data if value.strip()))


def extensions_from(values):
    extensions = list(dict.fromkeys(x.strip().lstrip(".") for x in values if x.strip()))
    if not extensions or any(not re.fullmatch(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*", x)
                             or x.lower() == "pdf" for x in extensions):
        raise ValueError("Supply non-PDF extensions, such as mp4, MOV, or tar.gz")
    return extensions


def open_state(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS probes (
            pdf_url TEXT NOT NULL, extension TEXT NOT NULL,
            status TEXT NOT NULL, status_code INTEGER, size_bytes INTEGER,
            content_type TEXT, last_modified TEXT, etag TEXT,
            checked_at_utc TEXT NOT NULL, error TEXT,
            PRIMARY KEY (pdf_url, extension)
        );
        CREATE TABLE IF NOT EXISTS known (
            pdf_url TEXT PRIMARY KEY, record TEXT NOT NULL
        );
    """)
    return conn


def import_known(conn, path):
    """Reuse identified records, preserving provenance; never import missing guesses."""
    records = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if not (row.get("status", "").startswith("available") or
                    row.get("status") in {"found", "known"}):
                continue
            pdf = normalize_pdf(row["pdf_url"])
            extension = extensions_from([row["extension"]])[0]
            native = pdf[:-3] + extension
            if row.get("native_url") and row["native_url"] != native:
                raise ValueError(f"Native URL does not match PDF and extension: {pdf}")
            size = row.get("size_bytes")
            size = int(size) if size not in (None, "") else None
            if size is not None and size < 0:
                raise ValueError(f"Negative size for {pdf}")
            record = {key: row.get(key, "") for key in FIELDS}
            record.update(pdf_url=pdf, native_url=native, extension=extension,
                          status="known", size_bytes=size,
                          size_source=row.get("size_source") or "imported_metadata")
            records.append((pdf, json.dumps(record)))
    with conn:
        conn.executemany("INSERT OR IGNORE INTO known VALUES (?, ?)", records)
    return len(records)


class RateLimiter:
    """One request budget shared by all workers, including retries."""

    def __init__(self, rate):
        self.interval = 1 / rate
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.queue = deque()

    def acquire(self):
        gate = threading.Event()
        with self.lock:
            self.queue.append(gate)
            if len(self.queue) == 1:
                gate.set()
        gate.wait()
        # Only the queue head waits on a timer; other workers stay asleep.
        while True:
            with self.lock:
                delay = self.next_request - time.monotonic()
                if delay <= 0:
                    self.next_request = time.monotonic() + self.interval
                    self.queue.popleft()
                    if self.queue:
                        self.queue[0].set()
                    return
            time.sleep(delay)

    def defer(self, seconds):
        with self.lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)


def retry_delay(value, fallback):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = fallback
    return max(0, min(seconds, 60))


class Prober:
    def __init__(self, limiter, timeout=15, retries=2):
        self.limiter = limiter
        self.timeout = timeout
        self.retries = retries
        self.client = None
        self.session_lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.requests_made = 0

    def session(self):
        # HTTPX clients are shared across threads, including the TLS context.
        with self.session_lock:
            if self.client is None:
                self.client = httpx.Client(
                    http2=False,
                    headers={"User-Agent": "Epstein-files-archive-metadata/1.0",
                             "Accept-Encoding": "identity"},
                    limits=httpx.Limits(max_connections=256, max_keepalive_connections=256),
                )
                # Match 2fetcher.py's cookie without following age-gate redirects.
                self.client.cookies.set("justiceGovAgeVerified", "true",
                                        domain="www.justice.gov", path="/")
        return self.client

    def probe(self, pdf, extension):
        record = dict(pdf_url=pdf, extension=extension, status="uncertain",
                      status_code=None, size_bytes=None, content_type="",
                      last_modified="", etag="", checked_at_utc=now(), error="")
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            with self.counter_lock:
                self.requests_made += 1
            try:
                # Never follow redirects or fall back to GET (including 405).
                response = self.session().head(pdf[:-3] + extension, timeout=self.timeout,
                                               follow_redirects=False)
                code = response.status_code
                record.update(status_code=code, checked_at_utc=now(), error="")
                if code in (429, 503):
                    self.limiter.defer(retry_delay(response.headers.get("Retry-After"),
                                                   2 ** attempt))
                if (code in (429, 500, 502, 503, 504) and attempt < self.retries):
                    continue
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                record.update(content_type=mime,
                              last_modified=response.headers.get("Last-Modified", ""),
                              etag=response.headers.get("ETag", ""))
                if code in (404, 410):
                    record["status"] = "missing"
                elif code == 200 and mime and mime not in {"text/html", "application/xhtml+xml"}:
                    record["status"] = "found"
                    length = response.headers.get("Content-Length", "")
                    if re.fullmatch(r"[0-9]+", length):
                        record["size_bytes"] = int(length)
                else:
                    record["error"] = (f"HTTP {code}; Location={response.headers.get('Location', '')}; "
                                       "unexpected status or missing/HTML content type; no file confirmed")
                return record
            except httpx.RequestError as exc:
                record["error"] = str(exc)
                record["checked_at_utc"] = now()
        return record

    def scan(self, pdf, extensions, cache):
        records = []
        for extension in extensions:
            record = cache.get(extension)
            if record is None or record["status"] not in TERMINAL:
                record = self.probe(pdf, extension)
                records.append(record)
            if record["status"] == "found":
                break
        return records


def save_probes(conn, records):
    if records:
        keys = list(records[0])
        with conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO probes ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [tuple(record[key] for key in keys) for record in records])


def result_row(conn, pdf, extensions):
    imported = conn.execute("SELECT record FROM known WHERE pdf_url=?", (pdf,)).fetchone()
    if imported:
        return json.loads(imported[0])
    cache = {row["extension"]: dict(row) for row in
             conn.execute("SELECT * FROM probes WHERE pdf_url=?", (pdf,))}
    relevant = [cache.get(extension) for extension in extensions]
    found = next((row for row in cache.values() if row["status"] == "found"), None)
    row = dict.fromkeys(FIELDS, "")
    row.update(pdf_url=pdf, size_bytes=None, status="incomplete")
    if found:
        row.update({key: found[key] for key in FIELDS if key in found})
        row.update(native_url=pdf[:-3] + found["extension"], size_source="live_head")
    elif all(record and record["status"] == "missing" for record in relevant):
        row["status"] = "not_found_in_tested_extensions"
    return row


def export(conn, urls, extensions, output, requests_made=0):
    rows = [result_row(conn, pdf, extensions) for pdf in urls]
    with (output / "files.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    sizes = [row["size_bytes"] for row in rows if row["status"] in {"known", "found"}
             and row["size_bytes"] is not None]
    summary = dict(input_urls=len(urls), statuses=dict(Counter(row["status"] for row in rows)),
                   known_size_files=len(sizes), known_total_bytes=sum(sizes),
                   known_total_gb=sum(sizes) / 10**9, known_total_gib=sum(sizes) / 2**30,
                   extensions=extensions, head_requests_this_run=requests_made,
                   body_downloads=0, exported_at_utc=now())
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def run(conn, urls, extensions, prober, workers, refresh=False):
    def tasks():
        for pdf in urls:
            if conn.execute("SELECT 1 FROM known WHERE pdf_url=?", (pdf,)).fetchone():
                continue
            cache = {} if refresh else {row["extension"]: dict(row) for row in
                       conn.execute("SELECT * FROM probes WHERE pdf_url=?", (pdf,))}
            if not refresh and any(row["status"] == "found" for row in cache.values()):
                continue
            yield pdf, cache

    # Submit at most `workers` URLs at once, rather than millions of futures.
    iterator = iter(tasks())
    count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        exhausted = False
        while pending or not exhausted:
            while len(pending) < workers and not exhausted:
                try:
                    pdf, cache = next(iterator)
                    pending.add(pool.submit(prober.scan, pdf, extensions, cache))
                except StopIteration:
                    exhausted = True
            if pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    save_probes(conn, future.result())
                    count += 1
                    if count % 50 == 0:
                        print(f"Completed {count} URLs (known files skipped)", file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON, CSV (pdf_url column), or text URL list")
    parser.add_argument("--output", type=Path, default=Path("native_inventory"))
    parser.add_argument("--known-csv", type=Path, help="Import identified metadata; never re-probe these files")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--extensions", help="Comma-separated, case-sensitive extension guesses")
    group.add_argument("--extensions-file", type=Path, help="Whitespace-separated extension guesses")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--rate", type=float, default=20, help="Global maximum HEAD requests/second")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--refresh", action="store_true", help="Recheck cached probes; imported files still skipped")
    parser.add_argument("--export-only", action="store_true", help="Import/export metadata without network requests")
    args = parser.parse_args(argv)
    if not (1 <= args.workers <= 256 and 0 < args.rate <= 200 and
            0 < args.timeout <= 120 and 0 <= args.retries <= 5):
        parser.error("Require 1..256 workers, rate (0,200], timeout (0,120], retries 0..5")
    try:
        urls = load_urls(args.input)
        values = (args.extensions.split(",") if args.extensions else
                  args.extensions_file.read_text(encoding="utf-8").split()
                  if args.extensions_file else MEDIA_EXTENSIONS)
        extensions = extensions_from(values)
        args.output.mkdir(parents=True, exist_ok=True)
        conn = open_state(args.output / "state.sqlite")
        prober = None
        try:
            if args.known_csv:
                import_known(conn, args.known_csv)
            prober = None
            if not args.export_only:
                prober = Prober(RateLimiter(args.rate), args.timeout, args.retries)
                run(conn, urls, extensions, prober, args.workers, args.refresh)
            summary = export(conn, urls, extensions, args.output,
                             prober.requests_made if prober else 0)
        finally:
            if prober is not None and prober.client is not None:
                prober.client.close()
            conn.close()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
