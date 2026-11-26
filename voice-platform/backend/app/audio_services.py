"""
Audio Services Segment
Handles audio transcoding (mu-law <-> PCM16) and Voice Activity Detection (VAD).
Adheres to Single Responsibility Principle (SRP).
"""

import base64
import struct
import logging
from typing import List
from .interfaces import BaseAudioTranscoder, BaseVADDetector
from .config import VADSettings, get_config

logger = logging.getLogger(__name__)

# ─── Lookup Table for mu-law to PCM16 ──────────────────────────────────────────
_ULAW_TO_PCM16: List[int] = [
    -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
    -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
    -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
    -11900, -11388, -10876, -10364,  -9852,  -9340,  -8828,  -8316,
     -7932,  -7676,  -7420,  -7164,  -6908,  -6652,  -6396,  -6140,
     -5884,  -5628,  -5372,  -5116,  -4860,  -4604,  -4348,  -4092,
     -3900,  -3772,  -3644,  -3516,  -3388,  -3260,  -3132,  -3004,
     -2876,  -2748,  -2620,  -2492,  -2364,  -2236,  -2108,  -1980,
     -1884,  -1820,  -1756,  -1692,  -1628,  -1564,  -1500,  -1436,
     -1372,  -1308,  -1244,  -1180,  -1116,  -1052,   -988,   -924,
      -876,   -844,   -812,   -780,   -748,   -716,   -684,   -652,
      -620,   -588,   -556,   -524,   -492,   -460,   -428,   -396,
      -372,   -356,   -340,   -324,   -308,   -292,   -276,   -260,
      -244,   -228,   -212,   -196,   -180,   -164,   -148,   -132,
      -120,   -112,   -104,    -96,    -88,    -80,    -72,    -64,
       -56,    -48,    -40,    -32,    -24,    -16,     -8,      0,
     32124,  31100,  30076,  29052,  28028,  27004,  25980,  24956,
     23932,  22908,  21884,  20860,  19836,  18812,  17788,  16764,
     15996,  15484,  14972,  14460,  13948,  13436,  12924,  12412,
     11900,  11388,  10876,  10364,   9852,   9340,   8828,   8316,
      7932,   7676,   7420,   7164,   6908,   6652,   6396,   6140,
      5884,   5628,   5372,   5116,   4860,   4604,   4348,   4092,
      3900,   3772,   3644,   3516,   3388,   3260,   3132,   3004,
      2876,   2748,   2620,   2492,   2364,   2236,   2108,   1980,
      1884,   1820,   1756,   1692,   1628,   1564,   1500,   1436,
      1372,   1308,   1244,   1180,   1116,   1052,    988,    924,
       876,    844,    812,    780,    748,    716,    684,    652,
       620,    588,    556,    524,    492,    460,    428,    396,
       372,    356,    340,    324,    308,    292,    276,    260,
       244,    228,    212,    196,    180,    164,    148,    132,
       120,    112,    104,     96,     88,     80,     72,     64,
        56,     48,     40,     32,     24,     16,      8,      0,
]


try:
    import audioop
except ImportError:
    import audioop_lts as audioop

