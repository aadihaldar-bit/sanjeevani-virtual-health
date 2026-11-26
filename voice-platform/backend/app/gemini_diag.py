"""What happened inside a Gemini Live call, kept as a short timeline so a call that goes wrong can be explained afterwards.

Logs in the container are lost on every recreate and say nothing about messages we dropped. This keeps, per call:
 - what Gemini sent us, message by message (kinds and flags, never audio or the caller's words),
 - what we sent it (frames, bytes, failures),
 - each stretch of speech from the caller (when, how long, how loud),
and is saved on the call (metadata_json["gemini_diag"]) when it ends.

It knows nothing about sockets or time: callers pass `now` (seconds, any monotonic clock).
"""
from typing import Any, Dict, List, Optional

VOICE_RMS = 300         # audio level (16-bit samples) that counts as speech; a quiet line sits near 2 to 5
SPEECH_GAP_S = 0.8      # this much quiet ends a stretch of caller speech
TIMELINE_CAP = 300      # entries kept; the first HEAD are never dropped, so the start of the call survives a long call
HEAD = 60


def describe(msg: Optional[Dict[str, Any]]) -> str:
    """One short line naming what a Gemini server message contains, without any audio or text in it."""
    if not isinstance(msg, dict) or not msg:
        return "empty"
    parts: List[str] = []
    for key, value in msg.items():
        if key == "serverContent" and isinstance(value, dict):
            flags = []
            for k, v in value.items():
                if k == "modelTurn":
                    kinds = sorted({("audio" if "inlineData" in p else "text" if "text" in p else "other")
                                    for p in (v or {}).get("parts", []) or []})
                    flags.append("modelTurn(" + ",".join(kinds) + ")")
                elif k in ("inputTranscription", "outputTranscription"):
                    flags.append(f"{k}({len((v or {}).get('text') or '')} chars)")
                elif v is True or v is False:
                    flags.append(f"{k}={str(v).lower()}")
                else:
                    flags.append(k)
            parts.append("serverContent[" + ",".join(flags) + "]")
        elif key == "usageMetadata":
            parts.append("usageMetadata")
        elif isinstance(value, dict):
            parts.append(f"{key}{{{','.join(sorted(value))[:60]}}}")
        else:
            parts.append(key)
    return " ".join(parts)


class CallDiagnostics:
    def __init__(self, t0: float):
        self.t0 = t0
        self.timeline: List[list] = []
        self.events: Dict[str, int] = {}              # what Gemini sent, by kind
        self.sent_frames = 0
        self.sent_bytes = 0
        self.send_failures = 0
        self.last_send_error = ""
        self.last_event_at: Optional[float] = None
        self.peak = 0                                 # loudest caller frame so far
        self._window_peak = 0                         # loudest since the last heartbeat
        self._seg_start: Optional[float] = None
        self._seg_last: Optional[float] = None
        self._seg_peak = 0
        self.speech_segments = 0
        self.speech_seconds = 0.0
        self._segments: List[tuple] = []              # (start, end) of each stretch of caller speech
        self._signals: List[float] = []               # when Gemini showed it heard someone: a voice-activity signal or a transcript

    # -- timeline
    def note(self, now: float, tag: str, detail: str = "") -> None:
        self.timeline.append([round(now - self.t0, 2), tag, detail])
        if len(self.timeline) > TIMELINE_CAP:
            del self.timeline[HEAD]                   # drop the oldest entry after the head

    # -- what we send
    def sent(self, nbytes: int) -> None:
        self.sent_frames += 1
        self.sent_bytes += nbytes

    def send_failed(self, now: float, exc: BaseException) -> bool:
        """Count a failed send. True for the first failure and every 100th, which are the ones worth logging."""
        self.send_failures += 1
        self.last_send_error = f"{type(exc).__name__}: {exc}"[:120]
        if self.send_failures == 1 or self.send_failures % 100 == 0:
            self.note(now, "send_failed", f"#{self.send_failures} {self.last_send_error}")
            return True
        return False

    # -- what the caller does
    def caller_frame(self, rms: int, now: float) -> None:
        self.peak = max(self.peak, rms)
        self._window_peak = max(self._window_peak, rms)
        if rms < VOICE_RMS:
            return
        if self._seg_start is not None and self._seg_last is not None and now - self._seg_last >= SPEECH_GAP_S:
            self._close_segment()
        if self._seg_start is None:
            self._seg_start = now
            self._seg_peak = 0
        self._seg_last = now
        self._seg_peak = max(self._seg_peak, rms)

    def tick(self, now: float) -> None:
        """Close a stretch of speech once the caller has been quiet long enough."""
        if self._seg_last is not None and now - self._seg_last >= SPEECH_GAP_S:
            self._close_segment()

    def _close_segment(self) -> None:
        if self._seg_start is None or self._seg_last is None:
            return
        length = self._seg_last - self._seg_start
        self.speech_segments += 1
        self.speech_seconds += length
        self.note(self._seg_start, "caller_speech", f"{length:.1f}s peak={self._seg_peak}")
        self._segments.append((self._seg_start, self._seg_last))
        self._seg_start = self._seg_last = None

    def unheard(self, now: float, min_seconds: float = 0.5, grace: float = 3.0) -> List[float]:
        """Seconds into the call at which the caller spoke (a real sentence, not a click) and Gemini showed no sign of hearing it.
        A stretch is only judged `grace` seconds after it ended, since Gemini answers a moment after the speaker stops."""
        out = []
        for start, end in self._segments:
            if end - start < min_seconds or now - end < grace:
                continue
            if not any(start - 0.3 <= s <= end + grace for s in self._signals):
                out.append(round(start - self.t0, 1))
        return out

    def window_peak(self) -> int:
        """Loudest caller frame since the last call to this, for the heartbeat."""
        peak, self._window_peak = self._window_peak, 0
        return peak

    # -- what Gemini does
    def event(self, now: float, kind: str, detail: str = "", log: bool = True) -> None:
        self.events[kind] = self.events.get(kind, 0) + 1
        self.last_event_at = now
        if kind == "input_transcript" or (kind == "other" and detail.startswith("voiceActivity")):
            self._signals.append(now)
        if log and kind not in ("audio", "output_transcript"):      # the agent's words arrive in a dozen pieces a turn: counted, not listed
            self.note(now, kind, detail)

    def heartbeat(self, now: float, voice_s: float = 0.0) -> str:
        quiet = "none yet" if self.last_event_at is None else f"{now - self.last_event_at:.1f}s ago"
        return (f"t=+{now - self.t0:.0f}s sent={self.sent_frames} frames/{self.sent_bytes // 1024} KiB failed={self.send_failures} "
                f"caller_peak={self.window_peak()} speech={self.speech_seconds:.1f}s/{self.speech_segments} segments not_heard={len(self.unheard(now))} "
                f"gemini={dict(sorted(self.events.items()))} last_gemini_event={quiet}")

    def snapshot(self, now: float) -> Dict[str, Any]:
        self.tick(now + SPEECH_GAP_S)
        return {"seconds": round(now - self.t0, 1), "sent_frames": self.sent_frames, "sent_bytes": self.sent_bytes,
                "send_failures": self.send_failures, "last_send_error": self.last_send_error,
                "caller_peak": self.peak, "speech_segments": self.speech_segments,
                "speech_seconds": round(self.speech_seconds, 1), "speech_not_heard": self.unheard(now + 100),
                "gemini_events": dict(self.events),
                "timeline": self.timeline}
