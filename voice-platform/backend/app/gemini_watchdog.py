"""A Gemini Live call can go quiet: the caller speaks, clearly and for seconds, and the model never answers (no transcript of
the caller, no reply). The caller waits in silence and hangs up. This watches for exactly that and says when to nudge the
model to ask the caller to repeat.

It knows nothing about sockets or time: it is told when caller audio arrives, when the model does anything, and what time
it is, and it answers whether to nudge now. That keeps it testable with a fake clock.
"""
from typing import Optional

NUDGE_TEXT = ("NOTICE: the caller just spoke but you did not respond, so you may not have heard them. In one short, friendly "
              "sentence, say you did not catch that and ask them to repeat it, then wait for their answer.")


class ResponseWatchdog:
    def __init__(self, *, wait_s: float = 7.0, voice_rms: int = 300, min_voice_s: float = 0.6, quiet_s: float = 1.2, max_nudges: int = 2):
        self.wait_s = wait_s                # how long the caller is left unanswered before the first nudge (the second comes at twice this)
        self.voice_rms = voice_rms          # audio level (16-bit samples) that counts as speech; a quiet line sits near 2 to 5
        self.min_voice_s = min_voice_s      # less speech than this (a cough, a click) is not worth answering
        self.quiet_s = quiet_s              # the caller counts as finished after this much quiet
        self.max_nudges = max_nudges        # per unanswered utterance
        self.nudges_total = 0
        self.last_activity_at: Optional[float] = None
        self._reset()

    def _reset(self) -> None:
        self.voice_s = 0.0
        self.last_voice_at: Optional[float] = None
        self.nudges = 0

    def caller_audio(self, rms: int, now: float, frame_s: float) -> None:
        if rms >= self.voice_rms:
            self.voice_s += frame_s
            self.last_voice_at = now

    def model_activity(self, now: float) -> None:
        """The model spoke, sent a transcript or called a tool: whatever the caller said so far has been answered."""
        self.last_activity_at = now
        self._reset()

    def check(self, now: float) -> bool:
        """True when the model should be nudged right now."""
        if self.last_voice_at is None or self.voice_s < self.min_voice_s:
            return False
        if now - self.last_voice_at < self.quiet_s:
            return False                    # still talking
        if self.last_activity_at is not None and self.last_activity_at >= self.last_voice_at:
            return False                    # answered
        if self.nudges < self.max_nudges and now - self.last_voice_at >= self.wait_s * (self.nudges + 1):
            self.nudges += 1
            self.nudges_total += 1
            return True
        return False
