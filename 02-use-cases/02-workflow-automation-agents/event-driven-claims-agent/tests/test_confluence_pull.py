"""scripts/confluence_pull.py: read-only, HTTPS only, allow-listed spaces, never prints the token.

Run:  ./.venv/bin/python3 -m unittest tests.test_confluence_pull -v
"""

import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("confluence_pull", ROOT / "scripts" / "confluence_pull.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

FIRST = "https://x.atlassian.net/wiki/rest/api/content?spaceKey=KB&type=page&expand=body.storage&limit=50"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def page(pid, title, body):
    return {"id": pid, "title": title, "body": {"storage": {"value": body}}}


class PullTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name)
        self.requests = []

    def opener(self, pages_by_url):
        def _open(request, timeout=30):
            self.requests.append(request)
            return Resp(json.dumps(pages_by_url[request.full_url]).encode())

        return _open

    def test_follows_pagination_and_writes_html_with_a_title(self):
        responses = {
            FIRST: {"results": [page("1", "Hardship Policy", "<p>Be kind</p>")], "_links": {"next": "/wiki/rest/api/content?start=50"}},
            "https://x.atlassian.net/wiki/rest/api/content?start=50": {"results": [page("2", "Claims / Guide", "<p>Send a form</p>")], "_links": {}},
        }
        names = cp.pull_space("https://x.atlassian.net", "KB", self.dest, email="a@b.c", token="SECRET", opener=self.opener(responses))
        self.assertEqual(names, ["KB-1-hardship-policy.html", "KB-2-claims-guide.html"])
        self.assertIn("<title>Hardship Policy</title>", (self.dest / names[0]).read_text(encoding="utf-8"))
        self.assertTrue(all(r.get_method() == "GET" for r in self.requests))

    def test_http_is_refused(self):
        with self.assertRaisesRegex(cp.ConfluenceError, "HTTPS"):
            cp.pull_space("http://x.atlassian.net", "KB", self.dest, email="a", token="t", opener=self.opener({}))

    def test_token_never_appears_in_an_error(self):
        def boom(request, timeout=30):
            raise OSError("failed with Authorization for a@b.c using SECRET")

        with self.assertRaises(cp.ConfluenceError) as ctx:
            cp.pull_space("https://x.atlassian.net", "KB", self.dest, email="a@b.c", token="SECRET", opener=boom)
        for text in (str(ctx.exception), repr(ctx.exception)):
            self.assertNotIn("SECRET", text)
            self.assertNotIn("a@b.c", text)
        self.assertIsNone(ctx.exception.__cause__)

    def test_empty_space_is_an_error(self):
        with self.assertRaisesRegex(cp.ConfluenceError, "no pages"):
            cp.pull_space("https://x.atlassian.net", "KB", self.dest, email="a", token="t",
                          opener=self.opener({FIRST: {"results": [], "_links": {}}}))

    def test_space_key_is_validated(self):
        with self.assertRaisesRegex(cp.ConfluenceError, "space key"):
            cp.pull_space("https://x.atlassian.net", "KB&x=1", self.dest, email="a", token="t", opener=self.opener({}))


BASE = "https://x.atlassian.net"
FIRST_PATH = "/wiki/rest/api/content?spaceKey=KB&type=page&expand=body.storage&limit=50"


