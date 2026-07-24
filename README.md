# bedrock-slack-ai-chatbot
A Slack AI chatbot application using Amazon Bedrock with thread-based conversation history.

Functions as an HTTP API server using API Gateway.
***
This Project was created with reference to the following:
[Amazon BedrockとSlackで生成AIチャットボットアプリを作る (その2：Lambda＋API Gatewayで動かす)](https://dev.classmethod.jp/articles/amazon-bedrock-slack-chat-bot-part2/)

### Architecture Diagram

```mermaid
flowchart TD
    User -->|"@mention"| Slack

    subgraph AWS
        APIGW[API Gateway]
        LambdaFront[Lambda: slack-ai-chatbot]
        SQS[SQS]
        LambdaBack[Lambda: bedrock-backend]
        DynamoDB[(DynamoDB: conversation history)]
        Bedrock[Amazon Bedrock: Claude]
    end

    Slack -->|Event| APIGW
    APIGW --> LambdaFront
    LambdaFront -->|Enqueue| SQS
    SQS -->|Trigger| LambdaBack
    LambdaBack <-->|Read/Write| DynamoDB
    LambdaBack -->|Invoke| Bedrock
    LambdaBack -->|Reply in thread| Slack
```
***
### Resources Created
When you Terraform Apply this project, it creates the resources and configurations within the 'AWS Cloud' shown in the architecture diagram.
 - API Gateway
 - Lambda Layer
 - Lambda Function
 - Bedrock
 - SQS
 - DynamoDB (conversation history)

For the variables SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET, use the values from your own Slack App.
***
## How To Use

**Mention @app_name to start a conversation.**

The bot replies in-thread and remembers the conversation context within that thread. Mention @app_name again in the same thread to continue the conversation.

```
You:  @bot Pythonのリスト内包表記って何？
Bot:  [スレッドに返信] リスト内包表記は...

You:  (スレッド内) @bot じゃあ辞書版は？
Bot:  (スレッド内) 辞書内包表記は...  ← 前の文脈を踏まえて回答
```

Conversation history is stored per thread in DynamoDB and automatically expires after 7 days.

***
## Why the queue

Slack gives an event endpoint 3 seconds to respond, and a Bedrock call takes far longer,
so the answer has to be produced outside the request. SQS carries that handoff rather
than an asynchronous Lambda invoke because Bedrock quotas are much lower than Lambda's
default concurrency: the event source mapping's `maximum_concurrency` caps how many
requests reach the model at once, `maxReceiveCount` plus the visibility timeout give
throttled requests a real retry, and the dead-letter queue keeps whatever still failed.

The pieces that keep it honest, and what breaks without each:

| Setting | Value | Without it |
|---|---|---|
| `visibility_timeout_seconds` | 180 (6x the function timeout) | A slow invocation has its message redelivered while still running, and the thread gets two answers |
| `message_retention_seconds` (queue) | 900 | A backlog silently deletes the question |
| `message_retention_seconds` (DLQ) | 14 days | Failures vanish before anyone can look at them |
| `maxReceiveCount` | 3 | A hard failure never reaches the handler, so nobody tells the user |
| `maximum_concurrency` | 5 | A burst fans out and every call is throttled at once |

### Where retries actually happen

Queue redelivery cannot arrive sooner than the visibility timeout, which makes it
useless for *answering*: by the time the message comes back, the answer is 180 seconds
late and nobody wants it. So the two kinds of retry are separated.

- **Throttling is retried inside the invocation.** The Bedrock client runs in adaptive
  retry mode, which backs off in seconds and keeps the answer timely. This is the only
  retry that ever produces an answer.
- **Queue redelivery exists to inform, not to answer.** The backend refuses to answer a
  question older than `ANSWER_DEADLINE_SECONDS` (60) and posts a short "I gave up"
  message in the thread instead.

That is why retention is generous while the deadline is tight. They are not the same
knob: retention only decides when a message disappears with nobody noticing, and the
deadline decides whether an answer is still worth having. A question that ages out is
never answered late and never silently dropped — the thread is told once.

Delivery is at-least-once at two points, and both are handled in code rather than
assumed away:

- **Slack retries** a delivery it thinks failed. The frontend recognises the
  `X-Slack-Retry-Num` header, acknowledges, and enqueues nothing.
- **SQS redelivers.** The frontend forwards Slack's `event_id`; the backend takes an
  expiring claim on it in DynamoDB before doing any work, and releases the claim if
  processing fails so the retry is allowed through.

Within a thread, the history item carries a `version` and is written conditionally, so
two mentions racing each other cannot erase one another's turn — the loser fails and is
retried against fresh state. The reply is posted to Slack *before* the turn is recorded:
saving first would mean a failed post retries against a history that already contains
the answer, appending the user's question to the thread twice.

***
## Installation Guide

### Prerequisites
- Terraform installed on your local machine
- AWS CLI configured with appropriate credentials
- A Slack app created with bot token and signing secret

### Steps

1. Clone the repository to your local machine.

2. Navigate to the project directory.

3. Set up Terraform variables:
 - Option A: set variable in HCP Terraform

   ```hcl
   slack_bot_token      = "your-slack-bot-token"
   slack_signing_secret = "your-slack-signing-secret"
   ```
 - Option B: Local environment, e.g., Ubuntu
  
   first, Open variables.tf and change the variable names to uppercase

   then add env var
   
   ```bash
   export SLACK_BOT_TOKEN="your-slack-bot-token"
   export SLACK_SIGNING_SECRET="your-slack-signing-secret"
   ```

    Replace your-slack-bot-token and your-slack-signing-secret with your actual Slack App values.

4. Initialize Terraform:
   ```
   terraform init
   ```

5. Apply the Terraform configuration:
   ```
   terraform apply
   ```

   Review the planned changes and type `yes` when prompted to create the resources.

6. After the apply is complete, note the API Gateway endpoint URL in the Terraform output.

7. Configure Slack:
   - Go to your Slack App's configuration page
   - Navigate to "Event Subscriptions"
   - Enable events
   - In the "Request URL" field, enter: `{your-api-endpoint}/slack/events`
     Replace `{your-api-endpoint}` with the actual API Gateway endpoint URL from step 6.

8. Save the Slack App configuration.

Your Slack AI chatbot should now be ready to use!

***
## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # create .venv and install runtime + dev dependencies
uv run pytest      # run the test suite
uvx ruff check .   # lint
```

### Tests

`tests/` holds unit tests for both Lambda handlers. They run entirely offline — every
AWS and Slack client is replaced with a mock before the handler module is imported, so
no credentials, no network and no LocalStack are needed.

| File | Covers |
|------|--------|
| `tests/test_slack_ai_chatbot_lambda.py` | mention parsing, thread root resolution, the SQS payload |
| `tests/test_bedrock_backend_lambda.py` | conversation history in DynamoDB, the Converse request/response, history trimming, empty input, idempotency claims, version conflicts |

Tests live at the repository root rather than inside the Lambda directories on purpose:
`lambda_function_slack_ai_chatbot/` and `lambda_function_bedrock_backend/` are zipped
verbatim by `data.archive_file`, so anything added there would ship to production and
change `source_code_hash`.

### CI

Every pull request runs:

| Workflow | Check | Source |
|----------|-------|--------|
| Python Test | `uv run pytest` | `.github/workflows/python-test.yml` (this repo) |
| Python CI | `uvx ruff check .` | reusable workflow, managed by Terraform |
| Terraform CI | `terraform fmt` / `tflint` / `trivy` | reusable workflow, managed by Terraform |

To make a green build the condition for merging, add the check names `pytest`, `lint`
and the Terraform CI jobs as **required status checks** on the `main` branch under
*Settings → Branches → Branch protection rules*.
