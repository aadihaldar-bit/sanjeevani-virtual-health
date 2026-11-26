"""The Gemini Live call timeline: what Gemini sent, what we sent it, when the caller spoke."""
import asyncio
import json
import types

from app.gemini_diag import CallDiagnostics, TIMELINE_CAP, describe
from app.gemini_live import GeminiLiveSession, LiveEvent
from app.gemini_live_processor import GeminiLiveProcessor


def test_describe_names_the_parts_without_their_content():
    msg = {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": "AAAA"}}]}, "generationComplete": True},
           "usageMetadata": {"totalTokenCount": 9}}
    line = describe(msg)
    assert "modelTurn(audio)" in line and "generationComplete=true" in line and "usageMetadata" in line
    assert "AAAA" not in line
    assert describe({"serverContent": {"inputTranscription": {"text": "my date of birth"}}}) == \
        "serverContent[inputTranscription(16 chars)]"
    assert "my date" not in describe({"serverContent": {"inputTranscription": {"text": "my date of birth"}}})
    assert describe({"goAway": {"timeLeft": "30s"}}) == "goAway{timeLeft}"
    assert describe({}) == "empty"


def test_speech_segments_are_timed_and_measured():
    d = CallDiagnostics(t0=100.0)
    for i in range(30):                              # 3 s of speech
        d.caller_frame(2000 + i, 110.0 + i * 0.1)
    d.tick(114.0)                                    # 1.1 s after the last frame: the segment is over
    assert d.speech_segments == 1
    assert d.timeline[-1][1] == "caller_speech"
    assert d.timeline[-1][0] == 10.0                 # started 10 s into the call
    assert "2.9s" in d.timeline[-1][2] and "peak=2029" in d.timeline[-1][2]


def test_a_short_pause_does_not_split_a_segment_but_a_long_one_does():
    d = CallDiagnostics(t0=0.0)
    d.caller_frame(1000, 1.0)
    d.caller_frame(1000, 1.5)                        # 0.5 s pause: same segment
    d.caller_frame(1000, 3.0)                        # 1.5 s pause: new segment
    d.tick(10.0)
    assert d.speech_segments == 2


def test_line_noise_is_not_speech():
    d = CallDiagnostics(t0=0.0)
    for i in range(100):
        d.caller_frame(4, i * 0.1)
    d.tick(60.0)
    assert d.speech_segments == 0 and d.peak == 4


def test_send_failures_are_counted_and_only_the_first_and_every_hundredth_are_reported():
    d = CallDiagnostics(t0=0.0)
    reported = [d.send_failed(1.0, RuntimeError("closed")) for _ in range(201)]
    assert reported[0] and reported[99] and reported[199]
    assert sum(reported) == 3
    assert d.send_failures == 201 and "closed" in d.last_send_error


def test_timeline_is_capped_but_keeps_the_start_of_the_call():
    d = CallDiagnostics(t0=0.0)
    for i in range(TIMELINE_CAP * 3):
        d.note(float(i), "x", str(i))
    assert len(d.timeline) == TIMELINE_CAP
    assert d.timeline[0][2] == "0"                   # the first entries survive
    assert d.timeline[-1][2] == str(TIMELINE_CAP * 3 - 1)


def test_heartbeat_and_snapshot_say_what_happened():
    d = CallDiagnostics(t0=0.0)
    d.sent(3000)
    d.caller_frame(2500, 1.0)
    d.event(2.0, "output_transcript", "serverContent[outputTranscription(5 chars)]")
    d.event(2.1, "audio", log=False)
    line = d.heartbeat(5.0)
    assert "sent=1 frames" in line and "caller_peak=2500" in line and "last_gemini_event=2.9s ago" in line
    snap = d.snapshot(6.0)
    assert snap["gemini_events"] == {"output_transcript": 1, "audio": 1}
    assert snap["speech_segments"] == 1 and snap["sent_bytes"] == 3000
    json.dumps(snap)                                 # it is stored on the call, so it must serialise


class FakeWS:
    def __init__(self, msgs):
        self.msgs = [json.dumps(m) for m in msgs]

    def __aiter__(self):
        async def gen():
            for m in self.msgs:
                yield m
        return gen()


def _events(msgs):
    async def run():
        s = GeminiLiveSession(key="k")
        s._ws = FakeWS(msgs)
        return [e async for e in s.events()]
    return asyncio.run(run())


def test_messages_the_client_does_not_know_are_surfaced_not_dropped():
    evs = _events([
        {"goAway": {"timeLeft": "30s"}},
        {"usageMetadata": {"totalTokenCount": 5}},
        {"serverContent": {"generationComplete": True}},
        {"serverContent": {"turnComplete": True}},
        {"serverContent": {"inputTranscription": {"text": "hello"}}},
    ])
    assert [e.kind for e in evs] == ["other", "other", "turn_complete", "turn_complete", "input_transcript"]
    assert "goAway" in describe(evs[0].raw)
    assert describe(evs[2].raw) != describe(evs[3].raw)        # generationComplete and turnComplete can now be told apart


