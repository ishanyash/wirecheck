"""Bug 1: a direct call that still sends temperature."""

import anthropic


def summarize(client: anthropic.Anthropic, text: str) -> str:
    reply = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        temperature=0.0,  # 400 under 2026-10-21, even though it looks harmless
        messages=[{"role": "user", "content": f"Summarize:\n\n{text}"}],
    )
    return reply.content[0].text
