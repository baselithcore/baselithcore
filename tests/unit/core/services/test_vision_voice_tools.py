import pytest
import base64
from unittest.mock import MagicMock, AsyncMock, patch
from core.services.vision.tools import register_vision_tools
from core.services.voice.tools import register_voice_tools
from core.services.vision.models import VisionResponse


@pytest.fixture
def mock_mcp_server():
    server = MagicMock()
    # Mock the tool decorator
    server.tool = MagicMock(return_value=lambda x: x)
    return server


@pytest.mark.asyncio
async def test_register_vision_tools(mock_mcp_server):
    mock_service = MagicMock()
    mock_service.analyze = AsyncMock(
        return_value=VisionResponse(
            success=True,
            content="Image description",
            provider="test",
            model="test-model",
        )
    )

    register_vision_tools(mock_mcp_server, service=mock_service)

    # Verify tools were registered
    assert mock_mcp_server.tool.call_count == 6

    # Retrieve the registered functions
    # In my simplified mock, the decorator returns the function itself

    # Actually, because I used `lambda x: x`, I need to find the functions
    # Let's re-mock more precisely to capture the functions
    tool_registry = {}

    def tool_decorator(name, **kwargs):
        def wrapper(func):
            tool_registry[name] = func
            return func

        return wrapper

    mock_mcp_server.tool.side_effect = tool_decorator
    register_vision_tools(mock_mcp_server, service=mock_service)

    # Test analyze_image
    analyze_img = tool_registry["analyze_image"]
    res = await analyze_img(image_data="base64data", prompt="test", is_url=False)
    assert res["status"] == "success"
    assert res["analysis"] == "Image description"

    # Test analyze_image as URL
    res_url = await analyze_img(image_data="http://url", prompt="test", is_url=True)
    assert res_url["status"] == "success"

    # Test error handling
    mock_service.analyze.side_effect = Exception("Vision Error")
    res_err = await analyze_img(image_data="fail")
    assert res_err["status"] == "error"
    mock_service.analyze.side_effect = None  # Reset

    # Test extract_text_from_image
    ocr = tool_registry["extract_text_from_image"]
    res_ocr = await ocr(image_data="base64")
    assert res_ocr["status"] == "success"

    # Test analyze_screenshot
    screenshot = tool_registry["analyze_screenshot"]
    res_ss = await screenshot(image_data="base64", question="where is the button?")
    assert res_ss["status"] == "success"

    # Test analyze_diagram
    diagram = tool_registry["analyze_diagram"]
    res_diag = await diagram(image_data="base64")
    assert res_diag["status"] == "success"

    # Test compare_images
    compare = tool_registry["compare_images"]
    res_comp = await compare(
        image1_data="b1", image2_data="b2", comparison_focus="color"
    )
    assert res_comp["status"] == "success"

    # Test find_ui_element
    find = tool_registry["find_ui_element"]
    mock_service.analyze.return_value = MagicMock(
        as_json={"found": True}, content="json_string"
    )
    await find(image_data="base64", element_description="submit")
    # Test error handling for ALL vision tools
    tools_to_test = [
        "extract_text_from_image",
        "analyze_screenshot",
        "analyze_diagram",
        "compare_images",
        "find_ui_element",
    ]
    for tool_name in tools_to_test:
        func = tool_registry[tool_name]
        mock_service.analyze.side_effect = Exception(f"Error in {tool_name}")
        # Call with appropriate number of args
        if tool_name == "compare_images":
            res = await func(image1_data="b1", image2_data="b2")
        elif tool_name == "find_ui_element":
            res = await func(image_data="b", element_description="d")
        else:
            res = await func(image_data="b")
        assert res["status"] == "error"
        assert f"Error in {tool_name}" in res["error"]


@pytest.mark.asyncio
async def test_register_voice_tools(mock_mcp_server):
    tool_registry = {}

    def tool_decorator(name, **kwargs):
        def wrapper(func):
            tool_registry[name] = func
            return func

        return wrapper

    mock_mcp_server.tool.side_effect = tool_decorator

    mock_voice_service = MagicMock()
    mock_voice_service.text_to_speech = AsyncMock(
        return_value=MagicMock(audio_base64="audio_data", provider="test", model="test")
    )
    mock_voice_service.speech_to_text = AsyncMock(
        return_value=MagicMock(text="Transcribed text", language="en", provider="test")
    )

    # Mocking lazy load of VoiceService
    with patch(
        "core.services.voice.service.VoiceService", return_value=mock_voice_service
    ):
        register_voice_tools(mock_mcp_server)

        # Verify 3 tools registered
        assert len(tool_registry) == 3

        # Test text_to_speech
        tts = tool_registry["text_to_speech"]
        res = await tts(text="hello", voice="alloy", speed=1.0)
        assert res["status"] == "success"

        # Test speech_to_text
        stt = tool_registry["speech_to_text"]
        b64_audio = base64.b64encode(b"audio").decode()
        res_stt = await stt(audio_base64=b64_audio)
        assert res_stt["status"] == "success"

        # Test STT error path
        mock_voice_service.speech_to_text.side_effect = Exception("STT Error")
        res_stt_err = await stt(audio_base64=b64_audio)
        assert res_stt_err["status"] == "error"
        assert "STT Error" in res_stt_err["error"]

        # Test list_voices
        list_v = tool_registry["list_voices"]
        res_v = await list_v(provider="openai")
        assert res_v["status"] == "success"

        # Test tts error
        mock_voice_service.text_to_speech.side_effect = Exception("Voice Error")
        res_e = await tts(text="fail")
        assert res_e["status"] == "error"
