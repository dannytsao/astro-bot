# 語音查詢轉錄（main.transcribe_voice_query）
import base64
import json

import main


class TestTranscribeVoiceQuery:
    def test_high_confidence_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            main, "call_openrouter",
            lambda system, user_content, max_tokens, temperature=0.2:
                json.dumps({"transcript": "合歡山今晚適合拍銀河嗎", "confidence": "high"}),
        )
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result == {"transcript": "合歡山今晚適合拍銀河嗎", "confidence": "high"}

    def test_low_confidence_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            main, "call_openrouter",
            lambda system, user_content, max_tokens, temperature=0.2:
                json.dumps({"transcript": "嗯...不知道", "confidence": "low"}),
        )
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result["confidence"] == "low"

    def test_empty_transcript_forces_low_confidence(self, monkeypatch):
        # 不猜測原則：模型自稱 high 但逐字稿是空的，仍必須視為 low，不可信任模型自我膨脹的信心宣稱
        monkeypatch.setattr(
            main, "call_openrouter",
            lambda system, user_content, max_tokens, temperature=0.2:
                json.dumps({"transcript": "", "confidence": "high"}),
        )
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result["confidence"] == "low"

    def test_unrecognized_confidence_value_clamped_to_low(self, monkeypatch):
        monkeypatch.setattr(
            main, "call_openrouter",
            lambda system, user_content, max_tokens, temperature=0.2:
                json.dumps({"transcript": "合歡山", "confidence": "super-high"}),
        )
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result["confidence"] == "low"

    def test_retry_on_malformed_json_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def fake_call(system, user_content, max_tokens, temperature=0.2):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not json at all"
            return json.dumps({"transcript": "阿里山這週末", "confidence": "medium"})

        monkeypatch.setattr(main, "call_openrouter", fake_call)
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result == {"transcript": "阿里山這週末", "confidence": "medium"}
        assert calls["n"] == 2  # 確實重試了一次

    def test_both_attempts_fail_returns_safe_fallback(self, monkeypatch):
        # 兩次都拿不到合法 JSON 時安全降級，不可拋例外中斷整個訊息處理流程
        monkeypatch.setattr(
            main, "call_openrouter",
            lambda system, user_content, max_tokens, temperature=0.2: "still not json",
        )
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result == {"transcript": "", "confidence": "low"}

    def test_audio_correctly_base64_encoded_in_input_audio_block(self, monkeypatch):
        captured = {}

        def capture_content(system, user_content, max_tokens, temperature=0.2):
            captured["user_content"] = user_content
            return json.dumps({"transcript": "test", "confidence": "high"})

        monkeypatch.setattr(main, "call_openrouter", capture_content)
        main.transcribe_voice_query(b"hello-audio", audio_format="mp4")

        audio_block = captured["user_content"][0]
        assert audio_block["type"] == "input_audio"
        assert audio_block["input_audio"]["format"] == "mp4"
        assert base64.b64decode(audio_block["input_audio"]["data"]) == b"hello-audio"

    def test_non_dict_json_response_triggers_retry(self, monkeypatch):
        # LLM 回傳合法 JSON 但不是物件（例如純陣列）也要視同解析失敗並重試
        calls = {"n": 0}

        def fake_call(system, user_content, max_tokens, temperature=0.2):
            calls["n"] += 1
            if calls["n"] == 1:
                return json.dumps(["not", "a", "dict"])
            return json.dumps({"transcript": "日月潭", "confidence": "high"})

        monkeypatch.setattr(main, "call_openrouter", fake_call)
        result = main.transcribe_voice_query(b"fake-audio-bytes")
        assert result == {"transcript": "日月潭", "confidence": "high"}
        assert calls["n"] == 2


class TestVoiceHandlerWiring:
    def test_max_voice_audio_bytes_is_15mb(self):
        # Gemini 音訊輸入上限，process_voice_and_reply() 依此防呆檢查
        assert main.MAX_VOICE_AUDIO_BYTES == 15 * 1024 * 1024

    def test_voice_transcription_error_is_runtime_error(self):
        assert issubclass(main.VoiceTranscriptionError, RuntimeError)
