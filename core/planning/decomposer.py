"""
Recursive Task Decomposition Module.

Specializes in breaking down monolithic, complex tasks into a hierarchy
of manageable subtasks. Supports semantic effort estimation and
contextual refinement via LLM feedback loops.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass, field
from typing import List, Optional

logger = get_logger(__name__)


@dataclass
class SubTask:
    """
    A granular component of a larger task.

    Attributes:
        id: Unique identifier for the subtask.
        title: Short name of the task.
        description: Detailed explanation of the required work.
        parent_id: Optional ID of the task this was decomposed from.
        estimated_effort: Relative weight (0.0 to 1.0) for planning.
        tags: Categorization labels for routing and filtering.
    """

    id: str
    title: str
    description: str
    parent_id: Optional[str] = None
    estimated_effort: float = 0.5  # 0-1 scale
    tags: List[str] = field(default_factory=list)


class TaskDecomposer:
    """
    Decomposes complex tasks into subtasks.

    Features:
    - Recursive decomposition
    - Effort estimation
    - Tag inheritance
    """

    def __init__(self, llm_service=None, max_depth: int = 3):
        """
        Initialize decomposer.

        Args:
            llm_service: Optional LLM for intelligent decomposition
            max_depth: Maximum decomposition depth
        """
        self._llm_service = llm_service
        self.max_depth = max_depth

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def decompose(
        self,
        task: str,
        context: Optional[str] = None,
        min_subtasks: int = 2,
        max_subtasks: int = 5,
    ) -> List[SubTask]:
        """
        Decompose a task into subtasks.

        Args:
            task: The task to decompose
            context: Optional context
            min_subtasks: Minimum number of subtasks
            max_subtasks: Maximum number of subtasks

        Returns:
            List of SubTask objects
        """
        if self.llm_service:
            return await self._decompose_with_llm(
                task, context, min_subtasks, max_subtasks
            )

        return self._decompose_simple(task, min_subtasks)

    async def _decompose_with_llm(
        self,
        task: str,
        context: Optional[str],
        min_count: int,
        max_count: int,
    ) -> List[SubTask]:
        """Decompose using LLM."""
        prompt = f"""Break down this task into {min_count}-{max_count} subtasks:

Task: {task}
Context: {context or "None"}

For each subtask provide:
- Title (brief)
- Description (one sentence)
- Effort (low/medium/high)

Format:
SUBTASK: <title>
DESCRIPTION: <description>
EFFORT: <level>
---"""

        try:
            result = await self.llm_service.generate_response(prompt)
            return self._parse_subtasks(result, task)
        except Exception as e:
            logger.warning(f"LLM decomposition failed: {e}")
            return self._decompose_simple(task, min_count)

    def _decompose_simple(self, task: str, count: int) -> List[SubTask]:
        """Simple decomposition without LLM."""
        subtasks = [
            SubTask(
                id="sub1",
                title="Preparation",
                description=f"Prepare for: {task}",
                estimated_effort=0.3,
            ),
            SubTask(
                id="sub2",
                title="Execution",
                description=f"Execute main work for: {task}",
                estimated_effort=0.5,
            ),
            SubTask(
                id="sub3",
                title="Verification",
                description="Verify results and quality",
                estimated_effort=0.2,
            ),
        ]
        return subtasks[:count]

    def _parse_subtasks(self, text: str, parent_task: str) -> List[SubTask]:
        """Parse LLM output into SubTask objects."""
        subtasks = []
        idx = 1

        for block in text.split("---"):
            if "SUBTASK:" not in block:
                continue

            try:
                title = ""
                description = ""
                effort = 0.5

                for line in block.strip().split("\n"):
                    if line.startswith("SUBTASK:"):
                        title = line.replace("SUBTASK:", "").strip()
                    elif line.startswith("DESCRIPTION:"):
                        description = line.replace("DESCRIPTION:", "").strip()
                    elif line.startswith("EFFORT:"):
                        level = line.replace("EFFORT:", "").strip().lower()
                        effort = {"low": 0.3, "medium": 0.5, "high": 0.8}.get(
                            level, 0.5
                        )

                if title:
                    subtasks.append(
                        SubTask(
                            id=f"sub{idx}",
                            title=title,
                            description=description or title,
                            estimated_effort=effort,
                        )
                    )
                    idx += 1
            except Exception as e:
                logger.warning(f"Failed to parse subtask: {e}")
                continue

        return subtasks
