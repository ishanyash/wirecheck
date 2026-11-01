"""Bug 1: the call itself still sends temperature.

Since anthropic 1.0.0, `temperature=` is no longer a keyword of messages.create
(passing it is a TypeError, so tests catch it). The SDK 1.0 migration guide said
to pass it through extra_body instead. Under API 2026-10-21 that returns 400.
"""

import anthropic


def summarize(client: anthropic.Anthropic, text: str) -> str:
    reply = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": f"Summarize:\n\n{text}"}],
        extra_body={"temperature": 0.0},  # 400 under 2026-10-21, even though it looks harmless
    )
    return reply.content[0].text
