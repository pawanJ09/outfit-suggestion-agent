# outfit-suggestion-agent

Pure-reasoning **Lambda** in the Agentic Weather App (Style 2: Classic
Bedrock Converse API). Given a weather summary as input, calls Bedrock's
Converse API directly and returns a short clothing recommendation. No
tools, no Action Groups, no Gateway — the "Act" step *is* the Bedrock
call, and it's a single call, not a loop, since this component never
needs anything else to finish its job.

Runtime: **Python 3.14** · Dependencies: **boto3** (ships with the Lambda
runtime — nothing to vendor)

## This repo's history: a Style 1 → Style 2 rebuild, same name on purpose

This component was originally built as a Bedrock Agent (Style 1) — see
git history for `config/agent-config.json` and `scripts/deploy_agent.sh`,
since removed. That attempt hit a real AWS policy wall: **Bedrock Agents
Classic closed to new AWS accounts on July 30, 2026** — `CreateAgent`
returns `AccessDeniedException` for any account without prior usage in
the past 12 months, with no exception process. Existing agents on
allowlisted accounts keep working fine; this is strictly about new
accounts starting fresh, which is what this one was.

So this is now a **hand-written** version: instead of Bedrock running an
agent loop for us, `src/handler.py` calls `bedrock-runtime.converse()`
directly. The reasoning task didn't change — `config/instructions.txt`
carried over almost unchanged (one line of Bedrock-Agent-specific jargon
removed) — only who's driving the model call changed.

**The repo, Lambda function name, and IAM role name are all intentionally
unchanged** (`outfit-suggestion-agent` throughout) to avoid creating
duplicate AWS resources. This has one real consequence, handled explicitly
in `deploy.yml`:

> The IAM role `outfit-suggestion-agent-role` most likely **already
> exists** in your AWS account — the Style 1 deploy script's role-creation
> step ran successfully before `CreateAgent` failed. That role trusts
> `bedrock.amazonaws.com` (it was a Bedrock Agent *service* role), which
> is wrong for a Lambda — Lambda can only assume roles that trust
> `lambda.amazonaws.com`. So the deploy workflow's role-handling step
> doesn't just "create if missing" like the other two Lambda repos — it
> **unconditionally runs `update-assume-role-policy`** every deploy to
> correct the trust relationship, and explicitly deletes the old
> Bedrock-Agent-era inline policy (`${ROLE_NAME}-invoke-model`) before
> attaching the current one. Safe to run whether the role is fresh or
> carrying over Style 1's leftover state.

## Repo layout

```
src/handler.py           Lambda handler + Bedrock Converse client
config/instructions.txt  The system prompt (carried over from Style 1)
tests/test_handler.py    Unit tests (mocked boto3, no real AWS calls)
events/                  Sample invocation payload
iam/                     Trust policy, execution policy
.github/workflows/       CI (lint+test) and Deploy (build, deploy, smoke-test)
```

## How the prompt gets to the Lambda

Unlike the two weather Lambdas (pure stdlib, nothing external to ship),
this one has an external text asset — the system prompt — that needs to
reach the running function. Two options existed: bundle
`config/instructions.txt` inside the deployment zip, or deliver it as a
Lambda environment variable. **Environment variable won** — it keeps the
deployment package exactly as simple as the other two repos' (just
`handler.py`, zipped alone) and avoids a zip-layout/local-layout path
mismatch between how `__file__`-relative lookups resolve locally
(`src/handler.py` with `config/` as a sibling of `src/`) versus inside a
flattened zip (`handler.py` at the zip root).

Concretely: `deploy.yml` reads `config/instructions.txt` at deploy time
and sets it as the `OUTFIT_INSTRUCTIONS` environment variable via
`update-function-configuration` (or inline on `create-function` for a
first deploy). Edit the prompt, push, redeploy — no code change needed,
same benefit the Style 1 version had via the agent's instruction field.

**Built with `python3 -c` + `json.dumps`, not AWS CLI shorthand** — the
prompt has commas, quotes, and newlines, all of which corrupt the CLI's
`Variables={KEY=VALUE}` shorthand syntax (commas are that syntax's
key-value delimiter, so a naive shorthand attempt would silently truncate
the prompt at its first comma). `deploy.yml` builds real JSON instead and
passes it straight to `--environment`.

**Size headroom**: the current prompt serializes to ~1.5 KB; Lambda's
combined environment-variable limit is 4 KB. Comfortable now, but worth
knowing if the prompt grows substantially later.

