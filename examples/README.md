# Examples

This directory contains runnable demo scripts showcasing the Next-Gen capabilities of the Baselith-Core.

## Prerequisites

```bash
# Required
export OPENAI_API_KEY=your-key

# Optional (for additional providers)
export ANTHROPIC_API_KEY=your-key
export ELEVENLABS_API_KEY=your-key
export GOOGLE_API_KEY=your-key
```

## Available Demos

### 🔍 Vision Demo

Demonstrates multi-modal image analysis, OCR, and UI screenshot analysis.

```bash
# Analyze a local image
python examples/demo_vision.py path/to/image.png

# Run with default URL sample
python examples/demo_vision.py
```

### 💻 Coding Demo

Demonstrates the Coding Agent with auto-debug loop, code generation, and test generation.

```bash
python examples/demo_coding.py
```

### 🗣️ Voice Demo

Demonstrates Text-to-Speech and Speech-to-Text capabilities.

```bash
# TTS demo (generates audio file)
python examples/demo_voice.py

# STT demo (transcribe audio)
python examples/demo_voice.py path/to/audio.mp3
```

### 🌐 Browser Demo

Demonstrates autonomous browser navigation and screenshot capture.

```bash
# Prerequisites
pip install playwright
playwright install chromium

# Navigate to a URL
python examples/demo_browser.py https://example.com

# Run autonomous task
python examples/demo_browser.py --autonomous
```

## Quick Test

Run a quick import check to verify all modules are working:

```bash
python -c "from core.services.vision import VisionService; print('✅ Vision OK')"
python -c "from core.services.voice import VoiceService; print('✅ Voice OK')"
python -c "from core.agents import CodingAgent; print('✅ Coding OK')"
python -c "from core.agents import BrowserAgent; print('✅ Browser OK')"
```
