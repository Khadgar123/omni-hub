"""Self-evolution agent harness for Omni Hub.

This package contains the contract layer for the harness loop:

    task packet
      -> retrieval / context bundle
      -> ensemble generation (N candidates via ccLoad multi-model fanout)
      -> LLM-as-judge scoring
      -> human preference (Argilla)
      -> regression case
      -> DSPy compile -> next-version prompt program

The contract layer is intentionally implemented inside the main repository so
no external fork is required to read/write task packets and generation
records.  External forks (SWE-agent, OpenHands, promptfoo, Argilla, Graphiti,
DSPy, Opik) plug in as data sinks/sources around this contract.
"""

from .models import (
    Candidate,
    GenerationRecord,
    HumanFeedback,
    JudgeScore,
    TaskPacket,
)

__all__ = [
    "Candidate",
    "GenerationRecord",
    "HumanFeedback",
    "JudgeScore",
    "TaskPacket",
]
