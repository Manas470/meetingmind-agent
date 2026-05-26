# MeetingMind — Complete Project Walkthrough

**Author: Venkatamanas Raghupatruni**
**Date: May 2026**
**Repo: [github.com/Manas470/meetingmind-agent](https://github.com/Manas470/meetingmind-agent)**

---

## Why I Built This

I've been in a lot of meetings. Too many. And the part that always bothered me wasn't the meetings themselves — it was what happened after.

Someone (usually me) would go back to their desk, spend 20–30 minutes writing up notes, try to remember who said they'd own what, paste a wall of text into Slack, and hope people actually read it. Half the time action items got dropped because nobody was sure who owned them. Blockers got buried in meeting notes that nobody opened after day one.

I kept thinking: **this is exactly the kind of repetitive, structured, high-stakes work that AI should be handling.**

So I built MeetingMind — an autonomous agent that takes a meeting transcript and runs a full pipeline: extract action items with owners and deadlines, detect blockers, create Jira tickets for each action item, send a personalized follow-up email to every attendee with *only their tasks*, and post a rich summary to Slack. All before anyone finishes their coffee after the call.

This isn't a toy. I built it to be production-ready — async, fault-isolated, tested, and deployable to Railway in one command.

---

## The Problem I Was Actually Solving

Let me be specific about why existing solutions didn't cut it.

**Notion AI / meeting bots** give you a summary. A wall of text. You still have to manually pull out action items, manually assign them, manually create tickets, manually draft emails. The "AI" just transcribed better.

**Manual process** is slow, error-prone, and inconsistent. I've seen critical action items get dropped because they were buried on line 47 of a Slack message nobody scrolled to.

**What I wanted:** zero human effort post-meeting. You walk out of the call, and by the time you open Jira, your tickets are already there. By the time you check your email, you've already got your tasks. The AI has already done the work.

The gap wasn't AI capability — Claude can absolutely extract structured data from a conversation. The gap was **pipeline engineering**: connecting the AI output to the tools teams actually use, reliably, without one service going down and killing everything.

---

## Architecture Overview

```
Transcript Input (manual / Zoom webhook / Google Meet webhook)
         │
         ▼
┌─────────────────────────────────────────┐
│         ExtractionAgent (Claude)        │
│  LangChain + claude-sonnet-4-6          │
│                                         │
│  Output: MeetingAnalysis                │
│  - action items (owner, deadline, pri)  │
│  - blockers (who's blocked, by what)    │
│  - decisions                            │
│  - follow-up topics                     │
│  - executive summary                    │
└──────────────────┬──────────────────────┘
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
      JiraClient  Gmail   Slack
      (tickets)  (emails) (post)
          │        │        │
          └────────┴────────┘
                   │
          MeetingProcessResponse
          (persisted to SQLite/PostgreSQL)
```

**Key design principle:** every integration step is independently fault-isolated. If Jira returns a 503, the email step still runs. If Slack's API is flaky, Jira tickets and emails still complete. The pipeline always returns whatever it managed to accomplish, with a clear record of what succeeded and what failed.

---

## Stack Decisions and Why

### FastAPI over Flask/Django

FastAPI gives you async-native request handling out of the box. Since the pipeline makes multiple external API calls (Anthropic, Jira, Gmail, Slack) that can each take seconds, I needed true async I/O, not thread-pool workarounds. FastAPI's Pydantic integration also meant my request/response validation was automatic — I defined the models once and got both the API docs and the runtime validation for free.

### LangChain over raw Anthropic SDK

I went back and forth on this. The raw SDK is simpler and I control exactly what goes over the wire. LangChain adds abstraction that can feel like magic-you-don't-control.

I chose LangChain because:
1. `ChatAnthropic` gives me a clean async `.ainvoke()` interface
2. The retry and error handling patterns are well-tested
3. If I want to swap Claude for GPT-4 or a local model in the future, it's one line change

The key is I'm not using LangChain's agent loop or memory systems — I'm using it as a thin async wrapper around the Claude API. That keeps the complexity low.

### SQLAlchemy async (not SQLModel, not raw SQL)

I wanted async database access without writing raw SQL, but I also wanted to stay close to standard SQLAlchemy so the codebase doesn't get locked into an ORM-specific DSL. `AsyncSession` with `mapped_column` type annotations gives you the best of both worlds — IDE autocomplete, type safety, and full async support.

SQLite for dev, PostgreSQL (via `asyncpg`) for Railway prod. Same codebase, different connection string.

### Pydantic v2 for all domain models

Every piece of data in this system — from the API request to the AI output to the database record to the API response — passes through a Pydantic model. This means I catch shape errors at the boundary, not deep inside business logic. When Claude returns slightly-wrong JSON, the validation layer catches it and the retry kicks in, instead of a `KeyError` crashing the pipeline three steps later.

---

## Technical Bottlenecks I Hit (And Exactly How I Solved Them)

### Bottleneck 1: Getting structured output from Claude, reliably

The naive prompt gives you JSON... most of the time. But "most of the time" is not good enough for a production pipeline. The issues:

- Claude sometimes wraps JSON in markdown fences (` ```json ... ``` `)
- Priority values sometimes come back as `"SUPER_URGENT"` instead of the enum values I defined
- Owner names sometimes come back as `"Bob"` when the attendee is `"Bob Smith"`
- Dates come back as `"by end of day"` instead of `"2026-05-25"`

**How I solved it:**

1. **Fence stripping** — before parsing, run `re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())` to strip any markdown wrapper Claude adds

2. **Priority fallback** — wrap the enum parse in a try/except and fall back to `Priority.MEDIUM` on unknown values instead of crashing

3. **Fuzzy owner resolution** — after extraction, run each owner name through a substring match against the attendees list to resolve `"Bob"` → `"Bob Smith"` → `bob@company.com`

4. **Retry with tenacity** — the extraction method has `@retry(stop=stop_after_attempt(3), wait=wait_exponential(...))`. If JSON parsing fails, it retries the whole LLM call. Three attempts is enough — if Claude can't return valid JSON in three tries with a good prompt, something is wrong with the prompt, not the model.

5. **Strict system prompt** — the system prompt explicitly says: `Return ONLY valid JSON, no markdown fences, no commentary`. Sounds obvious, but it matters.

### Bottleneck 2: Fault isolation in the pipeline

The first version of the pipeline was a straight chain: `extract → jira → email → slack`. If Jira was down, email never sent. If email was slow, Slack had to wait. One failure killed everything downstream.

**How I solved it:**

Each step is wrapped in its own `try/except` that catches any `Exception`, appends the error to `response.errors`, appends a `PipelineStepResult(success=False)` to the results list, and then *continues to the next step*. The pipeline only returns `FAILED` status if AI extraction itself fails — because without the analysis, there's nothing to do. Every integration step is considered optional from the pipeline's perspective.

This means if Jira is having an incident at 10am, your team still gets their emails. If Slack is down, Jira tickets still exist. The system degrades gracefully instead of failing completely.

```python
try:
    jira_results = jira.create_tickets_bulk(...)
except Exception as e:
    response.errors.append(f"Jira step error: {e}")
    response.pipeline_results.append(
        PipelineStepResult(step=PipelineStep.JIRA_TICKETS_CREATED, success=False)
    )
    # pipeline continues — does NOT return here
```

### Bottleneck 3: Testing without burning API credits or requiring real credentials

I needed 23 tests that could run in CI without an Anthropic API key, without a Jira account, without a Slack token. But I also didn't want tests so fake they provide no confidence.

**How I solved it:**

The key insight: mock at the LangChain layer, not the HTTP layer.

```python
with patch("app.agents.extraction_agent.ChatAnthropic") as MockLLM:
    instance = MockLLM.return_value
    instance.ainvoke = AsyncMock(return_value=mock_response)
    # now run the real extraction logic
    result = await agent.extract(transcript, ...)
```

This means:
- The real prompt formatting code runs
- The real JSON parsing and validation runs
- The real owner email resolution runs
- The real priority fallback logic runs
- Only the actual HTTP call to Anthropic is stubbed

The tests are actually testing the business logic, just with a predictable "model response." This is the correct abstraction boundary.

For integration clients (Jira, Gmail, Slack) I stub at the module level using `patch.dict("sys.modules", {...})` for cases where the underlying library (like the `jira` package) isn't installed in CI. The key is that the pipeline's error handling paths still get exercised.

### Bottleneck 4: The `event` keyword conflict in structlog

This one was subtle. I had a log call like:

```python
event = body.get("event", "")
logger.info("zoom.webhook_received", event=event)
```

In structlog, `event` is a reserved keyword — it's the main message field. Passing it as a keyword argument causes a `TypeError: got multiple values for argument 'event'`.

**Fix:** rename the kwarg to `zoom_event=event`. One character change, but it took a test failure to catch it — which is exactly why you write tests.

### Bottleneck 5: Async SQLAlchemy session management

The first version passed a single `AsyncSession` into background tasks. Background tasks in FastAPI outlive the request, which means the session's connection gets returned to the pool before the background task finishes — causing `DetachedInstanceError` and connection pool exhaustion.

**Fix:** background tasks get their own session via the `get_session()` async context manager:

```python
async def _run_pipeline_background(request, meeting_id):
    pipeline = MeetingPipeline()
    result = await pipeline.run(request, meeting_id=meeting_id)
    
    async with get_session() as db:   # fresh session, not the request's session
        record = await db.get(MeetingRecord, meeting_id)
        record.status = result.status.value
        # ...
```

Each background task opens its own connection, uses it, and closes it. No shared state between the request lifecycle and the background work.

### Bottleneck 6: Git operations on a mounted sandbox filesystem

While building, I ran the git commands inside the development sandbox where the project folder is mounted. The Linux sandbox had permission restrictions that blocked git's lock file cleanup:

```
fatal: Unable to create '.git/index.lock': File exists.
rm: cannot remove '.git/index.lock': Operation not permitted
```

**Fix:** copy the project files to `/tmp` (local to the sandbox, no mount permissions issue), run all git operations there, and push to GitHub from `/tmp`. The remote gets the right code regardless of where the local repo lives. For the developer's local machine, `git clone` from GitHub gives them a clean working copy with no filesystem quirks.

---

## What I'm Most Proud Of

**The email personalization.** It would have been easy to send one email to everyone with the full list of action items. That's what every other tool does. Instead, each attendee gets a completely tailored email — only their action items, only blockers that directly affect them, decisions relevant to their work. I ran a second LLM call per attendee specifically to generate this. The compute cost is worth it because the output is actually useful, not a wall of text nobody reads.

**The pipeline result model.** Every step returns a `PipelineStepResult` with `success`, `detail`, and `artifacts` (e.g., `{"jira_keys": ["ENG-230", "ENG-231"]}`). You can look at any meeting's response and see exactly what happened at each step, what was created, and what failed — without digging through logs. That's the kind of observability that matters in production.

**23 tests passing without a single real API call.** Every integration is tested, every failure path is tested, every edge case in the AI parsing is tested — and it all runs in under 1 second on any machine with Python installed.

---

## What I'd Do Differently

**Add a proper job queue from day one.** `FastAPI BackgroundTasks` is fine for low volume, but for anything serious you want Celery + Redis or a managed queue. I kept it simple here to stay focused on the core problem, but I'd swap it out before putting this in front of real users at scale.

**Stream the pipeline results.** Right now the sync endpoint blocks until all steps complete. With Server-Sent Events, you could watch the pipeline run in real time — "AI extraction done ✓ ... Jira tickets created ✓ ... emails sent ✓" — which is a much better UX for a long-running operation.

**Smarter deadline parsing.** Right now I rely on Claude to interpret "by EOW" or "next sprint" into an ISO date. It works most of the time, but a dedicated date parsing library (like `dateparser`) would be more reliable for edge cases.

---

## What's Next

- [ ] Zoom auto-ingestion via webhook (groundwork is there, needs OAuth flow)
- [ ] Google Meet transcript pulling (needs Workspace admin setup)
- [ ] Web UI — upload transcript, watch the pipeline run live
- [ ] Multi-tenant support (per-org Jira/Slack config)
- [ ] GitHub Actions CI pipeline
- [ ] Recurring meeting awareness (track carry-overs across sprints)

---

## Final Thought

The goal of this project was never just to build a cool demo. It was to build something I'd actually use.

Every engineering decision — the fault isolation, the personalized emails, the structured test mocks, the async session management — came from thinking about what it would take to run this reliably for a real team, every day, without babysitting it.

That's the bar I hold myself to.

If you're building something similar or want to talk about any of the technical decisions here, reach out. I'm always up for that conversation.

— **Venkatamanas Raghupatruni**
[github.com/Manas470](https://github.com/Manas470) · [venkatamanasraghupatruni@gmail.com](mailto:venkatamanasraghupatruni@gmail.com)
