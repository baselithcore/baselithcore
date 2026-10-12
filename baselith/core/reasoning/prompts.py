"""
Prompts for Tree of Thoughts (ToT) reasoning.
"""

THOUGHT_GENERATION_PROMPT = """
You are an intelligent agent solving a complex problem.
Your goal is to generate {k} possible next steps (thoughts) from the current state.

PROBLEM:
{problem}

CURRENT STATE:
{state}

INSTRUCTIONS:
1. Generate {k} distinct, valid, and constructive next steps.
2. Each thought should be a single, clear, and actionable step.
3. Do not try to solve the entire problem in one step.
4. Keep thoughts concise.

OUTPUT FORMAT:
Provide the thoughts as a numbered list:
1. [Thought 1]
2. [Thought 2]
...
"""

THOUGHT_EVALUATION_PROMPT = """
You are an expert evaluator.
Your goal is to score the quality of the following thought in solving the given problem.

PROBLEM:
{problem}

THOUGHT TO EVALUATE:
{thought}

INSTRUCTIONS:
1. Assess if this thought is a valid and useful step towards the solution.
2. Consider:
   - Validity: Is it logically sound?
   - Progress: Does it move closer to the goal?
   - Feasibility: Is it actionable?
3. Assign a score between 0.0 and 1.0 (where 1.0 is excellent, 0.0 is useless).

OUTPUT FORMAT:
Return ONLY the numeric score (e.g., 0.85). Do not add any text.
"""
