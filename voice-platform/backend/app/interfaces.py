"""
Abstract Base Interfaces (Contracts)
Defines explicit interfaces for all subsystems in accordance with
Interface Segregation Principle (ISP) and Dependency Inversion Principle (DIP).
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, AsyncGenerator


class BaseLLMService(ABC):
    """Abstract interface for Large Language Model inference engines."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        history: List[Dict[str, str]],
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Any:
        """Generate a complete text response or function call dictionary."""
        pass

    async def generate_stream(
        self,
        prompt: str,
        history: List[Dict[str, str]],
        system_prompt: str
    ) -> AsyncGenerator[str, None]:
        """Stream generated text chunks. (Optional override)."""
        response = await self.generate(prompt, history, system_prompt)
        if isinstance(response, str):
            yield response
        elif isinstance(response, dict) and "text" in response:
            yield response["text"]


class BaseSTTService(ABC):
    """Abstract interface for Speech-to-Text transcription services."""

    @abstractmethod
    async def transcribe_bytes(
        self,
        audio_bytes: bytes,
        format: str = "wav",
        language_code: Optional[str] = None
    ) -> str:
        """Transcribe raw audio bytes into text."""
        pass

    async def transcribe(self, file_path_or_bytes: Any, format: Optional[str] = None) -> str:
        """Transcribe audio from a file path or raw bytes."""
        if isinstance(file_path_or_bytes, (bytes, bytearray)):
            return await self.transcribe_bytes(file_path_or_bytes, format=format or "wav")
        import os
        ext = os.path.splitext(str(file_path_or_bytes))[-1].lstrip(".").lower() or (format or "wav")
        with open(file_path_or_bytes, "rb") as f:
            data = f.read()
        return await self.transcribe_bytes(data, format=ext)


class BaseTTSService(ABC):
    """Abstract interface for Text-to-Speech synthesis services."""

    @abstractmethod
    async def synthesize_bytes(
        self,
        text: str,
        format: str = "pcm",
        language_code: Optional[str] = None,
        speaker: Optional[str] = None
    ) -> bytes:
        """Synthesize text into raw audio bytes."""
        pass


class BaseAudioTranscoder(ABC):
    """Abstract interface for audio format transcoding (e.g. mu-law <-> PCM16)."""

    @abstractmethod
    def ulaw_to_pcm16_bytes(self, ulaw_bytes: bytes) -> bytes:
        """Convert mu-law 8kHz audio bytes to PCM16 16kHz audio bytes."""
        pass

    @abstractmethod
    def pcm16_to_ulaw_b64(self, pcm_bytes: bytes, input_sample_rate: int = 24000) -> str:
        """Downsample and encode PCM16 bytes to base64-encoded mu-law 8kHz."""
        pass


class BaseVADDetector(ABC):
    """Abstract interface for Voice Activity Detection and Barge-In detection."""

    @abstractmethod
    def is_speech(self, ulaw_bytes: bytes) -> bool:
        """Determine whether the audio frame contains active human speech."""
        pass

    @abstractmethod
    def should_barge_in(self, speech_frames: int, is_agent_speaking: bool) -> bool:
        """Determine whether the user's speech should interrupt agent playback."""
        pass

    @abstractmethod
    def is_endpoint(self, silence_frames: int, buffer_has_audio: bool) -> bool:
        """Determine whether the user has finished their speech turn."""
        pass


class BaseTelephonyService(ABC):
    """Abstract interface for Telephony call control (Twilio, SIP, etc.)."""

    @abstractmethod
    async def transfer_call(self, call_sid: str, target_phone: str) -> bool:
        """Transfer an active live call to another phone number or SIP address."""
        pass

    @abstractmethod
    async def end_call(self, call_sid: str) -> bool:
        """Terminate an active live call."""
        pass

    @abstractmethod
    async def sync_active_calls(self) -> Dict[str, Any]:
        """Synchronize active live call states from the carrier."""
        pass


class BaseToolExecutor(ABC):
    """Abstract interface for executing tools and agent function calls."""

    @abstractmethod
    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool and return the resulting dictionary."""
        pass

    @abstractmethod
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return schema definitions of available tools for LLM binding."""
        pass


class BaseRAGEngine(ABC):
    """Abstract interface for Retrieval-Augmented Generation context search."""

    @abstractmethod
    async def search(self, query: str, node_id: Optional[str] = None, top_k: int = 3) -> str:
        """Retrieve relevant knowledge base passages for a user query."""
        pass
