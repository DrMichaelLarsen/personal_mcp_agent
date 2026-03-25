# Personal Productivity MCP

A modular Python starter for a schema-aware personal productivity system that exposes opinionated MCP tools and uses LangGraph only for multi-step workflows.

## Included in v1

- Schema-aware task, project, and note creation services
- Conservative project matching with confidence metadata
- Preview-first tagged email processing workflow
- Preview-first day planning workflow
- FastAPI health/capabilities endpoints
- MCP tool/resource registration scaffold
- Fake adapters and tests for core business logic
- Email workflow starter that structures unread/unprocessed email into task-ready page content with summary, action items, and original email body

## Email-to-task workflow direction

The internal workflow engine now has a first-pass email processing design intended for your Gmail inbox workflow:

- fetch unread/unprocessed candidate emails
- classify them
- analyze each email through an AI-analysis service boundary
- generate structured task page content with:
  - summary
  - action items checklist
  - original email content
- map as many task properties as possible
- create or preview the task result

Right now the "AI" analysis step is implemented as a replaceable service boundary with deterministic heuristics so the architecture is ready before wiring a live LLM.

## Running the API

Install the runtime dependencies first:

```bash
pip install -e .[dev]
```

Then run the API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Hosting on Unraid

The easiest way to run this on Unraid is as a Docker container.

### 1. Put the project somewhere on your cache/appdata share

Example:

```text
/mnt/user/appdata/personal-productivity-mcp/
```

Put these files there:
- project source
- `.env`
- optional `docker-compose.yml`

### 2. Create your `.env`

Copy `.env.example` to `.env` and fill in your values.

Important Unraid-style paths:

```env
PPMCP_GMAIL__CREDENTIALS_PATH=/config/secrets/google-oauth-client.json
PPMCP_CALENDAR__CREDENTIALS_PATH=/config/secrets/google-oauth-client.json
```

That means your Google credential JSON should exist on the host at:

```text
/mnt/user/appdata/personal-productivity-mcp/secrets/google-oauth-client.json
```

because the compose file mounts:

```text
/mnt/user/appdata/personal-productivity-mcp/secrets -> /config/secrets
```

### 3. Build and run

From the app folder on Unraid:

```bash
docker compose up -d --build
```

Then open:
- `http://UNRAID-IP:8000/health`
- `http://UNRAID-IP:8000/capabilities`

### 4. n8n integration

From n8n, send requests to:

```text
http://UNRAID-IP:8000/workflows/process-email-preview
```

or

```text
http://UNRAID-IP:8000/workflows/process-email
```

### 5. If you prefer the Unraid Docker UI instead of compose

Use these settings:
- Repository/Build: build from this folder or prebuild the image
- Port mapping: `8000 -> 8000`
- Volume mapping:
  - host: `/mnt/user/appdata/personal-productivity-mcp/secrets`
  - container: `/config/secrets`
  - mode: read-only
- Env file: point to your `.env` if your workflow supports it, or enter the same variables manually

### Notes

- The app is ready to run in Docker now.
- You still need valid Python dependencies inside the container, which the `Dockerfile` handles.
- If you want, the next best step is for me to add an Unraid-friendly prebuilt image workflow or a more explicit `/docs` example set for n8n payloads.

Useful endpoints:
- `GET /health`
- `GET /capabilities`
- `POST /workflows/process-email-preview`
- `POST /workflows/process-email`
- `POST /workflows/process-inbox`

## Sending one email from n8n

Yes — now you can POST a normalized email payload from n8n directly into this app.

Example request to preview a single email:

```json
POST /workflows/process-email-preview
{
  "email": {
    "id": "gmail-message-id-123",
    "thread_id": "gmail-thread-id-123",
    "subject": "Project Alpha: please review this",
    "sender": "person@example.com",
    "body": "Please review this proposal and send feedback by tomorrow.",
    "received_at": "2026-03-24T17:30:00Z",
    "labels": ["INBOX", "UNREAD"]
  },
  "preview_only": true,
  "confidence_threshold": 0.8
}
```

Example n8n flow:
1. Gmail Trigger or Gmail node gets an email
2. Set/Transform node maps it into the JSON shape above
3. HTTP Request node sends it to:
   - `POST http://YOUR-HOST:8000/workflows/process-email-preview`
   - or `POST http://YOUR-HOST:8000/workflows/process-email`

Suggested n8n HTTP Request body:

```json
{
  "email": {
    "id": "{{$json.id}}",
    "thread_id": "{{$json.threadId}}",
    "subject": "{{$json.subject}}",
    "sender": "{{$json.from}}",
    "body": "{{$json.textPlain || $json.snippet}}",
    "received_at": "{{$json.internalDate}}",
    "labels": "{{$json.labelIds}}"
  },
  "preview_only": true,
  "confidence_threshold": 0.8
}
```

Use preview first, then switch to commit mode once you trust the extraction.

## Gmail token requirement for inbox processing

`/workflows/process-inbox` uses the Gmail API adapter directly and requires a refreshable OAuth token file.

Set in `.env`:

```env
PPMCP_GMAIL__CREDENTIALS_PATH=/config/secrets/google-oauth-client.json
PPMCP_GMAIL__TOKEN_PATH=/config/secrets/gmail-token.json
```

Both files should exist on Unraid host under:

```text
/mnt/user/appdata/personal-productivity-mcp/secrets/
```

because that folder is mounted into the container as `/config/secrets`.

If token is missing/expired and cannot refresh, inbox processing will fail with a clear runtime error.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
uvicorn app.main:app --reload
pytest
```

## Configuration with .env

The app loads settings from a `.env` file in the project root, which is a better fit for Unraid, Docker, and other self-hosted setups.

1. Copy `.env.example` to `.env`
2. Fill in your Notion credentials, Google credential file paths, and database IDs
3. Start the app normally

Example:

```bash
cp .env.example .env
uvicorn app.main:app --reload
```

Important notes:
- `PPMCP_NOTION_API_KEY` is your Notion integration secret
- Gmail and Calendar use credential file paths, not a single API key
- On Unraid, mount your credential JSON files into the container and point the `.env` paths at those mounted files
- The task schema is now set up to map your real task fields like `Contexts`, `Importance`, `Scheduled`, `Deadline`, `Assigned`, `Goal`, `dependency_of`, `parent`, and `Time Required`
- The project schema is set up for `Status`, `Area`, `Parent Project`, `Target Deadline`, `Importance`, `Priority` (checkbox), `Budget`, and `Score` sorting

Example Unraid/Docker-style paths:

```env
PPMCP_GMAIL__CREDENTIALS_PATH=/config/secrets/google-oauth-client.json
PPMCP_CALENDAR__CREDENTIALS_PATH=/config/secrets/google-oauth-client.json
```

The settings model uses:
- prefix: `PPMCP_`
- nested separator: `__`

So `PPMCP_TASKS_DB__DATABASE_ID` maps to `settings.tasks_db.database_id`.
