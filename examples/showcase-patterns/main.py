"""
Showcase Patterns - Demonstration of Agentic Design Patterns.

This example showcases all patterns.
"""


import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn


# ============================================================================
# Pattern 1: Memory (Short-term, Long-term, Episodic)
# ============================================================================

class MemoryStore:
    """Multi-tier memory system."""
    
    def __init__(self):
        # Short-term: Current session context
        self.short_term: dict[str, list[dict]] = {}
        # Long-term: Persistent facts
        self.long_term: list[dict] = []
        # Episodic: Past experiences
        self.episodic: list[dict] = []
    
    def add_to_context(self, session_id: str, message: str, role: str = "user"):
        """Add to short-term memory."""
        if session_id not in self.short_term:
            self.short_term[session_id] = []
        self.short_term[session_id].append({
            "role": role,
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })
    
    def get_context(self, session_id: str, limit: int = 10) -> list[dict]:
        """Get conversation context."""
        return self.short_term.get(session_id, [])[-limit:]
    
    def store_fact(self, fact: str, metadata: dict = None):
        """Store in long-term memory."""
        self.long_term.append({
            "id": str(uuid.uuid4()),
            "fact": fact,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        })
    
    def recall_facts(self, query: str, limit: int = 5) -> list[dict]:
        """Recall relevant facts (simple keyword matching)."""
        query_terms = set(query.lower().split())
        scored = []
        for fact in self.long_term:
            fact_terms = set(fact["fact"].lower().split())
            overlap = len(query_terms & fact_terms)
            if overlap > 0:
                scored.append((overlap, fact))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:limit]]
    
    def record_episode(self, description: str, outcome: str, lesson: str):
        """Record an experience in episodic memory."""
        self.episodic.append({
            "id": str(uuid.uuid4()),
            "description": description,
            "outcome": outcome,
            "lesson": lesson,
            "timestamp": datetime.now().isoformat(),
        })
    
    def recall_similar_episodes(self, situation: str) -> list[dict]:
        """Recall similar past experiences."""
        terms = set(situation.lower().split())
        relevant = []
        for ep in self.episodic:
            ep_terms = set(ep["description"].lower().split())
            if terms & ep_terms:
                relevant.append(ep)
        return relevant[-5:]


# ============================================================================
# Pattern 2: Planning
# ============================================================================

@dataclass
class Plan:
    """A structured plan with steps."""
    goal: str
    steps: list[dict] = field(default_factory=list)
    status: str = "pending"


class Planner:
    """Planning pattern implementation."""
    
    def create_plan(self, goal: str) -> Plan:
        """Decompose goal into actionable steps."""
        # Simulate planning (replace with LLM-based planning)
        steps = [
            {"step": 1, "action": "Analyze the goal", "status": "pending"},
            {"step": 2, "action": "Identify required resources", "status": "pending"},
            {"step": 3, "action": "Execute main task", "status": "pending"},
            {"step": 4, "action": "Verify results", "status": "pending"},
            {"step": 5, "action": "Report completion", "status": "pending"},
        ]
        return Plan(goal=goal, steps=steps)
    
    def execute_step(self, plan: Plan, step_num: int) -> dict:
        """Execute a single step."""
        if 0 < step_num <= len(plan.steps):
            step = plan.steps[step_num - 1]
            step["status"] = "completed"
            step["result"] = f"Completed: {step['action']}"
            return step
        return {"error": "Invalid step"}
    
    def replan(self, plan: Plan, feedback: str) -> Plan:
        """Adjust plan based on feedback."""
        # Add recovery step
        plan.steps.append({
            "step": len(plan.steps) + 1,
            "action": f"Recovery: {feedback}",
            "status": "pending",
        })
        return plan


# ============================================================================
# Pattern 3: Reflection
# ============================================================================

