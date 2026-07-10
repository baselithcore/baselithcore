---
name: structured-summary
description: Produce a structured executive summary (context, key points, risks, next steps) from the documents already retrieved in the conversation.
version: 0.1.0
requires_approval: false
tools: []
---

# Structured summary

Use this skill when the user asks for a summary, recap, or executive
overview of the retrieved documents.

## Procedure

1. Read the CONTEXT section only — never invent content that is not there.
2. Extract the essential facts and group them under these exact headings:
   - **Context** — one short paragraph framing the subject.
   - **Key points** — 3 to 7 bullets, each a single factual statement.
   - **Risks / open questions** — bullets for anything uncertain,
     contradictory, or missing from the documents.
   - **Next steps** — bullets only when the documents themselves suggest
     actions; otherwise omit the section.
3. Keep the whole summary under 250 words.
4. Answer in the user's language.

## Constraints

- No sources, file names, or URLs in the output (the app renders them
  separately).
- If the CONTEXT is empty, state that no relevant information was found
  and stop.
