import pytest
import re
from app.api_routes import _is_whisper_noise


def test_whisper_noise_detection():
    # Empty or whitespace
    assert _is_whisper_noise("", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("   ", is_audio_input=True, hist_len=0) is True

    # Pure punctuation
    assert _is_whisper_noise(".", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("...", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("..", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("!", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise(" - ", is_audio_input=True, hist_len=0) is True

    # Common Whisper subtitle hallucinations on ambient mic noise
    assert _is_whisper_noise("you", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("you.", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("Thank you.", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("thank you", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("Thank you for watching.", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("bye", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("subtitles by", is_audio_input=True, hist_len=0) is True
    assert _is_whisper_noise("amara.org", is_audio_input=True, hist_len=0) is True

    # Legitimate user messages must NOT be flagged as noise
    assert _is_whisper_noise("I have an issue with SAP", is_audio_input=True, hist_len=0) is False
    assert _is_whisper_noise("Please log a ticket for my printer", is_audio_input=True, hist_len=0) is False
    assert _is_whisper_noise("My employee ID is EMP-1029", is_audio_input=True, hist_len=0) is False
    assert _is_whisper_noise("No that is all", is_audio_input=True, hist_len=2) is False

    # Text mode inputs (typed by user) should not be blocked if they say thank you
    assert _is_whisper_noise("thank you", is_audio_input=False, hist_len=2) is False
