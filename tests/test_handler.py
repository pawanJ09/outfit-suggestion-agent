import json
from unittest.mock import MagicMock, patch

import pytest

from src import handler

SAMPLE_RECOMMENDATION_TEXT = (
    "Layer up with a waterproof shell -- it's 38F with gusty wind "
    "and a real chance of rain this afternoon."
)

SAMPLE_CONVERSE_RESPONSE = {
    "output": {"message": {"content": [{"text": SAMPLE_RECOMMENDATION_TEXT}]}}
}


class TestLoadInstructions:
    def test_reads_from_env_var_when_set(self, monkeypatch):
        monkeypatch.setenv("OUTFIT_INSTRUCTIONS", "test instructions from env")
        assert handler._load_instructions() == "test instructions from env"

    def test_falls_back_to_local_file_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("OUTFIT_INSTRUCTIONS", raising=False)
        text = handler._load_instructions()
        assert "Outfit Suggestion agent" in text

    def test_raises_when_neither_env_var_nor_file_available(self, monkeypatch):
        monkeypatch.delenv("OUTFIT_INSTRUCTIONS", raising=False)
        # Patch Path.exists on the real class so the real chained
        # Path(__file__).resolve().parent.parent / "config" / "..." lookup
        # still runs for real, it just always reports "not found" -- this
        # is simpler and more reliable than mocking the whole chain.
        monkeypatch.setattr(handler.Path, "exists", lambda self: False)
        with pytest.raises(handler.OutfitRecommendationError):
            handler._load_instructions()


class TestFetchOutfitRecommendation:
    @patch("src.handler.boto3.client")
    def test_returns_text_on_success(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("OUTFIT_INSTRUCTIONS", "test instructions")
        mock_client = MagicMock()
        mock_client.converse.return_value = SAMPLE_CONVERSE_RESPONSE
        mock_boto_client.return_value = mock_client

        result = handler.fetch_outfit_recommendation("38F, windy, 60% rain chance")

        assert "waterproof shell" in result
        mock_client.converse.assert_called_once()
        call_kwargs = mock_client.converse.call_args.kwargs
        assert call_kwargs["modelId"] == handler.MODEL_ID
        assert call_kwargs["system"] == [{"text": "test instructions"}]

    @patch("src.handler.boto3.client")
    def test_raises_on_bedrock_client_error(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("OUTFIT_INSTRUCTIONS", "test instructions")
        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("throttled")
        mock_boto_client.return_value = mock_client

        with pytest.raises(handler.OutfitRecommendationError):
            handler.fetch_outfit_recommendation("some weather text")

    @patch("src.handler.boto3.client")
    def test_raises_on_unexpected_response_shape(self, mock_boto_client, monkeypatch):
        monkeypatch.setenv("OUTFIT_INSTRUCTIONS", "test instructions")
        mock_client = MagicMock()
        mock_client.converse.return_value = {"output": {"message": {"content": []}}}
        mock_boto_client.return_value = mock_client

        with pytest.raises(handler.OutfitRecommendationError):
            handler.fetch_outfit_recommendation("some weather text")


class TestLambdaHandler:
    @patch("src.handler.fetch_outfit_recommendation")
    def test_success_returns_200(self, mock_fetch):
        mock_fetch.return_value = "Wear a warm coat."
        event = {"inputText": "35F and windy"}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert json.loads(result["body"])["recommendation"] == "Wear a warm coat."

    def test_missing_input_text_returns_400(self):
        result = handler.lambda_handler({}, None)

        assert result["statusCode"] == 400
        assert "error" in json.loads(result["body"])

    def test_empty_input_text_returns_400(self):
        result = handler.lambda_handler({"inputText": ""}, None)

        assert result["statusCode"] == 400

    def test_non_string_input_text_returns_400(self):
        result = handler.lambda_handler({"inputText": 12345}, None)

        assert result["statusCode"] == 400

    @patch("src.handler.fetch_outfit_recommendation")
    def test_upstream_failure_returns_502(self, mock_fetch):
        mock_fetch.side_effect = handler.OutfitRecommendationError("boom")
        event = {"inputText": "35F and windy"}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 502
