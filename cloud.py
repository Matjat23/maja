"""Maja's optional cloud brain: Claude, for questions the local model is too small for.

It is only used when Matty asks for it (a message starting with "think:" or "search:",
or the brain button), never automatically. Every answer's cost is added up per month,
and Maja stops using it when the monthly budget is reached.

What gets sent: the question, Maja's instructions (which include Matty's notes, tasks and
matching document excerpts) and the recent chat. Needs the anthropic package and an API
key in the ANTHROPIC_API_KEY environment variable.
"""

import os
from datetime import datetime

from brain import BrainError

try:
    import anthropic
    CLOUD_AVAILABLE = True
except ImportError:
    CLOUD_AVAILABLE = False

DEFAULT_MODEL = "claude-opus-5-5"
# US dollars per million tokens: (input, output).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_PRICE = 0.01  # Per search ($10 per 1,000).
DEFAULT_BUDGET = 5.00    # Per month, unless Matty changes it.


def has_credentials():
    return CLOUD_AVAILABLE and bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def cost_of(model, usage):
    """What one answer cost in dollars, from the usage the API reports."""
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000 \
        + searches * WEB_SEARCH_PRICE


class CloudBrain:
    def __init__(self, settings, model=None):
        """settings is Maja: it stores the budget and what was spent each month."""
        self.settings = settings
        self.model = model or os.getenv("MAJA_CLOUD_MODEL", DEFAULT_MODEL)
        self._client = None
        self.last_cost = 0.0

    @property
    def budget(self):
        return float(self.settings.setting("cloud_budget", DEFAULT_BUDGET))

    def spent_this_month(self, now=None):
        month = f"{now or datetime.now():%Y-%m}"
        return float(self.settings.setting("cloud_spend", {}).get(month, 0.0))

    def _record(self, cost, now=None):
        month = f"{now or datetime.now():%Y-%m}"
        spend = dict(self.settings.setting("cloud_spend", {}))
        spend[month] = round(spend.get(month, 0.0) + cost, 6)
        self.settings.change_setting("cloud_spend", spend)

    @property
    def usable(self):
        """True when a question could be sent now (package, key and budget all fine)."""
        return has_credentials() and self.spent_this_month() < self.budget

    def chat(self, messages, web=False):
        return "".join(self.chat_stream(messages, web=web)).strip()

    def chat_stream(self, messages, web=False):
        """Like OllamaBrain.chat_stream: yields the answer in pieces. With web=True,
        Claude may search the web (a few searches at most) before answering."""
        if not CLOUD_AVAILABLE:
            raise BrainError("Thinking harder needs the anthropic package. In a terminal, run: "
                             "py -m pip install -r requirements.txt, then restart me.")
        if not has_credentials():
            raise BrainError("Thinking harder needs a Claude API key. See 'Cloud brain' in the "
                             "README for how to add one.")
        if self.spent_this_month() >= self.budget:
            raise BrainError(f"This month's cloud budget of ${self.budget:.2f} is used up, so I "
                             "answered nothing. Type 'cloud budget' and a number to change it.")
        if self._client is None:
            self._client = anthropic.Anthropic()

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        conversation = [{"role": m["role"], "content": m["content"]}
                        for m in messages if m["role"] in ("user", "assistant")]
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}] if web else []
        self.last_cost = 0.0
        try:
            # A server-side web search can pause a long turn; it's continued up to 3 times.
            for _ in range(4):
                with self._client.beta.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    system=system,
                    messages=conversation,
                    tools=tools,
                    output_config={"effort": "high"},
                    # If Claude declines a request for safety reasons, Anthropic's
                    # recommended fallback model answers instead.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    yield from stream.text_stream
                    final = stream.get_final_message()
                cost = cost_of(self.model, final.usage)
                self.last_cost += cost
                self._record(cost)
                if final.stop_reason == "refusal":
                    yield "\n\nI'm sorry, but I can't help with that one."
                    return
                if final.stop_reason != "pause_turn":
                    return
                conversation.append({"role": "assistant", "content": final.content})
        except anthropic.AuthenticationError as error:
            raise BrainError("The Claude API key was refused. Check ANTHROPIC_API_KEY.") from error
        except anthropic.PermissionDeniedError as error:
            raise BrainError("The Claude API key isn't allowed to do this (for example web "
                             "search may be turned off in the Claude Console).") from error
        except anthropic.RateLimitError as error:
            raise BrainError("Claude is busy right now (rate limit). Try again in a minute.") from error
        except anthropic.APIStatusError as error:
            raise BrainError(f"Claude returned an error ({error.status_code}). Try again later.") from error
        except anthropic.APIConnectionError as error:
            raise BrainError("I can't reach Claude. Check the internet connection.") from error
