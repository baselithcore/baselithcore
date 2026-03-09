"""
Baselith-Core Collaboration System.

Demonstrates collaborative agent patterns: Researcher, Writer, Reviewer.
"""

import asyncio
import yaml
from core.observability.logging import get_logger
from pathlib import Path
from typing import Optional, Any, Dict, List
from dataclasses import dataclass, field
from enum import Enum

# Baselith-Core Imports
from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

logger = get_logger(__name__)


# ============================================================================
# Configuration
# ============================================================================

def load_config(path: str = "config.yaml") -> dict:
    """Load configuration."""
    config_path = Path(__file__).parent / path
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {
        "llm": {"provider": "ollama", "model": "llama3.1:8b"},
        "orchestrator": {"max_iterations": 3, "timeout": 60},
    }


CONFIG = load_config()


# ============================================================================
# Agent Types and State
# ============================================================================

class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    RESEARCHER = "researcher"
    WRITER = "writer"
    REVIEWER = "reviewer"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass
class AgentMessage:
    """Message passed between agents."""
    sender: AgentRole
    recipient: AgentRole
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class TaskContext:
    """Shared context for a task."""
    task_id: str
    objective: str
    status: TaskStatus = TaskStatus.PENDING
    research: list[str] = field(default_factory=list)
    draft: str = ""
    feedback: list[str] = field(default_factory=list)
    final_output: str = ""
    iteration: int = 0
    messages: list[AgentMessage] = field(default_factory=list)


# ============================================================================
# Base Agent
# ============================================================================

class BaseAgent:
    """Base class for all agents."""
    
    role: AgentRole
    description: str = ""
    tools: list[str] = []
    
    def __init__(self, config: dict = None):
        self.config = config or {}
    
    async def think(self, context: TaskContext) -> str:
        """Generate agent's thoughts about current state."""
        raise NotImplementedError
    
    async def act(self, context: TaskContext) -> AgentMessage:
        """Perform agent's action and return message."""
        raise NotImplementedError
    
    def _format_prompt(self, context: TaskContext) -> str:
        """Format prompt for LLM."""
        return f"""Role: {self.role.value}
Objective: {context.objective}
Current Status: {context.status.value}
Iteration: {context.iteration}

Your task: {self.description}"""


# ============================================================================
# Specialized Agents
# ============================================================================

class ResearcherAgent(BaseAgent):
    """Agent specialized in gathering information."""
    
    role = AgentRole.RESEARCHER
    description = "Gather and synthesize information relevant to the task"
    tools = ["web_search", "document_retrieval"]
    
    async def think(self, context: TaskContext) -> str:
        return f"Analyzing objective: {context.objective}. Need to gather relevant information."
    
    async def act(self, context: TaskContext) -> AgentMessage:
        # Simulate research (replace with actual tool calls)
        research_result = f"""Research findings for: {context.objective}

1. Key Point: This is a demonstration of baselith-core collaboration.
2. Important Finding: Agents can work together on complex tasks.
3. Recommendation: Use structured communication between agents.

Sources: [demo-source-1], [demo-source-2]"""
        
        context.research.append(research_result)
        
        return AgentMessage(
            sender=self.role,
            recipient=AgentRole.WRITER,
            content=research_result,
            metadata={"sources_count": 2, "confidence": 0.85},
        )


class WriterAgent(BaseAgent):
    """Agent specialized in content creation."""
    
    role = AgentRole.WRITER
    description = "Create content based on research and objectives"
    tools = ["text_generation"]
    
    async def think(self, context: TaskContext) -> str:
        return f"Have {len(context.research)} research items. Creating draft content."
    
    async def act(self, context: TaskContext) -> AgentMessage:
        # Combine research into draft
        research_summary = "\n".join(context.research)
        
        draft = f"""# {context.objective}

## Overview
Based on the research conducted, here is a comprehensive response.

## Key Findings
{research_summary}

## Conclusion
This draft synthesizes the research findings into actionable content.

---
*Draft version {context.iteration + 1}*"""
        
        context.draft = draft
        
        return AgentMessage(
            sender=self.role,
            recipient=AgentRole.REVIEWER,
            content=draft,
            metadata={"word_count": len(draft.split()), "iteration": context.iteration},
        )


