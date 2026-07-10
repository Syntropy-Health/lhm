"""Publish targets (design 0001 §5.2/§7) — self-hosted first, cloud by key.

One target shape for both personas: ``base_url`` names the syntropy-journal
backend; ``api_key`` (the ``X-Syntropy-Key`` credential) is the ENTIRE
difference between self-hosted and cloud (D11/U3).

Key handling: **the SDK never persists the key.** It is caller-supplied per
session (12-factor — environment or secret manager on the consumer's side),
stored as a pydantic ``SecretStr`` (masked in ``repr``, ``str``, AND every
``model_dump`` mode), and only ever unwrapped at request-header construction
time.

Transport security: ``base_url`` must be ``https`` — the api key rides a
header, and plain ``http`` would send it in cleartext. Plain ``http`` is
allowed ONLY for loopback hosts (self-hosted dev); anything else requires an
explicit ``allow_insecure=True`` opt-in.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

#: The api-key header the journals route checks (design 0001 §5.2).
API_KEY_HEADER = "X-Syntropy-Key"

#: The lhm-publish/v1 route path on every syntropy-journal backend.
PUBLISH_PATH = "/api/lhm/v1/metrics"

#: Hosts where plain http is acceptable without opt-in (self-hosted dev).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class SyntropyTarget(BaseModel):
    """Where to publish (design 0001 §5.2).

    ``public_surface`` declares the target renders on a public/marketed
    surface — an Rx-input metric refuses such targets STRUCTURALLY (§5.3:
    the claim gate raises before any transport).

    ``allow_insecure`` (default ``False``) is the ONLY way to point a target
    at a non-loopback plain-``http`` URL — the ``X-Syntropy-Key`` header
    would otherwise cross the network in cleartext.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(min_length=1)  #: self-hosted: your journals URL
    #: cloud: the commercial credential — never persisted, never repr'd,
    #: never dumped (SecretStr masks every serialization).
    api_key: SecretStr | None = Field(default=None, repr=False)
    timeout_s: float = Field(default=10.0, gt=0)
    #: True ⇒ the rendered card is public/marketed (C1b: Rx refuses this).
    public_surface: bool = False
    #: Explicit opt-in for plain http to a non-loopback host (dev tunnels
    #: and the like) — the api key WILL cross the wire in cleartext.
    allow_insecure: bool = False

    @model_validator(mode="after")
    def _https_or_loopback(self) -> "SyntropyTarget":
        parts = urlsplit(self.base_url)
        if parts.scheme == "https":
            return self
        if parts.scheme == "http":
            host = (parts.hostname or "").casefold()
            if host in _LOOPBACK_HOSTS or self.allow_insecure:
                return self
            raise ValueError(
                "plain http would send the X-Syntropy-Key header in cleartext — "
                "use https, a loopback host (localhost / 127.0.0.1 / [::1]), or "
                "pass an explicit allow_insecure=True"
            )
        raise ValueError("base_url must be an http(s) URL")

    def publish_url(self) -> str:
        return self.base_url.rstrip("/") + PUBLISH_PATH

    def headers(self) -> dict[str, str]:
        """Request headers — the api key is unwrapped here and nowhere else."""
        if self.api_key is not None and self.api_key.get_secret_value():
            return {API_KEY_HEADER: self.api_key.get_secret_value()}
        return {}


__all__ = ["API_KEY_HEADER", "PUBLISH_PATH", "SyntropyTarget"]
