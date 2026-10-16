"""Exercise the user-facing CLI and protect existing annotation files."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "intro.py"
LINK = "https://www.olevod.com/player/vod/2-84001-1.html"


class IntroCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "api/v1/intros/olevod/84001.json"

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *args, "--output-dir", str(self.root)],
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
        return result

    def read(self):
        return json.loads(self.target.read_text())

    def test_create_series_and_parse_time_formats(self):
        for value in ["90", "01:30", "00:01:30"]:
            with self.subTest(value=value):
                self.run_cli(LINK, value, "--title", "兰香如故")
                self.assertEqual(self.read(), {"schemaVersion": 1, "provider": "olevod", "vodId": 84001,
                                              "title": "兰香如故", "defaultIntro": {"start": 0, "end": 90},
                                              "episodes": {}})

    def test_merge_series_episode_exceptions_and_metadata(self):
        self.run_cli(LINK, "90", "--title", "兰香如故")
        data = self.read()
        data["notes"] = "已人工核对"
        self.target.write_text(json.dumps(data))
        self.run_cli(LINK, "03:30.5", "--intro-start", "02:00", "--episode")
        self.run_cli(LINK.replace("-1.html", "-8.html"), "--disable", "--episode")
        result = self.run_cli(LINK, "100")
        data = self.read()
        self.assertEqual(data["defaultIntro"], {"start": 0, "end": 100})
        self.assertEqual(data["episodes"], {"1": {"start": 120, "end": 210.5}, "8": {"disabled": True}})
        self.assertEqual(data["title"], "兰香如故")
        self.assertEqual(data["notes"], "已人工核对")
        self.assertIn("保留 2 个单集例外", result.stdout)

    def test_disable_series_preserves_episode(self):
        self.run_cli(LINK, "45", "--episode")
        self.run_cli(LINK, "--disable")
        self.assertEqual(self.read()["defaultIntro"], {"disabled": True})
        self.assertEqual(self.read()["episodes"]["1"], {"start": 0, "end": 45})

    def test_url_query_is_not_saved_and_episode_keys_are_sorted(self):
        for index in [10, 2, 1]:
            self.run_cli(LINK.replace("-1.html", f"-{index}.html") + "?play=true#section", "45", "--episode")
        self.assertEqual(list(self.read()["episodes"]), ["1", "2", "10"])
        self.assertNotIn("play=true", self.target.read_text())
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_invalid_links_do_not_write_files(self):
        for link in [LINK.replace("https:", "http:"), LINK.replace("www.olevod.com", "other.example"),
                     LINK.replace("/2-", "/1-"), LINK.replace("-1.html", "-0.html"),
                     LINK.replace("www.olevod.com", "user@www.olevod.com"),
                     LINK.replace("www.olevod.com", "www.olevod.com:bad"), "84001"]:
            with self.subTest(link=link):
                self.run_cli(link, "90", ok=False)
                self.assertFalse(self.target.exists())

    def test_invalid_times_do_not_change_existing_file(self):
        self.run_cli(LINK, "90")
        original = self.target.read_bytes()
        for args in [["0"], ["-1"], ["NaN"], ["1:60"], ["1:60:00"], ["1.5:00"],
                     ["90", "--intro-start", "90"], ["90", "--disable"],
                     ["--disable", "--intro-start", "10"], [], ["9" * 400]]:
            with self.subTest(args=args):
                self.run_cli(LINK, *args, ok=False)
                self.assertEqual(self.target.read_bytes(), original)

    def test_bad_existing_documents_are_preserved(self):
        self.run_cli(LINK, "90")
        base = self.read()
        documents = ["{broken", "[]", '{"schemaVersion":1,"schemaVersion":2}',
                     json.dumps({**base, "schemaVersion": 2}), json.dumps({**base, "vodId": 123}),
                     json.dumps({**base, "provider": "other"}), json.dumps({**base, "episodes": []}),
                     json.dumps({**base, "episodes": {"1": {"start": True, "end": 90}}}),
                     json.dumps({**base, "defaultIntro": {"start": 90, "end": 10}}),
                     json.dumps({**base, "defaultIntro": {"disabled": True, "end": 90}})]
        for document in documents:
            with self.subTest(document=document):
                self.target.write_text(document)
                self.run_cli(LINK, "100", ok=False)
                self.assertEqual(self.target.read_text(), document)


if __name__ == "__main__":
    unittest.main()