class Reflector:
    """Self-reflection pattern implementation."""
    
    def reflect_on_output(self, output: str, criteria: list[str] = None) -> dict:
        """Evaluate output against criteria."""
        criteria = criteria or ["accuracy", "completeness", "clarity"]
        
        # Simulate reflection (replace with LLM evaluation)
        evaluation = {}
        for criterion in criteria:
            # Demo scoring
            score = 0.8 if len(output) > 50 else 0.5
            evaluation[criterion] = {
                "score": score,
                "feedback": f"The {criterion} is {'good' if score > 0.7 else 'needs improvement'}",
            }
        
        overall = sum(e["score"] for e in evaluation.values()) / len(evaluation)
        
        return {
            "output": output[:200],
            "criteria_scores": evaluation,
            "overall_score": overall,
            "needs_revision": overall < 0.7,
            "suggestions": ["Add more detail"] if overall < 0.7 else [],
        }
    
    def iterative_refinement(self, initial: str, max_iterations: int = 3) -> dict:
        """Iteratively refine output through reflection."""
        current = initial
        history = []
        
        for i in range(max_iterations):
            reflection = self.reflect_on_output(current)
            history.append({"iteration": i + 1, "reflection": reflection})
            
            if not reflection["needs_revision"]:
                break
            
            # Simulate improvement
            current = current + f"\n[Iteration {i+2}: Added detail based on feedback]"
        
        return {
            "final_output": current,
            "iterations": len(history),
            "history": history,
        }


# ============================================================================
# Pattern 4: Human-in-the-Loop
# ============================================================================

@dataclass
class ApprovalRequest:
    """Request for human approval."""
    id: str
    action: str
    context: dict
    status: str = "pending"  # pending, approved, rejected
    reviewer: str = None
    decision_time: str = None


class HumanInteraction:
    """Human-in-the-loop pattern implementation."""
    
    def __init__(self):
        self.pending_approvals: dict[str, ApprovalRequest] = {}
    
    def request_approval(self, action: str, context: dict) -> ApprovalRequest:
        """Create an approval request."""
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            action=action,
            context=context,
        )
        self.pending_approvals[request.id] = request
        return request
    
    def approve(self, request_id: str, reviewer: str) -> ApprovalRequest:
        """Approve a request."""
        if request_id not in self.pending_approvals:
            raise ValueError(f"Request {request_id} not found")
        
        request = self.pending_approvals[request_id]
        request.status = "approved"
        request.reviewer = reviewer
        request.decision_time = datetime.now().isoformat()
        return request
    
    def reject(self, request_id: str, reviewer: str, reason: str = "") -> ApprovalRequest:
        """Reject a request."""
        if request_id not in self.pending_approvals:
            raise ValueError(f"Request {request_id} not found")
        
        request = self.pending_approvals[request_id]
        request.status = "rejected"
        request.reviewer = reviewer
        request.context["rejection_reason"] = reason
        request.decision_time = datetime.now().isoformat()
        return request
    
    def list_pending(self) -> list[ApprovalRequest]:
        """List pending approvals."""
        return [r for r in self.pending_approvals.values() if r.status == "pending"]


# ============================================================================
# Pattern 5: Active Learning
# ============================================================================

class FeedbackCollector:
    """Active learning through feedback collection."""
    
    def __init__(self):
        self.feedback_log: list[dict] = []
    
    def collect_feedback(
        self, 
        query: str, 
        response: str, 
        rating: int,  # 1-5
        comment: str = ""
    ) -> dict:
        """Collect user feedback on a response."""
        entry = {
            "id": str(uuid.uuid4()),
            "query": query,
            "response": response[:200],
            "rating": rating,
            "comment": comment,
            "timestamp": datetime.now().isoformat(),
        }
        self.feedback_log.append(entry)
        return entry
    
    def analyze_feedback(self) -> dict:
        """Analyze collected feedback for improvements."""
        if not self.feedback_log:
            return {"message": "No feedback collected yet"}
        
        avg_rating = sum(f["rating"] for f in self.feedback_log) / len(self.feedback_log)
        low_rated = [f for f in self.feedback_log if f["rating"] <= 2]
        
        return {
            "total_feedback": len(self.feedback_log),
            "average_rating": round(avg_rating, 2),
            "low_rated_count": len(low_rated),
            "improvement_areas": [f["comment"] for f in low_rated if f["comment"]],
        }


# ============================================================================
# API Models
# ============================================================================

class MemoryRequest(BaseModel):
    session_id: str = "default"
    message: str
    store_as_fact: bool = False


class PlanRequest(BaseModel):
    goal: str


class ReflectionRequest(BaseModel):
    output: str
    criteria: list[str] = ["accuracy", "completeness", "clarity"]


class ApprovalActionRequest(BaseModel):
    action: str
    context: dict = {}


class ApprovalDecisionRequest(BaseModel):
    request_id: str
    approved: bool
    reviewer: str = "admin"
    reason: str = ""


