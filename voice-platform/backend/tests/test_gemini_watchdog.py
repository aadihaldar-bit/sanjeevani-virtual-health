"""The watchdog nudges a Gemini Live model that has gone quiet after the caller spoke, and only then."""
import asyncio

from app.gemini_live_processor import GeminiLiveProcessor
from app.gemini_watchdog import NUDGE_TEXT, ResponseWatchdog

FRAME = 0.095     # one input frame at 16 kHz is about 95 ms
LOUD = 2000
QUIET = 4


def speak(wd, start, seconds, rms=LOUD):
    """Feed loud caller audio from `start` for `seconds`; returns the time of the last frame."""
    t = start
    while t < start + seconds:
        wd.caller_audio(rms, t, FRAME)
        t += FRAME
    return t - FRAME


def test_no_nudge_for_a_line_that_is_only_noise():
    wd = ResponseWatchdog()
    speak(wd, 0, 20, rms=QUIET)
    assert not wd.check(30)


def test_a_cough_is_not_worth_answering():
    wd = ResponseWatchdog()
    last = speak(wd, 0, 0.3)
    assert not wd.check(last + 30)


def test_no_nudge_while_the_caller_is_still_talking():
    wd = ResponseWatchdog()
    last = speak(wd, 0, 5)
    assert not wd.check(last + 0.5)


def test_no_nudge_when_the_model_answered():
    wd = ResponseWatchdog()
    last = speak(wd, 0, 3)
    wd.model_activity(last + 1.5)
    assert not wd.check(last + 30)


def test_nudges_at_the_wait_then_again_then_stops():
    wd = ResponseWatchdog(wait_s=7)
    last = speak(wd, 0, 3)
    assert not wd.check(last + 6.9)
    assert wd.check(last + 7.05)
    assert not wd.check(last + 7.5)                 # not twice for the same wait
    assert not wd.check(last + 13.9)
    assert wd.check(last + 14.05)                  # the second comes at twice the wait
    assert not wd.check(last + 60)                  # and that is all for this utterance
    assert wd.nudges_total == 2


def test_model_activity_resets_so_the_next_utterance_can_be_nudged():
    wd = ResponseWatchdog(wait_s=7)
    last = speak(wd, 0, 3)
    assert wd.check(last + 7)
    wd.model_activity(last + 8)                     # the nudge worked; the model spoke
    last2 = speak(wd, 40, 3)
    assert wd.check(last2 + 7)
    assert wd.nudges_total == 2


def test_caller_speaking_again_after_a_nudge_restarts_the_wait():
    wd = ResponseWatchdog(wait_s=7)
    last = speak(wd, 0, 3)
    assert wd.check(last + 7)
    last2 = speak(wd, last + 8, 3)                  # still no answer, caller tries again
    assert not wd.check(last2 + 6)
    assert wd.check(last2 + 14)


class FakeSession:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_text(self, text, end_turn=True):
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append((text, end_turn))


def _processor():
    return GeminiLiveProcessor(system_instruction="x")


def test_processor_tick_sends_the_nudge():
    p = _processor()
    p._session = FakeSession()
    last = speak(p._watchdog, 0, 3)
    assert p._watchdog.wait_s == 4                           # the caller is not left waiting long for a reply
    assert not asyncio.run(p._watchdog_tick(last + 3))
    assert asyncio.run(p._watchdog_tick(last + 4.05))
    assert p._session.sent == [(NUDGE_TEXT, True)]


def test_processor_tick_survives_a_closed_session():
    p = _processor()
    p._session = FakeSession(fail=True)
    last = speak(p._watchdog, 0, 3)
    assert asyncio.run(p._watchdog_tick(last + 7)) is False


def test_processor_without_a_session_does_nothing():
    p = _processor()
    last = speak(p._watchdog, 0, 3)
    assert asyncio.run(p._watchdog_tick(last + 7)) is False


def test_can_be_switched_off_and_waits_longer_for_the_extended_model(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_WATCHDOG_SECS", "0")
    assert GeminiLiveProcessor(system_instruction="x")._watchdog is None
    monkeypatch.delenv("GEMINI_LIVE_WATCHDOG_SECS", raising=False)
    assert GeminiLiveProcessor(system_instruction="x")._watchdog.wait_s == 4
    monkeypatch.setenv("GEMINI_LIVE_WATCHDOG_SECS", "7")
    assert GeminiLiveProcessor(system_instruction="x")._watchdog.wait_s == 7
    ext = GeminiLiveProcessor(system_instruction="x", model="models/gemini-3.8-live-extended-thinking")
    assert ext._watchdog.wait_s == 12
