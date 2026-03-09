#!/usr/bin/env python3
"""
Demo: Voice Service - Text-to-Speech and Speech-to-Text.

This demo showcases the VoiceService capabilities:
- Text-to-Speech (TTS) with multiple providers
- Speech-to-Text (STT) transcription
- List available voices

Prerequisites:
    export OPENAI_API_KEY=your-key
    # Optional for other providers:
    export ELEVENLABS_API_KEY=your-key
    export GOOGLE_API_KEY=your-key

Usage:
    python examples/demo_voice.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.services.voice import VoiceService


async def demo_tts(service: VoiceService) -> None:
    """Demonstrate Text-to-Speech."""
    print("\n" + "=" * 60)
    print("🗣️ DEMO: Text-to-Speech")
    print("=" * 60)

    text = "Hello! I am the baselith-core's voice interface. I can speak in multiple languages and voices."

    print(f"📝 Text: {text}")
    print("⏳ Generating speech...")

    response = await service.text_to_speech(text=text)

    if response.success:
        # Save audio to file
        output_path = Path("demo_output.mp3")
        output_path.write_bytes(response.content)
        print(f"✅ Audio generated ({len(response.content)} bytes)")
        print(f"💾 Saved to: {output_path.absolute()}")
        print(f"🔊 Provider: {response.provider}")
    else:
        print(f"❌ Failed: {response.error}")


async def demo_stt(service: VoiceService, audio_path: str | None = None) -> None:
    """Demonstrate Speech-to-Text."""
    print("\n" + "=" * 60)
    print("👂 DEMO: Speech-to-Text")
    print("=" * 60)

    if audio_path and Path(audio_path).exists():
        audio_data = Path(audio_path).read_bytes()
        print(f"📁 Audio file: {audio_path}")
        print("⏳ Transcribing...")

        response = await service.speech_to_text(audio_data=audio_data)

        if response.success:
            print("✅ Transcription complete")
            print(f"📝 Text: {response.text}")
            print(f"🔊 Provider: {response.provider}")
        else:
            print(f"❌ Failed: {response.error}")
    else:
        print("⚠️  No audio file provided for STT demo.")
        print("   To test STT, run: python demo_voice.py <audio_file.mp3>")


async def demo_list_voices(service: VoiceService) -> None:
    """List available voices."""
    print("\n" + "=" * 60)
    print("📋 DEMO: Available Voices")
    print("=" * 60)

    voices = await service.list_voices()

    print(f"🎤 Found {len(voices)} voices:")
    for voice in voices[:10]:  # Show first 10
        print(f"   - {voice.name} ({voice.provider}): {voice.description or 'N/A'}")

    if len(voices) > 10:
        print(f"   ... and {len(voices) - 10} more")


async def main() -> None:
    """Run all demos."""
    print("🚀 Voice Service Demo")
    print("=" * 60)

    # Initialize service
    service = VoiceService()
    print(f"✅ VoiceService initialized (default: {service.default_provider.value})")

    await demo_tts(service)
    await demo_list_voices(service)

    # Check for audio file argument for STT
    audio_path = sys.argv[1] if len(sys.argv) > 1 else None
    await demo_stt(service, audio_path)

    print("\n" + "=" * 60)
    print("🎉 All demos completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
