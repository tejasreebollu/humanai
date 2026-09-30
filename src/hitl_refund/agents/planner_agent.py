import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI

from ..config import settings
from ..events import event
from ..prompts import PLANNER_SYSTEM_PROMPT
from ..state import WorkflowState

logger = logging.getLogger(__name__)


def create_plan(request: str, case: dict[str, Any]) -> tuple[list[str], str, str]:
    refund_fallback = (
        ["Verify customer", "Inspect order and policy", "Request human approval"],
        "refund",
    )

    general_fallback = (
        ["Understand the request", "Respond without a sensitive tool"],
        "general",
    )

    fallback = (
        refund_fallback
        if "refund" in request.lower()
        else general_fallback
    )

    if not settings.openrouter_api_key:
        logger.info("OpenRouter key absent; using deterministic planning")
        return (
            *fallback,
            "deterministic fallback (API key not configured)",
        )

    try:
        llm = ChatOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            temperature=0,
        )

        prompt = f"""
{PLANNER_SYSTEM_PROMPT}

Return ONLY valid JSON.

The JSON must have exactly this structure:

{{
  "plan": ["step 1", "step 2", "step 3"],
  "route": "refund"
}}

The "route" value must be either:
- "refund"
- "general"

Request:
{request}

Case:
{json.dumps(case)}
"""

        response = llm.invoke(prompt)

        raw_content = response.content

        # LangChain can sometimes return structured content blocks.
        if isinstance(raw_content, list):
            raw_content = "".join(
                block.get("text", "")
                if isinstance(block, dict)
                else str(block)
                for block in raw_content
            )

        raw_content = str(raw_content).strip()

        logger.info("OpenRouter planner response: %s", raw_content)

        # Remove Markdown JSON fences if the model returns them.
        if raw_content.startswith("```"):
            raw_content = raw_content.replace("```json", "", 1)
            raw_content = raw_content.replace("```", "")
            raw_content = raw_content.strip()

        parsed = json.loads(raw_content)

        plan = parsed.get("plan", fallback[0])
        route = parsed.get("route", fallback[1])

        if not isinstance(plan, list) or not plan:
            plan = fallback[0]

        if route not in {"refund", "general"}:
            route = fallback[1]

        return (
            plan,
            route,
            f"OpenRouter: {settings.openrouter_model}",
        )

    except Exception as exc:
        logger.exception(
            "OpenRouter failed; using deterministic planning: %s",
            exc,
        )

        return (
            *fallback,
            "deterministic fallback (OpenRouter unavailable)",
        )


def planner_agent(state: WorkflowState) -> WorkflowState:
    """Agent 1: create the plan and route the request."""

    plan, route, llm_status = create_plan(
        state["request"],
        state["case"],
    )

    return {
        "plan": plan,
        "route": route,
        "llm_status": llm_status,
        "tool_trace": [],
        "events": [
            event(
                "Planner Agent",
                f"Created a {len(plan)}-step plan and selected {route}",
            )
        ],
    }
