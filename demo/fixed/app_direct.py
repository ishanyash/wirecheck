"""Fixed: no sampling parameters. The model uses its defaults."""

import anthropic


def summarize(client: anthropic.Anthropic, text: str) -> str:
    reply = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": f"Summarize:\n\n{text}"}],
    )
    return reply.content[0].text
