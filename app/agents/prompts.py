"""
All LLM prompt templates for the MeetingMind extraction agent.
Centralized here so tweaks don't require touching business logic.
"""

SYSTEM_PROMPT = """You are MeetingMind, an elite meeting intelligence AI.
Your job is to analyze meeting transcripts with surgical precision and extract structured,
actionable intelligence that drives execution.

You ALWAYS:
- Attribute action items to specific, named individuals (not "the team")
- Identify concrete deadlines (parse phrases like "by EOW", "next sprint", "Friday" into ISO dates where possible)
- Distinguish between blockers (preventing progress NOW) and future risks
- Flag decisions that were made vs. topics still under discussion
- Be concise — every word in your output earns its place

You NEVER:
- Invent information not present in the transcript
- Assign action items to people who weren't mentioned in context of that item
- Confuse questions asked with commitments made
"""

EXTRACTION_PROMPT_TEMPLATE = """Analyze the following meeting transcript and extract structured intelligence.

Meeting Title: {meeting_title}
Date: {meeting_date}
Attendees: {attendees_list}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---

Extract and return a JSON object with this exact structure:
{{
  "summary": "<2-3 sentence executive summary>",
  "key_topics": ["<topic1>", "<topic2>"],
  "action_items": [
    {{
      "title": "<imperative verb phrase, e.g. 'Fix authentication bug'>",
      "description": "<what needs to be done and why>",
      "owner": "<name from attendees list, or null if unassigned>",
      "deadline": "<ISO date YYYY-MM-DD, or natural language if date unclear, or null>",
      "priority": "<low|medium|high|critical>",
      "context": "<exact quote or paraphrase from transcript that originated this item>"
    }}
  ],
  "blockers": [
    {{
      "description": "<what is blocking progress>",
      "blocking_owner": "<who is being blocked>",
      "blocker_owner": "<who/what is causing the block>"
    }}
  ],
  "decisions": [
    {{
      "description": "<what was decided>",
      "rationale": "<why this decision was made>",
      "decided_by": "<name or 'group consensus'>"
    }}
  ],
  "follow_up_topics": ["<topic to revisit in next meeting>"],
  "estimated_next_meeting": "<date or frequency, or null>"
}}

Rules:
- action_items must be concrete tasks with a clear definition of done
- If an owner is partially mentioned (e.g., "Bob will handle it"), resolve to their full name from the attendees list
- Priority logic: critical=blocking release/customer, high=sprint-critical, medium=planned work, low=nice-to-have
- Return ONLY valid JSON, no markdown fences, no commentary
"""

FOLLOWUP_EMAIL_TEMPLATE = """You are writing a personalized post-meeting follow-up email for one specific attendee.

Meeting: {meeting_title}
Date: {meeting_date}
Recipient: {recipient_name}

Meeting Summary:
{meeting_summary}

This attendee's action items:
{personal_action_items}

All other action items (for awareness, not their responsibility):
{other_action_items}

Key decisions made:
{decisions}

Blockers relevant to this person:
{relevant_blockers}

Write a professional, warm, concise follow-up email. Format:
- Opening: brief thank-you for attending, 1 sentence
- Your action items: numbered list with deadlines
- Key decisions: brief bullet points
- Relevant blockers: only if this person is involved
- Closing: 1 sentence

Tone: professional but human — not robotic. Do NOT say "as per our discussion" or "hope this email finds you well."
Return ONLY the email body text (no subject line, no "Subject:", just the body).
"""

EMAIL_SUBJECT_TEMPLATE = "Meeting Notes & Your Action Items — {meeting_title} ({meeting_date})"
"""Subject line template. Format with meeting_title and meeting_date."""