class ReviewerAgent(BaseAgent):
    """Agent specialized in quality review."""
    
    role = AgentRole.REVIEWER
    description = "Review content and provide constructive feedback"
    tools = ["analysis", "feedback"]
    
    async def think(self, context: TaskContext) -> str:
        return f"Reviewing draft of {len(context.draft)} characters."
    
    async def act(self, context: TaskContext) -> AgentMessage:
        # Evaluate draft quality
        approved = context.iteration >= 1  # Approve after first revision
        
        if approved:
            feedback = "APPROVED: The content meets quality standards."
            context.final_output = context.draft
            context.status = TaskStatus.COMPLETED
        else:
            feedback = """REVISION NEEDED:
- Add more specific examples
- Improve the conclusion section
- Cite sources more clearly"""
            context.status = TaskStatus.NEEDS_REVIEW
        
        context.feedback.append(feedback)
        
        return AgentMessage(
            sender=self.role,
            recipient=AgentRole.ORCHESTRATOR,
            content=feedback,
            metadata={"approved": approved, "iteration": context.iteration},
        )


# ============================================================================
# Orchestrator
# ============================================================================

class OrchestratorAgent(BaseAgent, LifecycleMixin, AgentProtocol):
    """Agent that coordinates the workflow."""
    
    role = AgentRole.ORCHESTRATOR
    description = "Coordinate agents and manage workflow"
    
    def __init__(self, agent_id: str, config: dict = None):
        BaseAgent.__init__(self, config)
        LifecycleMixin.__init__(self)
        self.agent_id = agent_id
        self.agents = {
            AgentRole.RESEARCHER: ResearcherAgent(config),
            AgentRole.WRITER: WriterAgent(config),
            AgentRole.REVIEWER: ReviewerAgent(config),
        }
        self.max_iterations = config.get("max_iterations", 3) if config else 3
    
    async def _do_startup(self) -> None:
        """Initialize all sub-agents."""
        logger.info(f"Orchestrator {self.agent_id} starting up...")

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Execute the collaborative workflow.
        """
        if self.state != AgentState.READY:
            return "Orchestrator is not ready."
            
        result_context = await self.run_workflow(input)
        return result_context.final_output or result_context.draft

    async def think(self, context: TaskContext) -> str:
        return f"Orchestrating task. Status: {context.status.value}, Iteration: {context.iteration}"
    
    async def run_workflow(self, objective: str) -> TaskContext:
        """Run the complete agent workflow."""
        context = TaskContext(
            task_id=f"task_{id(self)}",
            objective=objective,
            status=TaskStatus.IN_PROGRESS,
        )
        
        while context.iteration < self.max_iterations:
            # Research phase
            researcher = self.agents[AgentRole.RESEARCHER]
            msg = await researcher.act(context)
            context.messages.append(msg)
            
            # Writing phase
            writer = self.agents[AgentRole.WRITER]
            msg = await writer.act(context)
            context.messages.append(msg)
            
            # Review phase
            reviewer = self.agents[AgentRole.REVIEWER]
            msg = await reviewer.act(context)
            context.messages.append(msg)
            
            # Check if approved
            if context.status == TaskStatus.COMPLETED:
                break
            
            context.iteration += 1
        
        if context.status != TaskStatus.COMPLETED:
            context.status = TaskStatus.FAILED
        
        return context
    
    async def act(self, context: TaskContext) -> AgentMessage:
        return AgentMessage(
            sender=self.role,
            recipient=AgentRole.ORCHESTRATOR,
            content=f"Workflow complete. Status: {context.status.value}",
        )


# ============================================================================
# API Models and Application
# ============================================================================

class TaskRequest(BaseModel):
    """Request to create a new collaborative task."""
    objective: str = Field(..., min_length=10)
    max_iterations: Optional[int] = Field(3, ge=1, le=10)


class TaskResponse(BaseModel):
    """Response with task results."""
    task_id: str
    status: str
    iterations: int
    output: str
    messages: list[dict]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print("🚀 Multi-Agent Collaboration system starting...")
    await orchestrator.initialize()
    yield
    await orchestrator.shutdown()
    print("👋 Multi-Agent Collaboration system shutting down...")


app = FastAPI(
    title="Baselith-Core Collaboration",
    description="Collaborative baselith-core API",
    version="1.0.0",
    lifespan=lifespan,
)

orchestrator = OrchestratorAgent(agent_id="collab-template-system", config=CONFIG.get("orchestrator", {}))


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "agents": list(orchestrator.agents.keys())}


@app.post("/tasks", response_model=TaskResponse)
async def create_task(request: TaskRequest):
    """Create and execute a collaborative task."""
    try:
        output = await orchestrator.execute(
            input=request.objective,
            context={"max_iterations": request.max_iterations}
        )
        
        return TaskResponse(
            task_id=f"task_{id(orchestrator)}",
            status="completed",
            iterations=request.max_iterations or 3,
            output=output,
            messages=[], # Simplified for template
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents")
async def list_agents():
    """List available agents."""
    return {
        "agents": [
            {
                "role": role.value,
                "description": agent.description,
                "tools": agent.tools,
            }
            for role, agent in orchestrator.agents.items()
        ]
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104
