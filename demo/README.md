# WireCheck demo

Three small bugs that all send a sampling parameter to the Claude API. Each one
returns a 400 under API version 2026-10-21. Each one has a test that mocks the
HTTP response, so the tests pass anyway.

| Example | Where the bad parameter is |
|---|---|
| `app_direct.py` | `temperature=0.0` in the call itself |
| `app_factory.py` | `top_p` in a shared defaults dict used by a wrapper class |
| `app_extra_body.py` | `temperature` inside `extra_body` (the Nojoin bug) |

`fixed/` has the same code with the parameter removed.

```bash
pip install -r requirements.txt

pytest broken               # 3 passed  (the gap)
pytest broken --wirecheck   # 3 failed  (WireCheck sees the real payload)
pytest fixed --wirecheck    # 3 passed
```

`wirecheck scan broken` (the static scanner) finds only the first bug. In the
other two the parameter is in a dict that is unpacked at runtime, so you can
only see it in the real request. That is why the pytest plugin exists.
