"""Bug 2: a shared wrapper adds sampling defaults to every call.

The call sites look clean. The bad parameter lives in one shared place.
"""

import anthropic

DEFAULT_PARAMS = {"max_tokens": 512, "top_p": 0.9}


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


class LLM:
    def __init__(self, client: anthropic.Anthropic, **defaults):
        self.client = client
        self.defaults = {**DEFAULT_PARAMS, **defaults}

    def ask(self, prompt: str) -> str:
        reply = self.client.messages.create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": prompt}],
            **self.defaults,
        )
        return reply.content[0].text


def make_llm() -> LLM:
    return LLM(get_client())
