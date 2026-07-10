"""The LhmError root (D2-6, decision-of-record: ADOPTED).

One catch-all base for every typed SDK refusal — ``except LhmError`` catches
any of the five public exceptions — while each keeps its original stdlib
base, so every pre-existing ``except ValueError`` / ``LookupError`` /
``RuntimeError`` clause keeps working (multiple inheritance, additive-only).
"""

from __future__ import annotations

import pytest

import lhm
from lhm import (
    AmbiguousIntentError,
    ClaimScopeError,
    LhmError,
    MappingError,
    PublishTransportError,
    UnresolvableIntentError,
)

#: (exception, its original stdlib base) — both bases must hold, always.
PUBLIC_ERRORS = (
    (ClaimScopeError, ValueError),
    (MappingError, ValueError),
    (PublishTransportError, RuntimeError),
    (UnresolvableIntentError, LookupError),
    (AmbiguousIntentError, LookupError),
)


@pytest.mark.parametrize("exc_type,stdlib_base", PUBLIC_ERRORS)
def test_every_public_error_is_an_lhm_error_and_keeps_its_stdlib_base(exc_type, stdlib_base):
    assert issubclass(exc_type, LhmError)
    assert issubclass(exc_type, stdlib_base)


def test_instances_are_caught_by_both_bases():
    """Instance-level arm: a raised refusal lands in an ``except LhmError``
    AND in its original stdlib except-clause."""
    instances = (
        ClaimScopeError("x"),
        MappingError("x"),
        PublishTransportError("x"),
        UnresolvableIntentError("input", "q", ()),
        AmbiguousIntentError("outcome", "q", ("A", "B")),
    )
    for err in instances:
        assert isinstance(err, LhmError)
    assert isinstance(instances[0], ValueError)
    assert isinstance(instances[1], ValueError)
    assert isinstance(instances[2], RuntimeError)
    assert isinstance(instances[3], LookupError)
    assert isinstance(instances[4], LookupError)


def test_lhm_error_is_exported_from_the_front_door():
    assert "LhmError" in lhm.__all__
    assert lhm.LhmError is LhmError


def test_lhm_error_is_not_a_stdlib_alias():
    """The root is its own type — catching it must not accidentally catch
    unrelated ValueErrors/LookupErrors."""
    assert LhmError.__bases__ == (Exception,)
    assert not issubclass(ValueError, LhmError)
