"""Publish targets (design 0001 §5.2/§7) — self-hosted first, cloud by key.

One target shape for both personas: ``base_url`` names the syntropy-journal
backend; ``api_key`` (the ``X-Syntropy-Key`` credential) is the ENTIRE
difference between self-hosted and cloud (D11/U3).

Key handling: **the SDK never persists the key.** It is caller-supplied per
session (12-factor — environment or secret manager on the consumer's side),
excluded from ``repr`` so a logged target can't leak it, and only ever
placed on the request header at send time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: The api-key header the journals route checks (design 0001 §5.2).
API_KEY_HEADER = "X-Syntropy-Key"

#: The lhm-publish/v1 route path on every syntropy-journal backend.
PUBLISH_PATH = "/api/lhm/v1/metrics"


class SyntropyTarget(BaseModel):
    """Where to publish (design 0001 §5.2).

    ``public_surface`` declares the target renders on a public/marketed
    surface — an Rx-input metric refuses such targets STRUCTURALLY (§5.3:
    the claim gate raises before any transport).
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(min_length=1)  #: self-hosted: your journals URL
    #: cloud: the commercial credential — never persisted, never repr'd.
    api_key: str | None = Field(default=None, repr=False)
    timeout_s: float = Field(default=10.0, gt=0)
    #: True ⇒ the rendered card is public/marketed (C1b: Rx refuses this).
    public_surface: bool = False

    def publish_url(self) -> str:
        return self.base_url.rstrip("/") + PUBLISH_PATH

    def headers(self) -> dict[str, str]:
        """Request headers — the api key appears here and nowhere else."""
        if self.api_key:
            return {API_KEY_HEADER: self.api_key}
        return {}


__all__ = ["API_KEY_HEADER", "PUBLISH_PATH", "SyntropyTarget"]