class AudioTranscoder(BaseAudioTranscoder):
    """Encapsulates PCM16 <-> mu-law conversion and WAV header construction."""

    @staticmethod
    def _linear_to_ulaw(sample: int) -> int:
        BIAS = 0x84
        CLIP = 32635
        sign = 0x80 if sample < 0 else 0
        sample = min(abs(sample), CLIP) + BIAS
        exponent = 7
        mask = 0x4000
        for _ in range(8):
            if sample & mask:
                break
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        return ulaw_byte

    def ulaw_to_pcm16_bytes(self, ulaw_bytes: bytes) -> bytes:
        """Convert 8kHz mu-law bytes to upsampled 16kHz PCM16 bytes for STT."""
        if not ulaw_bytes:
            return b""
        try:
            pcm_8k = audioop.ulaw2lin(ulaw_bytes, 2)
            resampled_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
            return resampled_16k
        except Exception:
            pcm_samples = [_ULAW_TO_PCM16[b] for b in ulaw_bytes]
            upsampled = []
            for i in range(len(pcm_samples)):
                upsampled.append(pcm_samples[i])
                if i + 1 < len(pcm_samples):
                    upsampled.append((pcm_samples[i] + pcm_samples[i + 1]) >> 1)
                else:
                    upsampled.append(pcm_samples[i])
            return struct.pack(f"<{len(upsampled)}h", *upsampled)

    def pcm16_to_ulaw_b64(self, pcm_bytes: bytes, input_sample_rate: int = 8000) -> str:
        """
        High-fidelity resampler: Converts PCM16 audio at any sample rate (e.g. 48kHz, 24kHz, 16kHz)
        to 8kHz G.711 mu-law for Twilio Telephony Media Streams, and base64-encodes it.
        """
        if not pcm_bytes:
            return ""

        actual_sample_rate = input_sample_rate
        if pcm_bytes.startswith(b'RIFF') and len(pcm_bytes) > 44:
            try:
                header_sr = struct.unpack('<I', pcm_bytes[24:28])[0]
                if 8000 <= header_sr <= 96000:
                    actual_sample_rate = header_sr
                data_pos = pcm_bytes.find(b'data')
                if data_pos != -1:
                    pcm_bytes = pcm_bytes[data_pos + 8:]
                else:
                    pcm_bytes = pcm_bytes[44:]
            except Exception as e:
                logger.warning(f"[AudioTranscoder] Could not parse WAV header: {e}")

        if not pcm_bytes:
            return ""

        try:
            if actual_sample_rate != 8000:
                pcm_8k, _ = audioop.ratecv(pcm_bytes, 2, 1, actual_sample_rate, 8000, None)
            else:
                pcm_8k = pcm_bytes

            ulaw_bytes = audioop.lin2ulaw(pcm_8k, 2)
            return base64.b64encode(ulaw_bytes).decode("ascii")
        except Exception as err:
            logger.error(f"[AudioTranscoder] Resampling error: {err}")
            return ""

    def create_wav_bytes(self, pcm16_bytes: bytes, sample_rate: int = 16000) -> bytes:
        """Wrap raw PCM16 bytes into a valid RIFF/WAV container."""
        byte_rate = sample_rate * 2  # 16-bit mono
        header = (
            b'RIFF' +
            struct.pack('<I', len(pcm16_bytes) + 36) +
            b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, byte_rate, 2, 16) +
            b'data' +
            struct.pack('<I', len(pcm16_bytes))
        )
        return header + pcm16_bytes


class EnergyVADDetector(BaseVADDetector):
    """
    Energy-based Voice Activity and Barge-In Detector.
    Configurable via VADSettings.
    """

    def __init__(self, settings: VADSettings = None):
        self.settings = settings or get_config().vad

    def calculate_rms_energy(self, ulaw_bytes: bytes) -> float:
        """Calculate the average absolute sample amplitude for a mu-law frame."""
        if not ulaw_bytes:
            return 0.0
        return sum(abs(_ULAW_TO_PCM16[b]) for b in ulaw_bytes) / len(ulaw_bytes)

    def is_speech(self, ulaw_bytes: bytes) -> bool:
        """Check if frame energy exceeds threshold."""
        return self.calculate_rms_energy(ulaw_bytes) > self.settings.energy_threshold

    def should_barge_in(self, speech_frames: int, is_agent_speaking: bool) -> bool:
        """Barge in when caller speaks for N consecutive frames while agent is talking."""
        return is_agent_speaking and speech_frames > self.settings.speech_frames_for_barge_in

    def is_endpoint(self, silence_frames: int, buffer_has_audio: bool) -> bool:
        """Endpoint turn when silence exceeds threshold after speech was detected."""
        return buffer_has_audio and silence_frames > self.settings.silence_frames_for_endpointing


