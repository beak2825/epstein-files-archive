import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import httpx

import native_probe as probe


PDF = "https://www.justice.gov/epstein/files/DataSet%209/EFTA01187179.pdf"


def response(code=200, **headers):
    result = Mock(status_code=code, headers=headers)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.prober = probe.Prober(Mock(), retries=0)
        self.session = Mock()
        self.prober.client = self.session

    def test_head_only_no_redirects_and_unknown_size(self):
        self.session.head.return_value = response(**{"Content-Type": "audio/mp4"})
        result = self.prober.probe(PDF, "m4a")
        self.assertEqual(result["status"], "found")
        self.assertIsNone(result["size_bytes"])
        self.session.head.assert_called_once_with(PDF[:-3] + "m4a", timeout=15,
                                                  follow_redirects=False)
        self.session.get.assert_not_called()

    def test_session_reused_with_scoped_doj_cookie(self):
        prober = probe.Prober(Mock())
        session = prober.session()
        try:
            self.assertIs(session, prober.session())
            cookies = list(session.cookies.jar)
            self.assertEqual([(c.name, c.value, c.domain) for c in cookies],
                             [("justiceGovAgeVerified", "true", "www.justice.gov")])
        finally:
            session.close()

    def test_sizes_including_zero_and_malformed_length(self):
        for length, expected in [("0", 0), ("123456", 123456), ("-1", None), ("oops", None)]:
            with self.subTest(length=length):
                self.session.head.return_value = response(**{"Content-Type": "video/mp4",
                                                            "Content-Length": length})
                self.assertEqual(self.prober.probe(PDF, "mp4")["size_bytes"], expected)

    def test_challenges_redirects_and_errors_are_not_missing(self):
        for code, mime in [(200, "text/html; charset=utf-8"), (200, ""), (302, ""),
                           (403, "text/html"), (405, ""), (429, ""), (500, "")]:
            with self.subTest(code=code, mime=mime):
                self.session.head.return_value = response(code, **{"Content-Type": mime})
                self.assertEqual(self.prober.probe(PDF, "mp4")["status"], "uncertain")
        self.session.get.assert_not_called()

    def test_only_404_and_410_are_missing(self):
        for code in (404, 410):
            self.session.head.return_value = response(code)
            self.assertEqual(self.prober.probe(PDF, "mp4")["status"], "missing")

    def test_retries_share_rate_limit_and_obey_retry_after(self):
        self.prober.retries = 1
        self.session.head.side_effect = [response(429, **{"Retry-After": "4"}),
                                          response(**{"Content-Type": "video/mp4"})]
        self.assertEqual(self.prober.probe(PDF, "mp4")["status"], "found")
        self.assertEqual(self.prober.limiter.acquire.call_count, 2)
        self.prober.limiter.defer.assert_called_once_with(4)

    def test_timeouts_stay_retryable(self):
        self.prober.retries = 1
        self.session.head.side_effect = httpx.ReadTimeout("timed out")
        result = self.prober.probe(PDF, "mp4")
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("timed out", result["error"])
        self.assertEqual(self.session.head.call_count, 2)

    def test_scan_reuses_missing_retries_uncertain_and_stops_at_found(self):
        self.session.head.return_value = response(**{"Content-Type": "video/quicktime"})
        records = self.prober.scan(PDF, ["mp4", "mov", "mp3"], {
            "mp4": {"status": "missing"}, "mov": {"status": "uncertain"}})
        self.assertEqual([row["extension"] for row in records], ["mov"])
        self.session.head.assert_called_once()


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = probe.open_state(self.root / "state.sqlite")
        self.addCleanup(self.conn.close)

    def save(self, extension, status, size=None):
        probe.save_probes(self.conn, [dict(pdf_url=PDF, extension=extension, status=status,
            status_code=200 if status == "found" else 404, size_bytes=size,
            content_type="video/mp4", last_modified="", etag="", checked_at_utc=probe.now(), error="")])

    def test_partial_scan_is_incomplete_not_missing(self):
        self.save("mp4", "missing")
        self.assertEqual(probe.result_row(self.conn, PDF, ["mp4", "mov"])["status"], "incomplete")
        self.save("mov", "missing")
        self.assertEqual(probe.result_row(self.conn, PDF, ["mp4", "mov"])["status"],
                         "not_found_in_tested_extensions")

    def test_found_is_reused_when_extension_list_changes(self):
        self.save("mp4", "found", 123)
        prober = Mock()
        probe.run(self.conn, [PDF], ["mov"], prober, 2)
        prober.scan.assert_not_called()
        result = probe.result_row(self.conn, PDF, ["mov"])
        self.assertEqual((result["status"], result["extension"], result["size_bytes"]),
                         ("found", "mp4", 123))

    def test_state_survives_reopen_and_does_not_repeat_missing_probes(self):
        self.save("mp4", "missing")
        with probe.open_state(self.root / "state.sqlite") as reopened:
            prober = probe.Prober(Mock(), retries=0)
            prober.client = Mock()
            # Run scan synchronously because sessions are thread-local.
            cache = {r["extension"]: dict(r) for r in reopened.execute("SELECT * FROM probes")}
            self.assertEqual(prober.scan(PDF, ["mp4"], cache), [])
            prober.client.head.assert_not_called()

    def test_refresh_replaces_previous_success(self):
        self.save("mp4", "found", 123)
        prober = Mock()
        prober.scan.return_value = [dict(pdf_url=PDF, extension="mp4", status="missing",
            status_code=404, size_bytes=None, content_type="", last_modified="", etag="",
            checked_at_utc=probe.now(), error="")]
        probe.run(self.conn, [PDF], ["mp4"], prober, 1, refresh=True)
        self.assertEqual(probe.result_row(self.conn, PDF, ["mp4"])["status"],
                         "not_found_in_tested_extensions")

    def known_csv(self):
        path = self.root / "known.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=probe.FIELDS)
            writer.writeheader()
            writer.writerow(dict(pdf_url=PDF, native_url=PDF[:-3] + "m4a", extension="m4a",
                status="available_local", size_bytes=50, size_source="existing_download",
                checked_at_utc="2026-01-01T00:00:00+00:00"))
            writer.writerow(dict(pdf_url=PDF.replace("1179", "1180"), status="not_found_in_tested_extensions"))
        return path

    def test_known_files_never_reprobed_including_refresh(self):
        self.assertEqual(probe.import_known(self.conn, self.known_csv()), 1)
        prober = Mock()
        probe.run(self.conn, [PDF], ["mp4"], prober, 1, refresh=True)
        prober.scan.assert_not_called()
        summary = probe.export(self.conn, [PDF], ["mp4"], self.root)
        self.assertEqual(summary["known_total_bytes"], 50)
        with (self.root / "files.csv").open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["size_source"], "existing_download")
        self.assertEqual(row["checked_at_utc"], "2026-01-01T00:00:00+00:00")

    def test_export_excludes_unknown_sizes_and_unrelated_state(self):
        self.save("mp4", "found", None)
        summary = probe.export(self.conn, [PDF], ["mp4"], self.root)
        self.assertEqual(summary["known_size_files"], 0)
        self.assertEqual(summary["known_total_bytes"], 0)
        empty = probe.export(self.conn, [], ["mp4"], self.root)
        self.assertEqual(empty["input_urls"], 0)

    def test_export_only_cli_makes_no_requests(self):
        source = self.root / "urls.json"
        source.write_text(json.dumps([PDF]))
        with patch.object(probe, "Prober") as prober, patch("sys.stdout", new_callable=io.StringIO):
            probe.main([str(source), "--known-csv", str(self.known_csv()),
                        "--output", str(self.root / "out"), "--export-only"])
        prober.assert_not_called()
        result = json.loads((self.root / "out" / "summary.json").read_text())
        self.assertEqual(result["known_total_bytes"], 50)


