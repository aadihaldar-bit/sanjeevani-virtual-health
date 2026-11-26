"""Every Gemini call writes its token counts to a log the app can add up later, and a logging failure never fails the call."""
import importlib
import io
import json
import sys
import types

import pytest

import llm
import usage_log
import usage_report


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG", str(path))
    return path


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def sdk_usage(**counts):
    """What google-genai returns as response.usage_metadata."""
    base = {"prompt_token_count": 0, "candidates_token_count": 0, "thoughts_token_count": 0,
            "cached_content_token_count": 0, "total_token_count": 0}
    return types.SimpleNamespace(**{**base, **counts})


def test_sdk_usage_is_recorded_with_thinking_kept_apart(log):
    usage_log.record("careplan", "gemini-3.1-flash-lite",
                     sdk_usage(prompt_token_count=1200, candidates_token_count=300, thoughts_token_count=450, total_token_count=1950), 2.5)
    (row,) = lines(log)
    assert (row["purpose"], row["model"]) == ("careplan", "gemini-3.1-flash-lite")
    assert (row["input"], row["output"], row["thoughts"], row["total"], row["seconds"]) == (1200, 300, 450, 1950, 2.5)


def test_rest_usage_is_recorded(log):
    usage_log.record("vitals_context", "gemini-3.8-flash", {"promptTokenCount": 8200, "candidatesTokenCount": 40, "totalTokenCount": 8240})
    (row,) = lines(log)
    assert (row["input"], row["output"], row["thoughts"], row["cached"]) == (8200, 40, 0, 0)


def test_no_usage_records_nothing(log):
    usage_log.record("chat", "m1", None)
    assert not log.exists()


def test_an_unwritable_log_never_raises(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created under it")
    monkeypatch.setenv("LLM_USAGE_LOG", str(blocker / "usage.jsonl"))
    usage_log.record("chat", "m1", sdk_usage(prompt_token_count=5))
    assert "could not record usage" in capsys.readouterr().out


# ---------------------------------------------------------------- llm.generate
def fake_client(results):
    """A stand-in for google.genai.Client: each result is (text, usage) or an exception, one per call."""
    class Models:
        def generate_content(self, model, contents, config):
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return types.SimpleNamespace(text=item[0], usage_metadata=item[1])

    class Client:
        def __init__(self, *a, **k):
            self.models = Models()

    return Client


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "load_dotenv", lambda **k: None)


def test_generate_logs_every_billed_answer_even_an_empty_one(log, gemini, monkeypatch):
    """A model that spends its budget thinking returns no text but is still billed; the next model then answers."""
    monkeypatch.setattr(llm.genai, "Client", fake_client([
        ("", sdk_usage(prompt_token_count=100, thoughts_token_count=900)),
        ("hello", sdk_usage(prompt_token_count=100, candidates_token_count=5)),
    ]))
    assert llm.generate("hi", ["m1", "m2"], purpose="chat") == "hello"
    rows = lines(log)
    assert [(r["model"], r["purpose"], r["thoughts"], r["output"]) for r in rows] == [("m1", "chat", 900, 0), ("m2", "chat", 0, 5)]


def test_a_failed_call_records_nothing(log, gemini, monkeypatch):
    monkeypatch.setattr(llm.genai, "Client", fake_client([RuntimeError("500 boom")]))
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("hi", ["m1"])
    assert not log.exists()


# ---------------------------------------------------------------- vitals check
@pytest.fixture
def vitals_context(monkeypatch):
    """The vitals module imports opencv and numpy, which the tests do not install; stand-ins are enough for _ask()."""
    for name in ("cv2", "numpy"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules.pop("vitals.context", None)
    yield importlib.import_module("vitals.context")
    sys.modules.pop("vitals.context", None)


def test_the_vitals_situation_check_records_its_tokens(log, vitals_context, monkeypatch):
    body = {"candidates": [{"content": {"parts": [{"text": '{"posture": "sitting"}'}]}}],
            "usageMetadata": {"promptTokenCount": 2100, "candidatesTokenCount": 30, "thoughtsTokenCount": 60}}
    monkeypatch.setattr(vitals_context.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(body).encode()))
    assert vitals_context._ask("gemini-3.8-flash", "k", ["frame"]) == {"posture": "sitting"}
    (row,) = lines(log)
    assert (row["purpose"], row["model"], row["input"], row["thoughts"]) == ("vitals_context", "gemini-3.8-flash", 2100, 60)


# ---------------------------------------------------------------- report
def test_the_report_bills_thinking_as_output(log):
    for _ in range(2):
        usage_log.record("careplan", "gemini-3.1-flash-lite",
                         sdk_usage(prompt_token_count=500_000, candidates_token_count=100_000, thoughts_token_count=150_000))
    usage_log.record("vitals_context", "gemini-pro-latest", {"promptTokenCount": 10, "candidatesTokenCount": 5})
    by_model = usage_report.summarise(usage_report.load(str(log)), "model")
    flash = by_model["gemini-3.1-flash-lite"]
    assert (flash["calls"], flash["in"], flash["out"], flash["thinking"]) == (2, 1_000_000, 500_000, 300_000)
    assert flash["usd"] == pytest.approx(1.00)      # 1M input at $0.25 + 500K output (thinking included) at $1.50
    assert by_model["gemini-pro-latest"]["priced"] is False


def test_the_report_before_any_call_says_so_instead_of_crashing(log, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["usage_report.py", "--file", str(log)])
    usage_report.main()
    assert "no calls recorded yet" in capsys.readouterr().out


def test_the_report_groups_by_ist_day_and_skips_a_half_written_line(log):
    log.write_text(
        json.dumps({"ts": "2026-11-03T20:00:00Z", "purpose": "chat", "model": "gemini-3.1-flash-lite",
                    "input": 1, "output": 1, "thoughts": 0, "cached": 0, "total": 2, "seconds": 1.0}) + "\n"
        + '{"ts": "2026-11-', encoding="utf-8")
    rows = usage_report.load(str(log))
    assert [r["day"] for r in rows] == ["2026-11-04"]       # 20:00 UTC is already the next day in IST
