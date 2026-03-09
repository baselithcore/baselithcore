---
title: Human-in-the-Loop
description: Human intervention for critical decisions and approvals
---

**Module**: `core/human/`

The Human-in-the-Loop module provides robust mechanisms for pausing autonomous execution to request human intervention. This is essential for enterprise safety, especially before executing sensitive actions (e.g., destructive API calls, financial transactions, or production deployments).

---

## Module Structure

```text
core/human/
├── __init__.py           # Public exports
├── approval.py          # HumanApproval handler
├── requests.py          # ApprovalRequest models
└── protocols.py          # HITL interfaces
```

---

## Core Concepts

The system models approvals using structured requests and decisions, allowing agents to confidently halt execution until a human operator provides explicit instructions.

### Example Usage

```python
from core.human import HumanApproval, ApprovalRequest

approval = HumanApproval()

# Define the action that requires approval
if action.is_sensitive:
    request = ApprovalRequest(
        action=action,
        reason="This action will permanently modify production data.",
        timeout_seconds=300  # 5 minutes to approve or reject
    )
    
    # Execution halts here, waiting for async human input
    decision = await approval.request(request)
    
    if decision.approved:
        await execute_action(action)
    else:
        log.info(f"Action rejected by human: {decision.reason}")
```

---

## Implementation Details

!!! info "Escalation Logic"
    The `HumanApproval` handler integrates seamlessly with both the Swarm and the Planning modules, ensuring that any sub-agent attempting a restricted action will escalate the request up the chain until it reaches the human operator interface.

Approvals can be managed via API, allowing custom frontend instances to present interactive approval dashboards to the end users.
