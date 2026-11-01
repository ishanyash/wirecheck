# WireCheck launch notes

Everything below was measured on 1 Nov 2026 in GitHub Actions. Each number links to the run it came from.

## What it does

WireCheck is a pytest plugin that records every request your tests send to the Claude API, after your wrappers, shared defaults and `extra_body` have been merged in. It fails the test that sent a request that would return 400 under Claude API version `2026-10-21`, and shows the file and line that made the call.

## Who it is for

Python teams that call Claude through the `anthropic` SDK and run pytest in CI. It matters most if:

- your version range lets `anthropic` 2.x in (2.0.0 and later send `2026-10-21` by default),
- your tests mock the API response, so a 400 never shows up in tests,
- sampling settings travel through a wrapper, a defaults dict or `extra_body`, where a grep or type checker does not see them.

## Why now

- 21 Oct 2026: Claude API version `2026-10-21` released; `anthropic` 2.0.0 sends it by default. Under it, `temperature`, `top_p` or `top_k` return 400, even at their default values. [Migration guide](https://platform.claude.com/docs/en/api/migration-2026-10-21)
- `anthropic` 1.0.0 had already removed `temperature=` as a keyword, and its migration guide said to pass it through `extra_body`. That is the path that now fails silently in mocked tests.
- 22 Oct 2026: [Nojoin issue #554](https://github.com/Valtora/Nojoin/issues/554). A wrapper sent `temperature: 0.2` through `extra_body`, the tests mocked the client, and they all passed on 2.x.

## Results, exactly as measured

### Demo (3 bugs, 3 fixes)

`anthropic` 2.3.0, Python 3.12, WireCheck installed from the built wheel. [CI run](https://github.com/ishanyash/wirecheck/actions/runs/41333702096)

| Command | Result |
|---|---|
| `pytest broken` | 3 passed |
| `pytest broken --wirecheck` | 3 failed. 3 requests checked: 3 errors (WC001) |
| `pytest fixed --wirecheck` | 3 passed. 3 requests checked: 0 errors |
| `wirecheck scan demo/broken` (static) | 1 of the 3 bugs found |

The static scan finds only the bug written in the call itself. The other two live in a dict that is unpacked at runtime, and only the recorded request shows them.

### Real repo 1: simonw/llm-anthropic

Commit `b66cdf1`. The repo requires `anthropic>=1.5,<2`. Its tests replay recorded HTTP sessions (cassettes) through the real SDK. [Run](https://github.com/ishanyash/wirecheck/actions/runs/41333704279)

| | Own pin (`anthropic` 1.17.0, API `2023-06-01`) | Forced `anthropic` 2.3.0 (API `2026-10-21`) |
|---|---|---|
| Tests | 123 passed | 94 passed, 29 failed |
| Requests recorded | 39 | 41 |
| WireCheck findings | 33 warnings (WC001) | 35 errors (WC001) |
| Tests with findings | 29 | 29 |
| What is sent | `temperature=1.0` | `temperature=1.0` |
| Call sites | `llm_anthropic.py:1880` (29), `:2027` (4) | `llm_anthropic.py:1880` (31), `:2027` (4) |
| Static scan (37 files) | 0 findings | 0 findings |

- `temperature=1.0` is the plugin's `DEFAULT_TEMPERATURE`, added to `extra_body` on every request unless `top_p` is set. Under `2026-10-21` that returns 400 even though 1.0 is the default.
- Of the 29 failures on 2.3.0, 28 fail only because of WireCheck. One, `test_mcp_server_side_tool`, also fails on its own: its recorded request had `?beta=true` in the URL and the 2.3.0 request does not, so the cassette does not match. The SDK retried, which is why 2 more requests (41 vs 39) appear.
- On its own pin the suite passes and WireCheck only warns: the code works today and breaks when the pin moves to 2.x.

### Real repo 2: pydantic/pydantic-ai

Commit `e286b84`, `tests/models/test_anthropic.py` only. The lockfile pins `anthropic` 1.15.0. [Run](https://github.com/ishanyash/wirecheck/actions/runs/41333704279)

| | Own pin (`anthropic` 1.15.0) | Forced `anthropic` 2.3.0 |
|---|---|---|
| Tests | 433 passed, 2 skipped | 432 passed, 1 failed, 2 skipped |
| Requests recorded | 177 | 177 |
| WireCheck findings | 2 warnings (WC001) | 2 errors (WC001) |
| What is sent | `temperature=0.2`, `top_k=40` | `temperature=0.2`, `top_k=40` |
| Test | `test_anthropic_sampling_settings_reach_the_wire` | same |
| Call site | `pydantic_ai_slim/pydantic_ai/models/anthropic/__init__.py:1531` | same |
| Static scan (4,485 files) | 3 warnings (WCS002) | 3 warnings (WCS002) |

- pydantic-ai moves sampling settings into `extra_body` for models that accept them, and drops them for models that reject them. The test above sets `temperature=0.2` and `top_k=40` on `claude-haiku-4-5` and checks they reach the wire, so it is the one place in this file where they are sent.
- The 3 static warnings are in `tests/models/test_anthropic_batch.py:182-184`, a test that puts `X-Api-Key` in `default_headers`. That test does not send a request.

### A bug WireCheck had, found by this test

The first pydantic-ai run recorded 0 requests ([run](https://github.com/ishanyash/wirecheck/actions/runs/41332771399)). pydantic-ai runs its async tests under a checker that raises on blocking calls inside the event loop, and WireCheck called `os.path.abspath` while finding the call site, then hid the error. v0.1.0 does no file system calls in the hook and shows any internal error in the report. The numbers above are from the fixed version.

### Other checks

- Unit and plugin tests: pass on Python 3.10 to 3.13 with `anthropic` 2.3.0, and on Python 3.12 with `anthropic` 1.x. [CI run](https://github.com/ishanyash/wirecheck/actions/runs/41333702096)
- Fast mode rule (WC005, `claude-opus-4-8` with `speed: "fast"`): warning on 2026-11-29, error on 2026-11-30, tested with a frozen clock.
- GitHub Action: posted one findings comment on [test pull request #1](https://github.com/ishanyash/wirecheck/pull/1).

## Known limits (say these if asked)

- It only sees requests that are really built. Tests that replace `client.messages.create` with a `MagicMock` send nothing, so WireCheck sees nothing (it prints a hint when that happens).
- Python and `httpx` / `httpx2` only. No TypeScript, no auto-fix, no production proxy in v0.1.

## 3 screenshots to take

1. The findings comment on [pull request #1](https://github.com/ishanyash/wirecheck/pull/1).
2. The "wirecheck" summary table at the end of the llm-anthropic job on `anthropic` 2.3.0 ([run](https://github.com/ishanyash/wirecheck/actions/runs/41333704279), open that job, expand "pytest --wirecheck", scroll to the end).
3. The GitHub Marketplace listing page (link below).

The README already has the demo terminal image ([docs/screenshot.png](docs/screenshot.png)).

## Links

- Repo: https://github.com/ishanyash/wirecheck
- PyPI: https://pypi.org/project/pytest-wirecheck/
- GitHub Marketplace: https://github.com/marketplace/actions/wirecheck-for-claude-api
- Migration guide: https://platform.claude.com/docs/en/api/migration-2026-10-21

Built by [Ishan Yash](https://ishanyash.com)