class FixRoundOneTests(unittest.TestCase):
    """Credential-safety defects from the review of Task 8: redirects, next links, ids, loops and error text."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name)
        self.requests = []

    def serving(self, responses):
        """Fake opener: records every request before answering. Unknown URLs get an empty page. Loop guard stops runaway tests."""

        def _open(request, timeout=30):
            self.requests.append(request)
            if len(self.requests) > 20:
                raise RuntimeError("test loop guard: too many requests")
            body = responses.get(request.full_url, {"results": [], "_links": {}})
            return Resp(json.dumps(body).encode())

        return _open

    def pull(self, responses, **kwargs):
        return cp.pull_space(BASE, "KB", self.dest, email="a@b.c", token="SECRET", opener=self.serving(responses), **kwargs)

    def test_hostile_next_links_are_refused_before_any_request(self):
        for nxt in ("@evil.com/x", "//evil.com/x", "https://evil.com/x", "/other/path", "http://x.atlassian.net/wiki/rest/api/content"):
            with self.subTest(nxt=nxt):
                self.requests.clear()
                responses = {FIRST: {"results": [page("1", "T", "<p>x</p>")], "_links": {"next": nxt}}}
                with self.assertRaisesRegex(cp.ConfluenceError, "next link"):
                    self.pull(responses)
                self.assertEqual([r.full_url for r in self.requests], [FIRST])

    def test_redirect_handler_refuses_to_follow(self):
        handler = cp._NoRedirect()
        request = urllib.request.Request(FIRST)
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://evil.com/"))

    def test_a_redirect_status_is_a_clean_error_without_the_token(self):
        def redirected(request, timeout=30):
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)

        with self.assertRaises(cp.ConfluenceError) as ctx:
            cp.pull_space(BASE, "KB", self.dest, email="a@b.c", token="SECRET", opener=redirected)
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("a@b.c", str(ctx.exception))

    def test_a_403_is_a_clean_error_without_the_token(self):
        def forbidden(request, timeout=30):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with self.assertRaises(cp.ConfluenceError) as ctx:
            cp.pull_space(BASE, "KB", self.dest, email="a@b.c", token="SECRET", opener=forbidden)
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_invalid_json_is_a_confluence_error(self):
        def not_json(request, timeout=30):
            return Resp(b"<html>not json</html>")

        with self.assertRaises(cp.ConfluenceError):
            cp.pull_space(BASE, "KB", self.dest, email="a", token="t", opener=not_json)

    def test_traversal_id_is_refused_and_nothing_is_written(self):
        item = {"id": "../../x", "title": "T", "body": {"storage": {"value": "<p>x</p>"}}}
        with self.assertRaisesRegex(cp.ConfluenceError, "id"):
            self.pull({FIRST: {"results": [item], "_links": {}}})
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_missing_id_is_a_confluence_error(self):
        item = {"title": "T", "body": {"storage": {"value": "<p>x</p>"}}}
        with self.assertRaisesRegex(cp.ConfluenceError, "id"):
            self.pull({FIRST: {"results": [item], "_links": {}}})

    def test_null_title_is_a_confluence_error(self):
        item = {"id": "1", "title": None, "body": {"storage": {"value": "<p>x</p>"}}}
        with self.assertRaisesRegex(cp.ConfluenceError, "title"):
            self.pull({FIRST: {"results": [item], "_links": {}}})

    def test_non_string_body_is_a_confluence_error(self):
        item = {"id": "1", "title": "T", "body": {"storage": {"value": None}}}
        with self.assertRaisesRegex(cp.ConfluenceError, "body"):
            self.pull({FIRST: {"results": [item], "_links": {}}})

    def test_a_next_link_back_to_an_earlier_page_is_refused(self):
        responses = {FIRST: {"results": [page("1", "T", "<p>x</p>")], "_links": {"next": FIRST_PATH}}}
        with self.assertRaisesRegex(cp.ConfluenceError, "repeated"):
            self.pull(responses)
        self.assertEqual(len(self.requests), 1)

    def test_page_cap_is_enforced(self):
        p2, p3 = "/wiki/rest/api/content?start=1", "/wiki/rest/api/content?start=2"
        responses = {
            FIRST: {"results": [page("1", "A", "<p>a</p>")], "_links": {"next": p2}},
            BASE + p2: {"results": [page("2", "B", "<p>b</p>")], "_links": {"next": p3}},
            BASE + p3: {"results": [page("3", "C", "<p>c</p>")], "_links": {}},
        }
        with self.assertRaisesRegex(cp.ConfluenceError, "more than 2 pages"):
            self.pull(responses, max_pages=2)

    def test_pages_under_the_cap_are_all_written(self):
        p2, p3 = "/wiki/rest/api/content?start=1", "/wiki/rest/api/content?start=2"
        responses = {
            FIRST: {"results": [page("1", "A", "<p>a</p>")], "_links": {"next": p2}},
            BASE + p2: {"results": [page("2", "B", "<p>b</p>")], "_links": {"next": p3}},
            BASE + p3: {"results": [page("3", "C", "<p>c</p>")], "_links": {}},
        }
        self.assertEqual(len(self.pull(responses, max_pages=3)), 3)


if __name__ == "__main__":
    unittest.main()
