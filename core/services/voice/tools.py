"""
Voice Tools for MCP.

Exposes voice capabilities as MCP-compatible tools.

Usage:
    from core.mcp import MCPServer
    from core.services.voice.tools import register_voice_tools

    server = MCPServer()
    register_voice_tools(server)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def register_voice_tools(server: MCPServer) -> None:
    """
    Register voice tools with an MCP server.

    Args:
        server: MCP server instance
    """
    _voice_service = None

    async def get_voice_service():
        """
        Lazy-initialize and return the VoiceService.

        Returns:
            VoiceService: The active voice service instance.
        """
        nonlocal _voice_service
        if _voice_service is None:
            from core.services.voice.service import VoiceService

            _voice_service = VoiceService()
        return _voice_service

    @server.tool(
        name="text_to_speech",
        description="Convert text to speech audio. Returns base64-encoded audio.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to convert to speech",
                },
                "voice": {
                    "type": "string",
                    "description": "Voice to use (alloy, echo, fable, onyx, nova, shimmer for OpenAI)",
                    "default": "alloy",
                },
                "speed": {
                    "type": "number",
                    "description": "Speech speed (0.25 to 4.0)",
                    "default": 1.0,
                    "minimum": 0.25,
                    "maximum": 4.0,
                },
            },
            "required": ["text"],
        },
    )
    async def text_to_speech(
        text: str, voice: str = "alloy", speed: float = 1.0
    ) -> dict[str, Any]:
        """
        Convert text to speech.

        Args:
            text: Text to synthesize.
            voice: Name of the voice to use.
            speed: Playback speed multiplier.

        Returns:
            dict[str, Any]: Base64 audio or error.
        """
        try:
            service = await get_voice_service()
            response = await service.text_to_speech(text, voice=voice, speed=speed)

            return {
                "status": "success",
                "audio_base64": response.audio_base64,
                "format": "mp3",
                "provider": response.provider,
                "model": response.model,
            }
        except Exception as e:
            logger.error("voice_tool_error", tool="tts", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="speech_to_text",
        description="Transcribe audio to text. Accepts base64-encoded audio.",
        input_schema={
            "type": "object",
            "properties": {
                "audio_base64": {
                    "type": "string",
                    "description": "Base64-encoded audio data",
                },
                "language": {
                    "type": "string",
                    "description": "Language hint (e.g., 'en', 'it', 'es'). Auto-detect if not specified.",
                    "default": None,
                },
            },
            "required": ["audio_base64"],
        },
    )
    async def speech_to_text(
        audio_base64: str, language: str | None = None
    ) -> dict[str, Any]:
        """
        Transcribe audio to text.

        Args:
            audio_base64: Base64-encoded audio data.
            language: Optional language hint.

        Returns:
            dict[str, Any]: Transcription text or error.
        """
        try:
            import base64

            audio_data = base64.b64decode(audio_base64)

            service = await get_voice_service()
            response = await service.speech_to_text(
                audio_data=audio_data, language=language
            )

            return {
                "status": "success",
                "text": response.text,
                "language": response.language,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("voice_tool_error", tool="stt", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="list_voices",
        description="List available voices for text-to-speech.",
        input_schema={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Voice provider (openai, elevenlabs, google)",
                    "enum": ["openai", "elevenlabs", "google"],
                    "default": "openai",
                },
            },
        },
    )
    async def list_voices(provider: str = "openai") -> dict[str, Any]:
        """
        List available voices for a provider.

        Args:
            provider: The voice provider name.

        Returns:
            dict[str, Any]: List of available voices.
        """
        voices = {
            "openai": [
                {"id": "alloy", "description": "Neutral and balanced"},
                {"id": "echo", "description": "Warm and natural"},
                {"id": "fable", "description": "British accent"},
                {"id": "onyx", "description": "Deep and authoritative"},
                {"id": "nova", "description": "Friendly and energetic"},
                {"id": "shimmer", "description": "Clear and expressive"},
            ],
            "elevenlabs": [
                {"id": "Rachel", "description": "American female"},
                {"id": "Adam", "description": "American male"},
                {"id": "Antoni", "description": "Warm storyteller"},
                {"id": "Bella", "description": "British female"},
            ],
            "google": [
                {"id": "en-US-Wavenet-A", "description": "Male US English"},
                {"id": "en-US-Wavenet-D", "description": "Male US English (alt)"},
                {"id": "en-US-Wavenet-F", "description": "Female US English"},
                {"id": "it-IT-Wavenet-A", "description": "Female Italian"},
            ],
        }

        return {
            "status": "success",
            "provider": provider,
            "voices": voices.get(provider, []),
        }

    logger.info("voice_tools_registered", tool_count=3)
