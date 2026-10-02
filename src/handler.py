"""
outfit-suggestion-agent (Style 2: Classic Bedrock Converse API)

Pure-reasoning Lambda in the Agentic Weather App. Given a weather summary
as input, calls Bedrock's Converse API directly and returns a short
clothing recommendation. No Action Groups, no tools, no Gateway -- this
IS the whole "Act" step, and it's one call, not a loop, since this
component never needs to call anything else to finish its job.

This is the Style 2 rebuild of what was previously a Bedrock Agent
(Style 1, in config/agent-config.json + Bedrock's own managed
orchestration). Bedrock Agents Classic closed to new AWS accounts on
July 30, 2026, so this component is now hand-written: we call
bedrock-runtime.converse() ourselves instead of Bedrock running an agent
loop for us. The system prompt (config/instructions.txt) carried over
unchanged -- the reasoning task didn't change, only who's driving the
model call.

Invoked directly via boto3 lambda.invoke() from weather-orchestrator-lambda
-- there's no Bedrock Agent event shape to branch on anymore, so unlike
the two weather Lambdas, this one has a single invocation shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3

# Placeholder -- confirm the current Claude Haiku model ID available to
# Bedrock in your region with `aws bedrock list-foundation-models
# --query "modelSummaries[?contains(modelId, 'haiku')]"` before deploying.
# Same caveat as the Style 1 version of this component; carried over
# because I still can't verify this live from here.
MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"

MAX_TOKENS = 300
TEMPERATURE = 0.4


class OutfitRecommendationError(Exception):
    """Raised when Bedrock can't be reached or returns an unusable response."""


def _load_instructions() -> str:
    """
    Production: read from the OUTFIT_INSTRUCTIONS environment variable,
    which the deploy workflow sets from config/instructions.txt at deploy
    time. This keeps the prompt editable (edit the .txt file, redeploy)
    without touching this code, same benefit the Style 1 version had --
    just delivered via env var instead of a Bedrock Agent's instruction
    field, since there's no separate "agent resource" for the prompt to
    live on anymore.

    Local/dev fallback: if the env var isn't set (e.g. running tests or
    a manual script outside Lambda), read config/instructions.txt
    directly from the repo. This only works when the repo layout is
    intact -- it's a convenience for local use, not what runs in Lambda.
    """
    env_value = os.environ.get("OUTFIT_INSTRUCTIONS")
    if env_value:
        return env_value

    local_path = Path(__file__).resolve().parent.parent / "config" / "instructions.txt"
    if local_path.exists():
        return local_path.read_text()

    raise OutfitRecommendationError(
        "No OUTFIT_INSTRUCTIONS env var set and config/instructions.txt not "
        "found locally. In Lambda, OUTFIT_INSTRUCTIONS should always be set "
        "by the deploy workflow -- if you're seeing this in production, the "
        "function's environment variable is missing."
    )


def fetch_outfit_recommendation(input_text: str) -> str:
    """Call Bedrock's Converse API with the system prompt + weather text."""
    instructions = _load_instructions()
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    try:
        response = client.converse(
            modelId=MODEL_ID,
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": input_text}]}],
            inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
        )
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
        # botocore/boto3 exception here means "Bedrock call failed," and
        # the caller only needs to know that, not which specific
        # exception class botocore raised.
        raise OutfitRecommendationError(f"Bedrock Converse call failed: {exc}") from exc

    try:
        return response["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise OutfitRecommendationError(f"Bedrock response had an unexpected shape: {exc}") from exc


def _plain_response(http_status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": http_status, "body": json.dumps(body)}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    input_text = event.get("inputText")
    if not input_text or not isinstance(input_text, str):
        return _plain_response(400, {"error": "Missing or invalid 'inputText' in the request"})

    try:
        recommendation = fetch_outfit_recommendation(input_text)
    except OutfitRecommendationError as exc:
        return _plain_response(502, {"error": str(exc)})

    return _plain_response(200, {"recommendation": recommendation})
