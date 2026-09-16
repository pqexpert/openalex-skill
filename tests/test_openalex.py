"""Offline checks for credential confinement, paging, and faithful exports."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

SPEC = importlib.util.spec_from_file_location("openalex_client", Path(__file__).parents[1] / "scripts/openalex.py")
oa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oa)
FAKE_KEY = "offline-test-credential"


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())
        self.headers = {"X-RateLimit-Remaining": "999"}


class ClientTests(unittest.TestCase):
    def test_key_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apiopenalex"
            path.write_text("\ufeff" + FAKE_KEY + "\n")
            with patch.dict(os.environ, {"OPENALEX_API_KEY": "another-test-key"}, clear=True):
                self.assertEqual(oa.load_key(str(path)), FAKE_KEY)
                with self.assertRaises(oa.OpenAlexError):
                    oa.load_key(str(path) + ".missing")
                self.assertEqual(oa.load_key(), "another-test-key")
            path.write_text('export OPENALEX_API_KEY="' + FAKE_KEY + '"\n')
            self.assertEqual(oa.load_key(str(path)), FAKE_KEY)

    def test_empty_environment_key_does_not_fall_back(self):
        with patch.dict(os.environ, {"OPENALEX_API_KEY": ""}, clear=True):
            with self.assertRaises(oa.OpenAlexError):
                oa.load_key()

    def test_bearer_only_and_response_redaction(self):
        client = oa.Client(FAKE_KEY)
        seen = []
        def open_request(req, timeout):
            seen.append(req)
            return Response({"api_key": FAKE_KEY, "nested": {"message": "echo " + FAKE_KEY}})
        with patch.object(client.opener, "open", side_effect=open_request):
            result = client.get("rate-limit", verify_identity=True)
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_KEY, seen[0].full_url)
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer " + FAKE_KEY)
        self.assertEqual(urlsplit(seen[0].full_url).hostname, "api.openalex.org")

    def test_status_rejects_unconfirmed_identity(self):
        client = oa.Client(FAKE_KEY)
        with patch.object(client.opener, "open", return_value=Response({"api_key": None})):
            with self.assertRaises(oa.OpenAlexError):
                client.get("rate-limit", verify_identity=True)

    def test_status_accepts_only_matching_masked_key_hints(self):
        self.assertTrue(oa.credential_matches("offlin...", FAKE_KEY))
        self.assertTrue(oa.credential_matches("off***tial", FAKE_KEY))
        self.assertFalse(oa.credential_matches("wrong...", FAKE_KEY))
        self.assertFalse(oa.credential_matches("***", FAKE_KEY))
        self.assertFalse(oa.credential_matches(None, FAKE_KEY))
        client = oa.Client(FAKE_KEY)
        with patch.object(client.opener, "open", return_value=Response({"api_key": "offlin..."})):
            result = client.get("rate-limit", verify_identity=True)
        self.assertEqual(result["api_key"], "[REDACTED]")

    def test_query_encoding_preserves_or_and_plus(self):
        client = oa.Client(FAKE_KEY)
        with patch.object(client.opener, "open", return_value=Response({"results": []})) as opened:
            client.get("works", {"filter": "type:article|preprint", "search": "C++ & quantum"})
        query = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(query["filter"], ["type:article|preprint"])
        self.assertEqual(query["search"], ["C++ & quantum"])
        with self.assertRaises(oa.OpenAlexError):
            client.get("works", {"api_key": FAKE_KEY})

    def test_redirects_keep_key_on_origin(self):
        handler = oa.SameOriginRedirect()
        request = Request(oa.BASE + "/authors/A1", headers={"Authorization": "Bearer " + FAKE_KEY})
        for target in ("https://evil.example/", "http://api.openalex.org/", "https://api.openalex.org.evil.example/", "https://api.openalex.org:444/"):
            with self.subTest(target=target), self.assertRaises(oa.OpenAlexError):
                handler.redirect_request(request, None, 301, "Moved", {}, target)
        new = handler.redirect_request(request, None, 301, "Moved", {}, oa.BASE + "/authors/A2")
        self.assertEqual(new.get_header("Authorization"), "Bearer " + FAKE_KEY)

    def test_http_error_does_not_echo_url_or_body_or_retry_auth(self):
        client = oa.Client(FAKE_KEY)
        error = HTTPError(oa.BASE + "?api_key=" + FAKE_KEY, 401, FAKE_KEY, {}, io.BytesIO(FAKE_KEY.encode()))
        with patch.object(client.opener, "open", side_effect=error) as opened:
            with self.assertRaises(oa.OpenAlexError) as raised:
                client.get("works")
        self.assertEqual(opened.call_count, 1)
        self.assertNotIn(FAKE_KEY, str(raised.exception))

    def test_retry_is_bounded_and_honors_wait(self):
        client = oa.Client(FAKE_KEY)
        error = HTTPError(oa.BASE, 429, "rate", {"Retry-After": "1"}, io.BytesIO())
        with patch.object(client.opener, "open", side_effect=[error, Response({"results": []})]) as opened, patch.object(oa.time, "sleep") as slept:
            client.get("works")
        self.assertEqual(opened.call_count, 2)
        slept.assert_called_once_with(1)
        error = HTTPError(oa.BASE, 429, "rate", {"Retry-After": "900"}, io.BytesIO())
        with patch.object(client.opener, "open", side_effect=error), patch.object(oa.time, "sleep") as slept:
            with self.assertRaises(oa.OpenAlexError):
                client.get("works")
        slept.assert_not_called()

    def test_budget_exhaustion_stops(self):
        client = oa.Client(FAKE_KEY)
        error = HTTPError(oa.BASE, 429, "rate", {"X-RateLimit-Remaining": "0"}, io.BytesIO())
        with patch.object(client.opener, "open", side_effect=error) as opened:
            with self.assertRaises(oa.OpenAlexError):
                client.get("works")
        self.assertEqual(opened.call_count, 1)

    def test_external_ids_stay_in_api_path(self):
        cases = [("works", "https://doi.org/10.7717/peerj.4375", "works/doi:10.7717/peerj.4375"),
                 ("authors", "https://openalex.org/a123", "authors/A123"),
                 ("authors", "https://orcid.org/0000-0003-1613-5981", "authors/orcid:0000-0003-1613-5981"),
                 ("institutions", "https://ror.org/02y3ad647", "institutions/ror:02y3ad647")]
        for entity, value, expected in cases:
            self.assertEqual(oa.singleton_path(entity, value), expected)
        for entity, value in [("works", "A123"), ("works", "https://evil.example/steal"), ("authors", "Einstein")]:
            with self.assertRaises(oa.OpenAlexError):
                oa.singleton_path(entity, value)


class PagingAndExportTests(unittest.TestCase):
    def test_cursor_resume_bound_and_provenance(self):
        client = oa.Client(FAKE_KEY)
        responses = [{"meta": {"count": 9, "next_cursor": "page-two", "cost_usd": 0.001}, "results": [{"id": "W1"}, {"id": "W2"}]},
                     {"meta": {"count": 9, "next_cursor": "page-three", "cost_usd": 0.001}, "results": [{"id": "W3"}]}]
        with patch.object(client, "get", side_effect=responses) as get:
            result = oa.collect(client, "works", {"search": "quantum"}, limit=3, per_page=2)
        self.assertEqual(result["meta"]["stop_reason"], "limit")
        self.assertEqual(result["meta"]["next_cursor"], "page-three")
        self.assertEqual(get.call_args_list[1].args[1]["per_page"], 1)
        self.assertEqual(get.call_args_list[1].args[1]["cursor"], "page-two")
        self.assertEqual(result["meta"]["reported_count"], 9)
        self.assertEqual(result["meta"]["cost_usd_observed"], 0.002)

    def test_duplicate_records_and_repeated_cursor_stop(self):
        client = oa.Client(FAKE_KEY)
        data = {"meta": {"count": 3, "next_cursor": "*"}, "results": [{"id": "W1"}, {"id": "W1"}]}
        with patch.object(client, "get", return_value=data) as get:
            result = oa.collect(client, "works", {}, limit=3)
        self.assertEqual(result["meta"]["returned_count"], 1)
        self.assertEqual(result["meta"]["stop_reason"], "repeated_cursor")
        self.assertEqual(get.call_count, 1)

    def test_max_pages_and_empty_page(self):
        client = oa.Client(FAKE_KEY)
        with patch.object(client, "get", return_value={"meta": {"next_cursor": "next"}, "results": [{"id": "W1"}]}):
            result = oa.collect(client, "works", {}, limit=20, max_pages=1)
        self.assertEqual(result["meta"]["stop_reason"], "max_pages")
        with patch.object(client, "get", return_value={"meta": {"next_cursor": None}, "results": []}):
            result = oa.collect(client, "works", {})
        self.assertEqual(result["meta"]["stop_reason"], "exhausted")

    def test_group_paging_uses_group_records(self):
        client = oa.Client(FAKE_KEY)
        data = {"meta": {"count": 200, "next_cursor": None}, "group_by": [{"key": "2025", "count": 12}], "results": []}
        with patch.object(client, "get", return_value=data):
            result = oa.collect(client, "works", {"group_by": "publication_year"}, grouped=True)
        self.assertEqual(result["groups"][0]["count"], 12)

    def test_abstract_missing_index_and_gaps(self):
        self.assertEqual(oa.abstract_text({"is": [1], "Research": [0], "useful.": [2]}), "Research is useful.")
        self.assertIsNone(oa.abstract_text(None))
        self.assertEqual(oa.abstract_text({"a": [0], "b": [2]}), "a [missing] b")

    def test_csv_neutralizes_formulas_and_handles_nulls(self):
        text = oa.render_csv([{"id": "W1", "display_name": "=HYPERLINK(\"bad\")", "primary_location": None, "authorships": None}])
        self.assertIn("'=HYPERLINK", text)
        self.assertEqual(oa.safe_cell("  @evil"), "'  @evil")

    def test_jsonl_export_has_metadata_and_preserves_existing_file(self):
        payload = {"meta": {"query": {"search": "quantum"}}, "results": [{"id": "W1"}]}
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "result.jsonl"
            oa.write_output(payload, "jsonl", path)
            self.assertEqual(json.loads(path.read_text()), {"id": "W1"})
            self.assertEqual(json.loads(Path(str(path) + ".meta.json").read_text()), payload["meta"])
            with self.assertRaises(oa.OpenAlexError):
                oa.write_output(payload, "jsonl", path)


if __name__ == "__main__":
    unittest.main()
