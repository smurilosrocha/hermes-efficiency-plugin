"""Tests for the observer: the five hooks, the privacy contract and the error classification.

They run offline with EFICIENCIA_ROOT pointing at a temporary directory; no real data is read or written.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("eficiencia_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eficiencia_plugin_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeContext:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("EFICIENCIA_ROOT", None))
        os.environ["EFICIENCIA_ROOT"] = self.tmp.name
        self.plugin = load_plugin()
        self.ctx = FakeContext()
        self.plugin.register(self.ctx)

    def register_request(self, rid="r1", prompt="PROMPT-CANARY"):
        self.ctx.hooks["pre_api_request"](
            api_request_id=rid, session_id="s1", task_id="t1", platform="cli",
            model="m", provider="p", api_mode="chat", api_call_count=1, retry_count=0,
            started_at=time.time(), request_messages=[{"role": "user", "content": prompt}],
            system_prompt="rules", request_char_count=10, tool_count=2,
        )

    def records(self, sub="calls"):
        folder = Path(self.tmp.name) / "eficiencia" / sub
        files = sorted(folder.glob("*.jsonl"))
        self.assertTrue(files, f"no file in {folder}")
        return [json.loads(line) for line in files[-1].read_text().splitlines() if line.strip()]

    def day_file(self):
        folder = Path(self.tmp.name) / "eficiencia" / "calls"
        return folder, folder / (time.strftime("%Y-%m-%d") + ".jsonl")


class TestRegistration(Base):
    def test_registers_exactly_the_five_declared_hooks(self):
        self.assertEqual(set(self.ctx.hooks), {
            "pre_api_request", "post_api_request", "api_request_error",
            "post_tool_call", "post_auxiliary_call",
        })


class TestPrivacy(Base):
    def test_never_writes_prompt_text(self):
        self.register_request()
        self.ctx.hooks["post_api_request"](api_request_id="r1", ended_at=time.time(),
                                          usage={"input_tokens": 5})
        raw = (Path(self.tmp.name) / "eficiencia" / "calls").glob("*.jsonl")
        content = "".join(p.read_text() for p in raw)
        self.assertNotIn("PROMPT-CANARY", content)
        self.assertEqual(len(self.records()), 1)

    def test_error_message_is_not_persisted(self):
        self.register_request()
        self.ctx.hooks["api_request_error"](api_request_id="r1",
                                            error={"type": "E", "message": "error-canary"})
        content = "".join(p.read_text() for p in
                          (Path(self.tmp.name) / "eficiencia" / "calls").glob("*.jsonl"))
        self.assertNotIn("error-canary", content)

    def test_tool_call_records_size_and_name_only(self):
        self.ctx.hooks["post_tool_call"](session_id="s1", task_id="t1", tool_name="read_file",
                                         duration_ms=12, result="secret-content", status="ok")
        (record,) = self.records("tools")
        self.assertEqual(record["tool"], "read_file")
        self.assertEqual(record["chars"], len("secret-content"))
        self.assertNotIn("secret-content", json.dumps(record))


class TestErrorClassification(Base):
    def test_error_type_comes_from_the_error_dict(self):
        """The hook passes error={"type": ...} and no error_type keyword."""
        self.register_request()
        self.ctx.hooks["api_request_error"](
            api_request_id="r1", status_code=429,
            error={"type": "RateLimitError", "message": "canary-message"},
        )
        (record,) = self.records()
        self.assertEqual(record["err_type"], "RateLimitError")
        self.assertEqual(record["err"], 1)
        self.assertEqual(record["status"], 429)

    def test_explicit_error_type_still_wins(self):
        self.register_request()
        self.ctx.hooks["api_request_error"](api_request_id="r1", error_type="TimeoutError",
                                            error={"type": "Other", "message": "x"})
        (record,) = self.records()
        self.assertEqual(record["err_type"], "TimeoutError")


class TestWrite(Base):
    def test_refuses_symlink_at_the_day_file(self):
        folder, day = self.day_file()
        folder.mkdir(parents=True, exist_ok=True)
        target = Path(self.tmp.name) / "target.txt"
        target.write_text("untouched")
        day.symlink_to(target)
        self.register_request()
        self.ctx.hooks["post_api_request"](api_request_id="r1", ended_at=time.time(),
                                          usage={"input_tokens": 1})
        self.assertEqual(target.read_text(), "untouched")
        self.assertTrue(day.is_symlink())

    def test_fixes_permissions_of_a_preexisting_file(self):
        folder, day = self.day_file()
        folder.mkdir(parents=True, exist_ok=True)
        day.write_text("")
        os.chmod(day, 0o644)
        self.register_request()
        self.ctx.hooks["post_api_request"](api_request_id="r1", ended_at=time.time(),
                                          usage={"input_tokens": 1})
        self.assertEqual(day.stat().st_mode & 0o777, 0o600)

    def test_overflow_is_not_silent(self):
        self.plugin._MAX_PENDING = 2
        for i in range(3):
            self.register_request(rid=f"r{i}")
        records = self.records()
        incomplete = [r for r in records if r.get("incomplete") == 1]
        self.assertEqual(len(incomplete), 1, records)
        self.assertEqual(incomplete[0]["session"], "s1")
        self.assertNotIn("PROMPT-CANARY", json.dumps(incomplete[0]))


if __name__ == "__main__":
    unittest.main()
