"""
Demo script for Reference Product.

Runs the FAQ Agent through its full lifecycle.
"""

import asyncio
import logging
from core.observability.logging import get_logger
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from core.config import get_core_config
from core.lifecycle import AgentState
from examples.reference_product.agent import FAQAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = get_logger("ReferenceDemo")

async def main():
    logger.info("=== Starting Reference Product Demo ===")
    
    # Check deterministic mode
    config = get_core_config()
    logger.info(f"Deterministic Mode: {config.deterministic_mode}")
    
    # 1. Instantiate
    kb = {
        "hello": "Hi there! I'm the Reference Agent.",
        "status": "I am fully operational.",
        "lifecycle": "I follow startup -> ready -> stopped."
    }
    agent = FAQAgent(knowledge_base=kb)
    logger.info(f"State: {agent.state}")  # UNINITIALIZED
    
    # 2. Startup
    logger.info(">>> Calling startup()...")
    await agent.startup()
    logger.info(f"State: {agent.state}")  # READY
    
    # 3. Execution
    logger.info(">>> Executing queries...")
    queries = ["hello", "lifecycle", "unknown question"]
    
    for q in queries:
        try:
            logger.info(f"User: {q}")
            response = await agent.execute(q)
            logger.info(f"Agent: {response}")
        except Exception as e:
            logger.error(f"Execution error: {e}")
            
    # 4. Shutdown
    logger.info(">>> Calling shutdown()...")
    await agent.shutdown()
    logger.info(f"State: {agent.state}")  # STOPPED
    
    logger.info("=== Demo Completed ===")

if __name__ == "__main__":
    asyncio.run(main())
