"""Fixed: nothing is smuggled in through extra_body."""

import anthropic


class AnthropicBackend:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def _sampling_kwargs(self) -> dict:
        return {}

    def generate(self, notes: str) -> str:
        reply = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": f"Write meeting notes from:\n\n{notes}"}],
            **self._sampling_kwargs(),
        )
        return reply.content[0].text