class FeedbackRequest(BaseModel):
    query: str
    response: str
    rating: int = Field(..., ge=1, le=5)
    comment: str = ""


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Agentic Patterns Showcase",
    description="Demonstration of all Agentic Design Patterns",
    version="1.0.0",
)

# Initialize pattern implementations
memory = MemoryStore()
planner = Planner()
reflector = Reflector()
human_loop = HumanInteraction()
feedback_collector = FeedbackCollector()


@app.get("/")
async def root():
    """Overview of available patterns."""
    return {
        "title": "Agentic Patterns Showcase",
        "patterns": {
            "memory": "/remember - Short-term, Long-term, Episodic memory",
            "planning": "/plan - Task decomposition and execution",
            "reflection": "/reflect - Self-evaluation and refinement",
            "human_in_loop": "/approve - Human approval workflows",
            "active_learning": "/feedback - Feedback collection and learning",
        },
    }


@app.post("/remember")
async def demonstrate_memory(request: MemoryRequest):
    """Demonstrate memory patterns."""
    # Add to short-term
    memory.add_to_context(request.session_id, request.message)
    
    # Optionally store as fact
    if request.store_as_fact:
        memory.store_fact(request.message)
    
    # Recall relevant context
    context = memory.get_context(request.session_id)
    facts = memory.recall_facts(request.message)
    episodes = memory.recall_similar_episodes(request.message)
    
    return {
        "pattern": "Memory (Short-term, Long-term, Episodic)",
        "short_term_context": context,
        "relevant_facts": facts,
        "similar_episodes": episodes,
    }


@app.post("/plan")
async def demonstrate_planning(request: PlanRequest):
    """Demonstrate planning pattern."""
    plan = planner.create_plan(request.goal)
    
    # Execute first step as demo
    result = planner.execute_step(plan, 1)
    
    return {
        "pattern": "Planning",
        "goal": plan.goal,
        "steps": plan.steps,
        "executed_step": result,
    }


@app.post("/reflect")
async def demonstrate_reflection(request: ReflectionRequest):
    """Demonstrate reflection pattern."""
    result = reflector.iterative_refinement(request.output)
    
    return {
        "pattern": "Reflection",
        "original": request.output[:100],
        "final": result["final_output"][:200],
        "iterations": result["iterations"],
        "history": result["history"],
    }


@app.post("/approve/request")
async def request_approval(request: ApprovalActionRequest):
    """Create approval request (Human-in-the-Loop)."""
    approval = human_loop.request_approval(request.action, request.context)
    
    return {
        "pattern": "Human-in-the-Loop",
        "approval_request": {
            "id": approval.id,
            "action": approval.action,
            "status": approval.status,
        },
    }


@app.post("/approve/decide")
async def decide_approval(request: ApprovalDecisionRequest):
    """Approve or reject request."""
    try:
        if request.approved:
            result = human_loop.approve(request.request_id, request.reviewer)
        else:
            result = human_loop.reject(request.request_id, request.reviewer, request.reason)
        
        return {
            "pattern": "Human-in-the-Loop",
            "decision": result.status,
            "request_id": result.id,
            "reviewer": result.reviewer,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/approve/pending")
async def list_pending_approvals():
    """List pending approvals."""
    pending = human_loop.list_pending()
    return {
        "pattern": "Human-in-the-Loop",
        "pending_count": len(pending),
        "pending": [{"id": p.id, "action": p.action} for p in pending],
    }


@app.post("/feedback")
async def collect_feedback(request: FeedbackRequest):
    """Collect feedback (Active Learning)."""
    entry = feedback_collector.collect_feedback(
        request.query,
        request.response,
        request.rating,
        request.comment,
    )
    
    return {
        "pattern": "Active Learning",
        "feedback_id": entry["id"],
        "recorded": True,
    }


@app.get("/feedback/analysis")
async def analyze_feedback():
    """Analyze collected feedback."""
    analysis = feedback_collector.analyze_feedback()
    
    return {
        "pattern": "Active Learning",
        "analysis": analysis,
    }


@app.post("/episode")
async def record_episode(description: str, outcome: str, lesson: str):
    """Record an episode in episodic memory."""
    memory.record_episode(description, outcome, lesson)
    return {"pattern": "Episodic Memory", "recorded": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
