import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

sys_path = pathlib.Path(__file__).parents[1]
import sys
sys.path.insert(0, str(sys_path))

from montage.workflow import VideoAPI, WorkflowError, assemble, load_plan, request_body


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def close(self):
        pass


class Recorder:
    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.values.pop(0))


class WorkflowTests(unittest.TestCase):
    def plan_file(self, root):
        path = root / "plan.yml"
        path.write_text(
            "model: seedance-2.5\n"
            "resolution: 720p\n"
            'aspect_ratio: "16:9"\n'
            "reference_images: [https://cdn.test/character.png]\n"
            "shots:\n"
            "  - {id: opening, duration: 5, prompt: 'enter the station'}\n"
            "  - {id: clue, duration: 6, prompt: 'read the envelope'}\n",
            encoding="utf-8",
        )
        return path

    def test_plan_normalizes_ids_and_preserves_reference_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            plan = load_plan(self.plan_file(root))
            self.assertEqual([s["id"] for s in plan["shots"]], ["opening", "clue"])
            body = request_body(plan, plan["shots"][0])
            self.assertEqual(body["model"], "seedance-2.5")
            self.assertEqual(body["reference_images"], [{"url": "https://cdn.test/character.png"}])

    def test_plan_rejects_non_https_reference_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "bad.yml"
            path.write_text(
                "shots:\n  - {id: same, prompt: x}\n  - {id: same, prompt: y}\n"
                "reference_images: [http://cdn.test/a.png]\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_plan(path)

    def test_api_sets_auth_and_user_agent(self):
        recorder = Recorder([b'{"request_id":"job_1"}'])
        api = VideoAPI("secret", base_url="https://api.test/v1", opener=recorder)
        self.assertEqual(api.submit({"model": "seedance-2.5", "prompt": "x"}), "job_1")
        request = recorder.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("User-agent"), "seedance-2-5-montage-workflow/1.0")

    def test_concat_writes_a_valid_ffconcat_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = root / "first clip.mp4"
            second = root / "a'clip.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            output = root / "out.mp4"
            seen = {}
            def fake_run(command, check):
                seen["command"] = command
                manifest = pathlib.Path(command[command.index("-i") + 1])
                seen["contents"] = manifest.read_text()
            with patch("montage.workflow.subprocess.run", side_effect=fake_run):
                assemble([first, second], output)
            self.assertIn("ffconcat version 1.0", seen["contents"])
            self.assertIn("first clip.mp4", seen["contents"])
            self.assertIn("a'\\\\''clip.mp4", seen["contents"])
            self.assertEqual(seen["command"][0], "ffmpeg")

    def test_unsupported_status_fails_before_loop(self):
        recorder = Recorder([b'{"status":"running"}'])
        api = VideoAPI("secret", base_url="https://api.test/v1", opener=recorder)
        with self.assertRaises(WorkflowError):
            api.status("job_1")


if __name__ == "__main__":
    unittest.main()
