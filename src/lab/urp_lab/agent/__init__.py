"""Task agent: natural-language requests → confirmed task contract → tool calls on the existing services.

The agent never reports work it did not execute. Every state change comes from a tool's real return value, and every
result it quotes is re-checked by an independent verifier (the LLM-Modulo pattern: the model proposes, sound external
checkers decide). Components:

- ``contract``  task contract ``urp.task-contract/1``: typed fields with units, value provenance and change history;
- ``llm``       provider-neutral chat with tool calling (Ollama, OpenAI-compatible, Anthropic), usage and cost records;
- ``tools``     validated tool registry over composer, recommender, solver jobs, independent checker and simulation;
- ``diagnose``  failure classification (implementation error / method not applicable / task infeasible) and repairs;
- ``session``   persistent sessions and the phase-gated runner (understand → confirm → execute → report).
"""

# Research dialogue shares the gateway but has a distinct session schema.
from .research_llm import ModelError, OpenAICompatibleModel, Reply, model_from_env
from .manager import AgentManager, POLICY
from .research_tools import TOOLS