class FakeSession:
    def __init__(self, events=(), fail_send=False):
        self._events = list(events)
        self.fail_send = fail_send
        self.sent = []

    async def events(self):
        for e in self._events:
            yield e

    async def send_audio(self, pcm):
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.sent.append(pcm)


def test_receive_loop_handles_every_kind_of_event():
    """Regression: a stray `import time` inside the loop once made `time` local to it and killed the loop on the first event."""
    p = GeminiLiveProcessor(system_instruction="x")
    p._session = FakeSession([
        LiveEvent(kind="output_transcript", text="Hello", raw={"serverContent": {"outputTranscription": {"text": "Hello"}}}),
        LiveEvent(kind="turn_complete", raw={"serverContent": {"generationComplete": True}}),
        LiveEvent(kind="input_transcript", text="my date", raw={"serverContent": {"inputTranscription": {"text": "my date"}}}),
        LiveEvent(kind="other", raw={"goAway": {"timeLeft": "30s"}}),
        LiveEvent(kind="other", raw={"usageMetadata": {"totalTokenCount": 1}}),
        LiveEvent(kind="turn_complete", raw={"serverContent": {"turnComplete": True}}),
    ])
    asyncio.run(p._receive_loop())
    assert p._diag.events == {"output_transcript": 1, "turn_complete": 2, "input_transcript": 1, "other": 2}
    tags = [t[1] for t in p._diag.timeline]
    assert "other" in tags and tags.count("other") == 1       # the usage-only message is counted but not written to the timeline
    assert p._watchdog.last_activity_at is not None            # the model's transcript counted as an answer


def test_caller_audio_is_sent_measured_and_failures_are_counted():
    p = GeminiLiveProcessor(system_instruction="x")
    p._session = FakeSession()
    loud = (b"\xd0\x07" * 1500)                                # 16-bit samples of 2000
    frame = types.SimpleNamespace(audio=loud, sample_rate=16000)
    asyncio.run(p._send_caller_audio(frame))
    assert p._session.sent == [loud] and p._diag.sent_frames == 1 and p._diag.peak == 2000
    p._session.fail_send = True
    asyncio.run(p._send_caller_audio(frame))
    assert p._diag.send_failures == 1 and p._diag.sent_frames == 1


def test_diagnostics_are_saved_only_for_a_call_that_has_a_row():
    p = GeminiLiveProcessor(system_instruction="x")            # no call id yet: nothing to save, and no crash
    asyncio.run(p._record_diagnostics())


def test_debug_audio_is_off_unless_asked_for(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_LIVE_DEBUG_AUDIO", raising=False)
    p = GeminiLiveProcessor(system_instruction="x")
    p._save_debug_audio(b"\x00\x00" * 100)
    assert p._debug_wav is None and p._debug_path == ""


def test_speech_gemini_never_reacted_to_is_counted():
    d = CallDiagnostics(t0=100.0)
    for i in range(20):                                    # 2 s of speech from 110 s: Gemini signals it heard
        d.caller_frame(2000, 110.0 + i * 0.1)
    d.event(110.3, "other", "voiceActivity{audioOffset,type}")
    for i in range(20):                                    # 2 s of speech from 130 s: no sign at all
        d.caller_frame(2000, 130.0 + i * 0.1)
    for i in range(3):                                     # a click is not a sentence
        d.caller_frame(2000, 150.0 + i * 0.1)
    d.tick(160.0)
    assert d.unheard(160.0) == [30.0]
    assert d.snapshot(160.0)["speech_not_heard"] == [30.0]
    assert "not_heard=1" in d.heartbeat(160.0)


def test_a_transcript_counts_as_hearing_and_a_stretch_just_ended_is_not_judged_yet():
    d = CallDiagnostics(t0=0.0)
    for i in range(20):
        d.caller_frame(2000, 10.0 + i * 0.1)
    d.tick(13.0)                                           # ended 1.1 s ago: Gemini may still answer
    assert d.unheard(13.0) == []
    d.tick(20.0)
    assert d.unheard(20.0) == [10.0]
    d.event(14.0, "input_transcript", "serverContent[inputTranscription(5 chars)]")
    assert d.unheard(20.0) == []


def test_the_agents_words_are_counted_but_do_not_crowd_out_the_timeline():
    d = CallDiagnostics(t0=0.0)
    for i in range(400):                                   # a long call: hundreds of pieces of the agent's speech
        d.event(float(i), "output_transcript", "serverContent[outputTranscription(12 chars)]")
    d.event(401.0, "tool_call", "toolCall{functionCalls}")
    d.note(402.0, "handoff", "Front desk -> Appointment Desk")
    assert d.events["output_transcript"] == 400
    assert [e[1] for e in d.timeline] == ["tool_call", "handoff"]
