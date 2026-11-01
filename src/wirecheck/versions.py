"""Work out which Claude API version a request uses.

Order of trust:
1. The ``anthropic-version`` header on the request itself.
2. The installed ``anthropic`` SDK: 2.0.0 and later send 2026-10-21 by default,
   older versions send 2023-06-01.
3. Fall back to 2023-06-01.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata

NEW_VERSION = "2026-10-21"
OLD_VERSION = "2023-06-01"
FIRST_SDK_MAJOR_ON_NEW = 2


@dataclass(frozen=True)
class Resolution:
    api_version: str
    source: str  # "header", "sdk" or "default"

    @property
    def is_new(self) -> bool:
        return is_at_least_new(self.api_version)


def is_at_least_new(api_version: str) -> bool:
    # API versions are ISO dates, so string order is date order.
    return api_version.strip() >= NEW_VERSION


def installed_sdk_version() -> str | None:
    try:
        return metadata.version("anthropic")
    except metadata.PackageNotFoundError:
        return None


def sdk_major(version: str | None) -> int | None:
    if not version:
        return None
    match = re.match(r"\s*(\d+)", version)
    return int(match.group(1)) if match else None


def sdk_default_api_version(sdk_version: str | None) -> str | None:
    major = sdk_major(sdk_version)
    if major is None:
        return None
    return NEW_VERSION if major >= FIRST_SDK_MAJOR_ON_NEW else OLD_VERSION


def resolve(headers: dict[str, str] | None, sdk_version: str | None) -> Resolution:
    if headers:
        value = headers.get("anthropic-version")
        if value:
            return Resolution(value.strip(), "header")
    from_sdk = sdk_default_api_version(sdk_version)
    if from_sdk:
        return Resolution(from_sdk, "sdk")
    return Resolution(OLD_VERSION, "default")
