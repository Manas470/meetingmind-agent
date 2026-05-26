# 🧠 MeetingMind Agent

> **Autonomous meeting intelligence — converts transcripts into Jira tickets, personalized follow-up emails, and Slack summaries before the call ends.**

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Claude](https://img.shields.io/badge/Claude-Sonnet_4.6-orange?logo=anthropic)](https://anthropic.com)
[![Tests](https://img.shields.io/badge/Tests-23%2F23_passing-brightgreen)](#testing)
[![Deploy](https://img.shields.io/badge/Deploy-Railway-blueviolet?logo=railway)](https://railway.app)

---

## What It Does

MeetingMind is a production-ready AI agent that turns raw meeting transcripts into structured execution — automatically.

```
Meeting Transcript
      │
      ▼
┌─────────────────────────────┐
│   Claude AI Extraction      │  ← action items · owners · deadlines
│   (LangChain + Anthropic)   │     blockers · decisions · follow-ups
└──────────────┬──────────────┘
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
   Jira     Gmail    Slack
  Tickets  Follow-up  Summary
  (auto)   Emails    Post
           (per-person)
```

**Before the next meeting starts, every attendee has:**
- A Jira ticket created for each of their action items (with priority + deadline)
- A personalized email listing only *their* tasks — not the whole team's
- A Slack message the whole team can reference

---

## Features

- **AI Extraction** — Claude identifies action items, owners, deadlines, blockers, decisions, and follow-up topics with surgical precision
- **Personalized Emails** — each attendee gets a tailored follow-up with only their responsibilities, not a wall of text
- **Jira Integration** — tickets created with correct priority, assignee, due date, and transcript context
- **Slack Block Kit** — rich, structured summaries with emoji priority indicators and Jira ticket links
- **Zoom & Google Meet** — webhook-triggered: transcripts are fetched and processed automatically when a recording completes
- **Async Pipeline** — submit a transcript, get a job ID back instantly; poll for results
- **Fault Isolation** — Jira down? Emails still send. Slack unreachable? Jira tickets still get created
- **Railway-ready** — `railway.toml` + multi-stage Dockerfile included

---

## Architecture

```
meetingmind/
├── app/
│   ├── main.py                      # FastAPI app + lifespan
│   ├── config.py                    # Pydantic Settings (env-driven)
│   ├── agents/
│   │   ├── extraction_agent.py      # Claude LangChain agent
│   │   └── prompts.py               # All LLM prompt templates
│   ├── services/
│   │   ├── pipeline.py              # Master orchestrator
│   │   └── database.py              # Async SQLAlchemy
│   ├── integrations/
│   │   ├── jira_client.py           # Atlassian Jira REST
│   │   ├── gmail_client.py          # SMTP async email
│   │   ├── slack_client.py          # Slack Web API + Block Kit
│   │   ├── zoom_client.py           # Zoom OAuth2 + VTT parser
│   │   └── meet_client.py           # Google Meet + Drive API
│   ├── api/routes/
│   │   ├── meetings.py              # /meetings/* endpoints
│   │   ├── webhooks.py              # /webhooks/zoom, /webhooks/meet
│   │   └── health.py                # /health
│   └── models/
│       ├── schemas.py               # Pydantic v2 domain models
│       └── db_models.py             # SQLAlchemy ORM models
└── tests/                           # 23 tests, all mocked
    └── fixtures/sample_transcript.txt
```

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/meetingmind-agent.git
cd meetingmind-agent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

### 3. Run

```bash
uvicorn app.main:app --reload
# → http://localhost:8000/docs
```

### 4. Process your first meeting

```bash
curl -X POST http://localhost:8000/meetings/process \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Sprint Planning",
    "transcript": "Alice: Bob will own the auth refactor by Friday...",
    "attendees": [
      {"name": "Alice Chen", "email": "alice@company.com", "role": "PM", "is_organizer": true},
      {"name": "Bob Smith",  "email": "bob@company.com",  "role": "Tech Lead"}
    ],
    "send_emails": false,
    "create_jira_tickets": false,
    "post_to_slack": false
  }'
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/meetings/process` | Run full pipeline synchronously |
| `POST` | `/meetings/async` | Submit for background processing, returns `meeting_id` |
| `GET`  | `/meetings/{id}` | Retrieve results for a meeting |
| `GET`  | `/meetings` | List recent meetings |
| `POST` | `/webhooks/zoom` | Zoom recording webhook (auto-fetches transcript) |
| `POST` | `/webhooks/meet` | Google Meet recording webhook |
| `GET`  | `/health` | Liveness probe |
| `GET`  | `/docs` | Swagger UI |

---

## Configuration

All settings are environment variables (see `.env.example`).

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `JIRA_SERVER_URL` | Optional | e.g. `https://company.atlassian.net` |
| `JIRA_USER_EMAIL` | Optional | Jira account email |
| `JIRA_API_TOKEN` | Optional | Jira API token |
| `SMTP_USER` | Optional | Gmail address for sending emails |
| `SMTP_PASSWORD` | Optional | Gmail App Password |
| `SLACK_BOT_TOKEN` | Optional | `xoxb-...` bot token |
| `ZOOM_ACCOUNT_ID` | Optional | For webhook transcript fetching |
| `DATABASE_URL` | Optional | Postgres URL (Railway sets this automatically) |

**Integrations are all optional** — the AI extraction core works without any of them.

---

## Testing

```bash
pytest tests/ -v -p no:cacheprovider
# 23 passed in < 2s (all mocked, no API keys needed)
```

---

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add a **PostgreSQL** addon (Railway auto-sets `DATABASE_URL`)
4. Set `ANTHROPIC_API_KEY` in environment variables
5. Add any optional integration keys (Jira, Slack, etc.)
6. Deploy — Railway uses `railway.toml` automatically

---

## LinkedIn Hook

> *"I built an AI that turns meetings into action. No more 'waiting for notes' — it creates Jira tickets before the call ends."*

Built with: FastAPI · LangChain · Claude · Jira · Gmail · Slack · Zoom · Railway

---

## License

MIT
