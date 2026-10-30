"""scripts/confluence_pull.py: read-only, HTTPS only, allow-listed spaces, never prints the token.

Run:  ./.venv/bin/python3 -m unittest tests.test_confluence_pull -v
"""

import importlib.util
import io
import json
import tempfile
import unittest
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
            raise OSError("connection reset")

        with self.assertRaises(cp.ConfluenceError) as ctx:
            cp.pull_space("https://x.atlassian.net", "KB", self.dest, email="a@b.c", token="SECRET", opener=boom)
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("SECRET", repr(ctx.exception.__cause__))

    def test_empty_space_is_an_error(self):
        with self.assertRaisesRegex(cp.ConfluenceError, "no pages"):
            cp.pull_space("https://x.atlassian.net", "KB", self.dest, email="a", token="t",
                          opener=self.opener({FIRST: {"results": [], "_links": {}}}))

    def test_space_key_is_validated(self):
        with self.assertRaisesRegex(cp.ConfluenceError, "space key"):
            cp.pull_space("https://x.atlassian.net", "KB&x=1", self.dest, email="a", token="t", opener=self.opener({}))


if __name__ == "__main__":
    unittest.main()