For local development/testing without the env var set, `handler.py` falls
back to reading `config/instructions.txt` directly from disk — convenient
for `make test` and manual runs, but this fallback only works when the
repo layout is intact; it's not what runs in the actual deployed Lambda.

## Invocation contract

Single shape, unlike the weather Lambdas (no Bedrock Agent event format
to branch on anymore — this is invoked directly via `boto3`
`lambda.invoke()`):

```json
{ "inputText": "a plain-text weather summary" }
```

Returns:
```json
{ "statusCode": 200, "body": "{\"recommendation\": \"...\"}" }
```
or `400` (missing/invalid `inputText`) / `502` (Bedrock call failed or
returned an unexpected shape).

## ⚠️ Before you deploy

Same caveat as the Style 1 version, carried forward: `MODEL_ID` in
`src/handler.py` (and the matching model ARN in
`iam/execution-role-policy.json`) is a placeholder. Confirm the current
Claude Haiku model ID available to Bedrock in your region:
```bash
aws bedrock list-foundation-models \
  --query "modelSummaries[?contains(modelId, 'haiku')]"
```
If you change the model ID in `handler.py`, update the ARN in
`iam/execution-role-policy.json` to match — they're two separate static
values, not templated together.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt boto3
make check    # ruff format + ruff check + pytest --cov (90% floor)
```

## Manual test (needs real AWS credentials + Bedrock model access)

```bash
python3 -c "
from src.handler import lambda_handler
import json
print(lambda_handler(json.load(open('events/sample_event_direct.json')), None))
"
```

Unlike the weather Lambdas' manual test, this one makes a real Bedrock
call and needs valid AWS credentials in your environment with
`bedrock:InvokeModel` permission — it's not a free, keyless external API.

## AWS setup: automatic, including the legacy-role correction

On every deploy — first run or subsequent — the `deploy` job:

1. Creates `outfit-suggestion-agent-role` if it's genuinely missing, or
   finds the one left over from the Style 1 attempt.
2. **Unconditionally** corrects its trust policy to `lambda.amazonaws.com`
   and removes the old Bedrock-Agent-era inline policy, then attaches the
   current one (logs + `bedrock:InvokeModel`).
3. Creates `outfit-suggestion-agent` (the function) if missing, or updates
   its code and `OUTFIT_INSTRUCTIONS` env var if it already exists.

Nothing to run by hand except the one-time OIDC setup — same pattern as
`current-weather-lambda` and `forecast-weather-lambda`.

## GitHub Actions setup

- **`ci.yml`** — lint, format check, tests with a 90% coverage floor.
  Installs `boto3` explicitly (unlike real Lambda, GitHub's runners don't
  have it preinstalled).
- **`deploy.yml`** — reruns CI as a gate, builds `function.zip`, runs the
  role-correction logic above, creates-or-updates the function (code +
  environment variable), then invokes it live with
  `events/sample_event_direct.json` and fails if the response isn't a 200
  with a `recommendation` field.

### Required repo secret

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | ARN of an IAM role GitHub assumes via OIDC |

Same reuse-or-separate-role decision as the other two repos. Permissions
policy needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RoleBootstrap",
      "Effect": "Allow",
      "Action": ["iam:GetRole", "iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:UpdateAssumeRolePolicy"],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/outfit-suggestion-agent-role"
    },
    {
      "Sid": "PassRoleToLambda",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/outfit-suggestion-agent-role",
      "Condition": { "StringEquals": { "iam:PassedToService": "lambda.amazonaws.com" } }
    },
    {
      "Sid": "LambdaDeployAndInvoke",
      "Effect": "Allow",
      "Action": [
        "lambda:GetFunction",
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:outfit-suggestion-agent"
    }
  ]
}
```

Note the two extra `RoleBootstrap` actions compared to the weather Lambda
repos (`iam:DeleteRolePolicy`, `iam:UpdateAssumeRolePolicy`) and the extra
`LambdaDeployAndInvoke` action (`lambda:UpdateFunctionConfiguration`) —
all required specifically by the legacy-role-correction and
environment-variable-update logic this repo's deploy script needs that
the others don't.

## Next component

Once this is green, move on to `weather-orchestrator-lambda` — the final
Style 2 component, which needs both weather Lambdas and this one to
already exist, since it `lambda:Invoke`s all three directly (no Gateway,
no MCP, just `boto3.client('lambda').invoke()` for each).
