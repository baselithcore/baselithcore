---
name: intent_classification
version: "2"
labels: [production]
description: Semantic intent-classification prompt for the orchestrator.
variables: [intents_list, query]
---
Classify the user's intent from their message.

Available intents:
{{ intents_list }}

User message: "{{ query }}"

Respond ONLY with a JSON object in this exact format:
{
    "intent": "<intent_name>",
    "confidence": <0.0-1.0>
}

Choose the most appropriate intent. If unsure, use lower confidence.
