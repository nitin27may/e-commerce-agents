"""
MAF v1 — Chapter 17: Human-in-the-Loop (Python)

A workflow that pauses mid-execution to ask a human for input, then
resumes with the response. Canonical example: a number-guessing game
where the workflow keeps a secret number and asks the user to guess.

Run interactively:
    python tutorials/17-human-in-the-loop/python/main.py
"""

import asyncio
import pathlib
import random
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._request_info_mixin import response_handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402


# ─────────────── Request / response shapes ───────────────

@dataclass(frozen=True)
class GuessRequest:
    """Sent from the workflow to the caller — contains the prompt."""
    prompt: str


# ─────────────── Executors ───────────────

class GuessingGame(Executor):
    """
    Holds a secret number. On each run it pauses via request_info to ask
    for a guess. When the guess arrives it compares and yields an output.
    """

    def __init__(self, secret: int) -> None:
        super().__init__(id="guessing-game")
        self.secret = secret

    @handler
    async def start(self, prompt: str, ctx: WorkflowContext[None, str]) -> None:
        # Pause the workflow and wait for a human guess. The `int` type tells
        # MAF what shape to expect in the response.
        await ctx.request_info(
            request_data=GuessRequest(prompt=prompt or "Pick a number 1–10:"),
            response_type=int,
        )

    @response_handler
    async def check(
        self,
        request: GuessRequest,
        guess: int,
        ctx: WorkflowContext[None, str],
    ) -> None:
        if guess == self.secret:
            await ctx.yield_output(f"correct! the number was {self.secret}")
        elif guess < self.secret:
            await ctx.yield_output(f"too low — secret was {self.secret}")
        else:
            await ctx.yield_output(f"too high — secret was {self.secret}")


def build_workflow(secret: int):
    game = GuessingGame(secret)
    return WorkflowBuilder(start_executor=game).build()


# ─────────────── Drivers ───────────────

async def run_with_response(secret: int, guess: int) -> str:
    """Run once and feed a canned response when the workflow pauses."""
    workflow = build_workflow(secret)

    # First run — workflow pauses on request_info. Consume the full stream so
    # the workflow's internal run state is cleanly idle before we resume.
    pending_request_id: str | None = None
    first_events = []
    async for event in workflow.run("Pick a number 1–10:", stream=True):
        first_events.append(event)
        if pending_request_id is None and getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)

    assert pending_request_id, "expected a request_info event to pause the workflow"

    # Resume with the canned response. Run returns more events until completion.
    outputs: list[str] = []
    async for event in workflow.run(
        responses={pending_request_id: guess},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                outputs.append(data)
    return outputs[-1] if outputs else ""


async def main() -> None:
    secret = random.randint(1, 10)
    workflow = build_workflow(secret)

    pending_request_id: str | None = None
    prompt_text = ""
    async for event in workflow.run("Pick a number 1–10:", stream=True):
        if getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)
            request_data = getattr(event, "data", None)
            prompt_text = getattr(request_data, "prompt", "") or "Your guess:"
            break

    if not pending_request_id:
        print("Workflow finished without a pause — unexpected.")
        return

    try:
        guess = int(input(f"{prompt_text} ").strip())
    except ValueError:
        print("Not a number; aborting.")
        return

    async for event in workflow.run(
        responses={pending_request_id: guess},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                print(data)


if __name__ == "__main__":
    asyncio.run(main())