class InputTests(unittest.TestCase):
    def test_normalization_and_validation(self):
        self.assertEqual(probe.normalize_pdf(PDF.replace("%20", " ")), PDF)
        for value in [PDF.replace("justice.gov", "example.org"), PDF + "?q=1",
                      PDF.replace("https:", "http:"), PDF.replace(".pdf", ".mp4")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.normalize_pdf(value)

    def test_extensions_keep_case_and_compound_suffixes(self):
        self.assertEqual(probe.extensions_from([".mp4", "MOV", "tar.gz", "mp4"]),
                         ["mp4", "MOV", "tar.gz"])
        for values in [["pdf"], ["../mp4"], [], ["mp4?foo"]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                probe.extensions_from(values)

    def test_json_array_and_doj_search_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "urls.json"
            path.write_text(json.dumps([PDF, PDF.replace("%20", " ")]))
            self.assertEqual(probe.load_urls(path), [PDF])
            path.write_text(json.dumps({"hits": {"hits": [
                {"_source": {"ORIGIN_FILE_URI": PDF}}]}}))
            self.assertEqual(probe.load_urls(path), [PDF])

    def test_retry_delay_date_and_cap(self):
        self.assertEqual(probe.retry_delay("999", 1), 60)
        self.assertEqual(probe.retry_delay("bad", 2), 2)
        self.assertEqual(probe.retry_delay("Wed, 01 Jan 2020 00:00:00 GMT", 1), 0)

    def test_rate_limit_is_shared(self):
        limiter = probe.RateLimiter(10)
        with patch.object(probe.time, "monotonic", side_effect=[0, 0, 0.05, 0.1, 0.1]), \
                patch.object(probe.time, "sleep") as sleep:
            limiter.acquire()
            limiter.acquire()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.05)


class HTTPIntegrationTests(unittest.TestCase):
    def test_real_client_reuses_connection_and_never_sends_get(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_HEAD(self):
                calls.append((self.command, self.client_address))
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", "1000000000")
                self.end_headers()

            def do_GET(self):
                calls.append((self.command, self.client_address))
                self.send_error(405)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        prober = probe.Prober(Mock(), retries=0)
        try:
            pdf = f"http://127.0.0.1:{server.server_port}/example.pdf"
            for extension in ("mp4", "mov"):
                result = prober.probe(pdf, extension)
                self.assertEqual(result["size_bytes"], 1000000000)
                self.assertEqual(result["status"], "found")
            self.assertEqual([method for method, _ in calls], ["HEAD", "HEAD"])
            self.assertEqual(calls[0][1], calls[1][1])
            self.assertEqual(prober.requests_made, 2)
        finally:
            prober.session().close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