class SileroVADDetector(BaseVADDetector):
    """
    Neural Voice Activity Detector using the Silero VAD ONNX model.

    Silero VAD processes 512 PCM16 samples (32ms @ 16kHz) per inference call
    and outputs a speech probability in [0, 1]. This is far more accurate than
    energy thresholding on noisy telephony lines and reduces false barge-ins
    and missed endpoints. Saves ~200-400ms by enabling tighter silence windows.

    Automatically falls back to EnergyVADDetector if onnxruntime is not
    installed or the model file is missing.
    """

    # Silero VAD ONNX requires exactly 512 samples (32ms @ 16kHz)
    _SILERO_FRAME_SAMPLES = 512
    _SILERO_SAMPLE_RATE = 16000
    _SPEECH_THRESHOLD = 0.45      # probability above which frame is speech
    _SILENCE_THRESHOLD = 0.25     # probability below which frame is silence

    def __init__(self, model_path: str = "silero_vad.onnx", settings: VADSettings = None):
        self.settings = settings or get_config().vad
        self._session = None
        self._fallback = EnergyVADDetector(settings=self.settings)
        self._h = None   # Silero LSTM hidden state
        self._c = None   # Silero LSTM cell state
        self._pcm_accumulator = bytearray()

        try:
            import onnxruntime as ort
            import numpy as np
            self._np = np

            import os
            candidate_paths = [
                model_path,
                os.path.join(os.path.dirname(__file__), "..", model_path),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", model_path),
            ]
            resolved = None
            for p in candidate_paths:
                if os.path.isfile(p):
                    resolved = os.path.abspath(p)
                    break

            if resolved is None:
                raise FileNotFoundError(f"silero_vad.onnx not found in: {candidate_paths}")

            opts = ort.SessionOptions()
            opts.log_severity_level = 3  # ERROR only
            self._session = ort.InferenceSession(resolved, sess_options=opts)
            self._reset_state()
            logger.info(f"[SileroVAD] Loaded neural VAD model: {resolved}")

        except ImportError:
            logger.warning("[SileroVAD] onnxruntime not installed — using EnergyVAD fallback. "
                           "Install with: pip install onnxruntime")
        except FileNotFoundError as e:
            logger.warning(f"[SileroVAD] {e} — using EnergyVAD fallback.")
        except Exception as e:
            logger.warning(f"[SileroVAD] Failed to load ({e}) — using EnergyVAD fallback.")

    def _reset_state(self):
        """Reset Silero v5 LSTM state tensor to zero for new session."""
        np = self._np
        # Silero v5 uses unified state tensor shape (2, 1, 128)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def _infer_frame(self, pcm16_samples: bytes) -> float:
        """Run Silero ONNX on exactly 512 PCM16 samples. Returns speech probability [0,1]."""
        np = self._np
        samples = np.frombuffer(pcm16_samples, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) != self._SILERO_FRAME_SAMPLES:
            if len(samples) < self._SILERO_FRAME_SAMPLES:
                samples = np.pad(samples, (0, self._SILERO_FRAME_SAMPLES - len(samples)))
            else:
                samples = samples[:self._SILERO_FRAME_SAMPLES]

        ort_inputs = {
            "input": samples.reshape(1, -1),
            "state": self._state,
            "sr": np.array(self._SILERO_SAMPLE_RATE, dtype=np.int64),
        }
        ort_out = self._session.run(["output", "stateN"], ort_inputs)
        self._state = ort_out[1]
        return float(ort_out[0].squeeze())


    def _ulaw_frame_to_pcm16_16k(self, ulaw_bytes: bytes) -> bytes:
        """Convert µ-law 8kHz → PCM16 16kHz using audioop for quality upsampling."""
        import struct
        try:
            pcm_8k = struct.pack(f"<{len(ulaw_bytes)}h",
                                 *[_ULAW_TO_PCM16[b] for b in ulaw_bytes])
            pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
            return pcm_16k
        except Exception:
            # Linear interpolation fallback
            samples = [_ULAW_TO_PCM16[b] for b in ulaw_bytes]
            upsampled = []
            for i in range(len(samples)):
                upsampled.append(samples[i])
                nxt = samples[i + 1] if i + 1 < len(samples) else samples[i]
                upsampled.append((samples[i] + nxt) >> 1)
            return struct.pack(f"<{len(upsampled)}h", *upsampled)

    def is_speech(self, ulaw_bytes: bytes) -> bool:
        """Classify µ-law audio frame as speech/silence via neural Silero VAD."""
        if self._session is None:
            return self._fallback.is_speech(ulaw_bytes)
        try:
            self._pcm_accumulator.extend(self._ulaw_frame_to_pcm16_16k(ulaw_bytes))
            frame_bytes = self._SILERO_FRAME_SAMPLES * 2
            speech_detected = False
            while len(self._pcm_accumulator) >= frame_bytes:
                frame = bytes(self._pcm_accumulator[:frame_bytes])
                self._pcm_accumulator = self._pcm_accumulator[frame_bytes:]
                if self._infer_frame(frame) >= self._SPEECH_THRESHOLD:
                    speech_detected = True
            return speech_detected
        except Exception as e:
            logger.debug(f"[SileroVAD] Inference error: {e}")
            return self._fallback.is_speech(ulaw_bytes)

    def should_barge_in(self, speech_frames: int, is_agent_speaking: bool) -> bool:
        """Barge-in — Silero accuracy allows 1 fewer confirmation frame vs energy VAD."""
        if self._session is None:
            return self._fallback.should_barge_in(speech_frames, is_agent_speaking)
        barge_threshold = max(2, self.settings.speech_frames_for_barge_in - 1)
        return is_agent_speaking and speech_frames > barge_threshold

    def is_endpoint(self, silence_frames: int, buffer_has_audio: bool) -> bool:
        """Endpoint detection — tighter silence window (~500ms vs ~1.2s) thanks to neural accuracy."""
        if self._session is None:
            return self._fallback.is_endpoint(silence_frames, buffer_has_audio)
        # 35 frames × 20ms = 700ms silence window (vs 60 frames = 1.2s energy VAD)
        silence_threshold = max(35, self.settings.silence_frames_for_endpointing - 25)
        return buffer_has_audio and silence_frames > silence_threshold

    def reset_state(self):
        """Reset LSTM state between turns to prevent cross-turn state bleeding."""
        if self._session is not None:
            self._reset_state()
            self._pcm_accumulator.clear()

