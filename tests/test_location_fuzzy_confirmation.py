from types import SimpleNamespace

import pytest

import main


def _text_event(user_id, text):
    return SimpleNamespace(
        source=SimpleNamespace(user_id=user_id),
        message=SimpleNamespace(text=text),
        reply_token="reply-token",
    )


class TestLocationSuggestion:
    def test_one_character_typo_suggests_known_location(self):
        suggestion = main.suggest_known_location("阿裏山")

        assert suggestion is not None
        assert suggestion.location_name == "阿里山"

    def test_ambiguous_generic_term_has_no_suggestion(self):
        assert main.suggest_known_location("觀景臺") is None

    def test_unrelated_location_has_no_suggestion(self):
        assert main.suggest_known_location("神祕小徑") is None

    def test_uses_user_text_when_llm_normalizes_typo(self):
        intent = {"location_name": "阿里山", "lat": 23.51, "lon": 120.8}

        try:
            main.normalize_intent(intent, "7月20號阿裏山適合拍銀河嗎")
        except main.LocationSuggestionError as error:
            assert error.location_name == "阿裏山"
            assert error.suggestion.location_name == "阿里山"
        else:
            raise AssertionError("LLM 正規化地名後仍必須要求使用者確認")

    def test_confirmation_marker_is_consumed_without_asking_again(self):
        intent = {
            "location_name": "阿里山",
            "lat": None,
            "lon": None,
            "_confirmed_location": "阿里山",
        }

        normalized = main.normalize_intent(intent, "今晚阿裏山適合拍銀河嗎")

        assert normalized["location_name"] == "阿里山"
        assert "_confirmed_location" not in normalized


def test_run_query_uses_already_normalized_confirmed_intent(monkeypatch):
    class CalculationStarted(RuntimeError):
        pass

    confirmed_intent = {
        "location_name": "阿里山",
        "lat": None,
        "lon": None,
        "date_start": "2026-07-20",
        "date_end": "2026-07-20",
        "targets": ["銀河核心"],
        "_confirmed_location": "阿里山",
    }
    normalized_intent = main.normalize_intent(confirmed_intent, "今晚阿裏山適合拍銀河嗎")
    monkeypatch.setattr(
        main,
        "normalize_intent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("run_query 不可重複正規化")),
    )
    monkeypatch.setattr(
        main.wgs84,
        "latlon",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CalculationStarted()),
    )

    with pytest.raises(CalculationStarted):
        main.run_query("今晚阿裏山適合拍銀河嗎", prefetched_intent=normalized_intent)


def test_unconfirmed_suggestion_does_not_run_query(monkeypatch):
    user_id = "fuzzy-pending-user"
    pushed = []
    main.user_state.pop(user_id, None)
    main.user_pending_location_query.pop(user_id, None)
    monkeypatch.setattr(main, "get_display_name", lambda _user_id: "測試者")
    monkeypatch.setattr(main, "safe_push_message", lambda _user_id, message, _context: pushed.append(message) or True)
    monkeypatch.setattr(main, "persist_pending_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "log_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "mark_message_as_read", lambda _token: None)
    monkeypatch.setattr(
        main,
        "run_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("未確認前不可進入計算")),
    )

    try:
        main.process_and_reply(
            user_id,
            "今晚阿裏山適合拍銀河嗎",
            prefetched_intent={"location_name": "阿裏山", "lat": None, "lon": None},
        )

        assert main.user_state[user_id] == "waiting_location_confirmation"
        assert main.user_pending_location_query[user_id]["suggested_location"] == "阿里山"
        assert pushed
    finally:
        main.user_state.pop(user_id, None)
        main.user_pending_location_query.pop(user_id, None)


def test_confirmed_suggestion_uses_canonical_coordinates(monkeypatch):
    user_id = "fuzzy-confirm-user"
    submitted = []
    replies = []
    main.user_state[user_id] = "waiting_location_confirmation"
    main.user_pending_location_query[user_id] = {
        "text": "今晚阿裏山適合拍銀河嗎",
        "intent": {"location_name": "阿裏山", "lat": None, "lon": None},
        "location_name": "阿裏山",
        "suggested_location": "阿里山",
        "reply_prefix": "",
    }
    monkeypatch.setattr(main, "safe_reply_message", lambda _token, message, _context: replies.append(message) or True)
    monkeypatch.setattr(main, "mark_message_as_read", lambda _token: None)
    monkeypatch.setattr(main, "clear_pending_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "submit_background_query", lambda *args: submitted.append(args))

    try:
        main.handle_message(_text_event(user_id, "是"))

        assert user_id not in main.user_state
        assert len(submitted) == 1
        submitted_intent = submitted[0][3]
        assert submitted_intent["location_name"] == "阿里山"
        assert submitted_intent["_confirmed_location"] == "阿里山"
        assert (submitted_intent["lat"], submitted_intent["lon"]) == main.KNOWN_LOCATIONS["阿里山"]
        assert replies
    finally:
        main.user_state.pop(user_id, None)
        main.user_pending_location_query.pop(user_id, None)


def test_rejected_suggestion_transitions_to_coordinate_prompt(monkeypatch):
    user_id = "fuzzy-reject-user"
    persisted = []
    replies = []
    main.user_state[user_id] = "waiting_location_confirmation"
    main.user_pending_location_query[user_id] = {
        "text": "今晚阿裏山適合拍銀河嗎",
        "intent": {"location_name": "阿裏山", "lat": None, "lon": None},
        "location_name": "阿裏山",
        "suggested_location": "阿里山",
        "reply_prefix": "",
    }
    monkeypatch.setattr(main, "safe_reply_message", lambda _token, message, _context: replies.append(message) or True)
    monkeypatch.setattr(main, "mark_message_as_read", lambda _token: None)
    monkeypatch.setattr(main, "persist_pending_state", lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr(main, "get_display_name", lambda _user_id: "測試者")
    monkeypatch.setattr(main, "log_wish", lambda *args, **kwargs: True)

    try:
        main.handle_message(_text_event(user_id, "不是"))

        assert main.user_state[user_id] == "waiting_location_coordinates"
        assert persisted
        assert replies
    finally:
        main.user_state.pop(user_id, None)
        main.user_pending_location_query.pop(user_id, None)
