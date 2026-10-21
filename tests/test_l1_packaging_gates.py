"""L1 — R7 packaging gates (PHI/secret scan, license/NOTICE, clean-env).

R7: the published package must be PHI/secret-safe and license-clean.

- **PHI/secret scan** (always-on): every tracked file under ``LHM/`` AND the
  serialized output of every synthetic generator is scanned against the
  pattern table below. Fixtures are synthetic-by-construction; this gate
  catches regressions (a pasted real id, a lookalike email, a token).
- **License/NOTICE gate** (always-on): LICENSE (Apache-2.0) + NOTICE exist;
  NOTICE attributes every runtime dep incl. the fhir-core BSD verification
  (the A&D D2 pre-sign-off item, verified 2026-07-09); pyproject declares
  Apache-2.0; no copyleft dep appears in the graph.
- **Clean-env install + synthesize** (opt-in, ``RUN_PACKAGING_GATES=1``):
  builds the wheel, installs it into a fresh venv, and synthesizes a metric
  path (fixtures → mapping → serialization) with no source tree on path —
  the R7 integration proof. Heavy, so opt-in (release/CI-nightly lane).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LHM_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# PHI / secret pattern table (fail-closed additions welcome; removals need
# a QG note). Patterns target REAL-identifier shapes, not the synthetic ones.
# ---------------------------------------------------------------------------
FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "clerk-user-id": re.compile(r"\buser_[A-Za-z0-9]{16,}\b"),
    "stripe-secret": re.compile(r"\bsk_(live|test)_[A-Za-z0-9]{8,}\b"),
    "stripe-token": re.compile(r"\b(shpat|shpca|shpss|whsec|rk_live)_[A-Za-z0-9]+\b"),
    # (?<![\w.-]) instead of \b so "git@github.com" can't re-match from a
    # mid-token position; git/ssh remotes + placeholder hosts are whitelisted.
    "email": re.compile(
        r"(?<![\w.-])(?!git@)[A-Za-z0-9._%+-]+"
        r"@(?!example\.(com|org)|invalid\b|localhost\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "infisical-cred": re.compile(r"INFISICAL_(CLIENT_SECRET|TOKEN)\s*[=:]\s*\S+"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

_SCAN_EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", "dist", "build", ".git"}

#: Cap per-file read (a stray large binary shouldn't stall the gate; a
#: leaked key never needs more than this to be visible).
_SCAN_MAX_BYTES = 2_000_000


def _scannable_lhm_files() -> list[Path]:
    """Every package file on DISK — filesystem walk of ALL file types.

    Two review findings shaped this: (a) ``git ls-files`` silently skips
    UNTRACKED new files (false green during exactly the pre-commit window
    where it matters most); (b) a suffix allowlist excluded the file types
    that most commonly CARRY a leaked key (.pem/.key/.env/.sh/.cfg), making
    the private-key pattern dead for its likeliest carrier. So: walk disk,
    scan everything as replace-decoded text, cap per-file size."""
    out: list[Path] = []
    for f in sorted(LHM_ROOT.rglob("*")):
        if not f.is_file():
            continue
        if any(part in _SCAN_EXCLUDE_DIRS or part.endswith(".egg-info") for part in f.parts):
            continue
        try:
            if f.stat().st_size > _SCAN_MAX_BYTES:
                continue
        except OSError:
            continue
        out.append(f)
    return out


class TestPhiSecretScan:
    def test_scan_set_is_nonempty_and_includes_the_l1_modules(self) -> None:
        """Self-test: an empty or mis-rooted scan set would make every scan
        pass vacuously. The set must include the mapping + fixtures modules."""
        rel = {str(f.relative_to(LHM_ROOT)) for f in _scannable_lhm_files()}
        assert len(rel) >= 10, f"suspiciously small scan set: {sorted(rel)}"
        for must in (
            "syntropy/lhm/models/mapping.py",
            "syntropy/lhm/fixtures/synthetic.py",
            "NOTICE",
        ):
            assert must in rel, f"scan set missing {must}"

    def test_source_tree_carries_no_phi_or_secrets(self) -> None:
        offenders: list[str] = []
        for f in _scannable_lhm_files():
            try:
                text = f.read_text(errors="replace")
            except (OSError, IsADirectoryError):
                continue
            for name, pat in FORBIDDEN_PATTERNS.items():
                for m in pat.finditer(text):
                    offenders.append(f"{f.relative_to(LHM_ROOT)}: {name}: {m.group(0)[:40]}")
        assert not offenders, "PHI/secret patterns in the package tree:\n" + "\n".join(offenders)

    def test_synthetic_output_carries_no_phi_or_secrets(self) -> None:
        from syntropy.lhm.fixtures import synthetic_order, synthetic_regimen_start, synthetic_series
        from syntropy.lhm.models.mapping import map_order, map_regimen_start, map_series

        resources = []
        for seed in range(5):
            resources.append(map_order(synthetic_order(seed)))
            resources.append(map_order(synthetic_order(seed, is_rx=True)))
            resources.append(map_regimen_start(synthetic_regimen_start(seed)))
            resources.extend(map_series(synthetic_series(seed), sentiment_fn=lambda s: 0.1))
        blob = json.dumps([r.model_dump(exclude_none=True) for r in resources], default=str)
        for name, pat in FORBIDDEN_PATTERNS.items():
            m = pat.search(blob)
            assert m is None, f"synthetic output matched {name}: {m.group(0)[:40]}"

    def test_synthetic_inputs_carry_no_phi_or_secrets(self) -> None:
        """The raw fixture RECORDS too — note text is (by design, C9) never
        serialized, so the output scan above is structurally blind to a PHI
        string planted in a check-in note. Scan the inputs directly."""
        from syntropy.lhm.fixtures import synthetic_order, synthetic_regimen_start, synthetic_series

        blob = "\n".join(
            repr(x)
            for seed in range(5)
            for x in (
                synthetic_order(seed),
                synthetic_order(seed, is_rx=True),
                synthetic_regimen_start(seed),
                synthetic_series(seed),
            )
        )
        for name, pat in FORBIDDEN_PATTERNS.items():
            m = pat.search(blob)
            assert m is None, f"synthetic INPUT matched {name}: {m.group(0)[:40]}"


class TestPatternTableBites:
    """Self-test of FORBIDDEN_PATTERNS (review CRITICAL-2): a broken regex
    makes every scan pass VACUOUSLY — pin that each pattern fires on a
    shaped-but-fake positive, and stays quiet on the sanctioned synthetic
    vocabulary."""

    # Built by CONCATENATION so the shaped positives never exist as
    # literals in this file — the source-tree scan itself would (correctly)
    # flag them otherwise. That failure mode was observed live: the scan
    # caught this table's first draft, which doubles as proof it bites.
    POSITIVES = {
        "clerk-user-id": "user" + "_2NNxat9BcdEfGhIjKlMnOpQr",
        "stripe-secret": "sk_" + "live_ABCDEFGH12345678",
        "stripe-token": "shpat" + "_deadbeefdeadbeef",
        "email": "dev" + "@" + "syntropyhealth.bio",
        "infisical-cred": "INFISICAL_" + "TOKEN=abc123def",
        "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    }

    NEGATIVES = (
        "git@github.com:Syntropy-Health/SyntropyJournal.git",
        "user@localhost",
        "syn-subject-001",
        "https://lhm.syntropyhealth.bio/fhir/codes",
        "user@example.com",
        "noreply@example.org",
        "synthetic check-in note (fixture text, no PHI)",
        "Synthetic Peptide D (fixture)",
    )

    def test_every_pattern_fires_on_its_shaped_positive(self) -> None:
        assert set(self.POSITIVES) == set(FORBIDDEN_PATTERNS), (
            "keep POSITIVES in lockstep with the pattern table"
        )
        for name, sample in self.POSITIVES.items():
            assert FORBIDDEN_PATTERNS[name].search(sample), f"{name} no longer bites: {sample}"

    def test_sanctioned_synthetic_vocabulary_stays_clean(self) -> None:
        for sample in self.NEGATIVES:
            for name, pat in FORBIDDEN_PATTERNS.items():
                assert not pat.search(sample), f"{name} false-positives on {sample!r}"


class TestLicenseNoticeGate:
    def test_license_file_is_apache_2(self) -> None:
        text = (LHM_ROOT / "LICENSE").read_text()
        assert "Apache License" in text and "Version 2.0" in text

    def test_notice_attributes_every_runtime_dep(self) -> None:
        notice = (LHM_ROOT / "NOTICE").read_text()
        for dep in ("fhir.resources", "fhir-core", "pydantic", "pint", "ucumvert"):
            assert dep in notice, f"NOTICE missing attribution for {dep}"
        # The A&D D2 pre-sign-off item: fhir-core's BSD license TEXT was
        # machine-unconfirmed at design time — the NOTICE must record the
        # verification, not just assert BSD.
        assert "fhir-core" in notice and "verified" in notice.lower()
        assert "BSD" in notice

    def test_pyproject_declares_apache_2(self) -> None:
        """PEP 639 form: an SPDX expression plus the files that carry the text.

        PyPI metadata is immutable per version, so the deprecated table form
        (`license = { text = ... }`) and the license classifier were replaced
        BEFORE the first upload (CTO #15700 U1). setuptools removes support
        for the old form in 2027.
        """
        import tomllib

        meta = tomllib.loads((LHM_ROOT / "pyproject.toml").read_text())
        project = meta["project"]
        assert project["license"] == "Apache-2.0"
        assert project["license-files"] == ["LICENSE", "NOTICE"]
        assert not [c for c in project.get("classifiers", []) if c.startswith("License ::")], (
            "a License classifier alongside an SPDX expression is rejected by modern tooling"
        )
        assert any(r.startswith("setuptools>=77") for r in meta["build-system"]["requires"])

    def test_no_copyleft_in_dependency_graph(self) -> None:
        """D2 license posture: BSD/MIT-family only — no (L)GPL anywhere."""
        import importlib.metadata as im

        graph = ["fhir.resources", "fhir_core", "pydantic"]
        for name in graph:
            try:
                meta = im.metadata(name.replace(".", "_"))
            except im.PackageNotFoundError:
                try:
                    meta = im.metadata(name)
                except im.PackageNotFoundError:
                    continue
            lic = (meta.get("License") or "") + " ".join(
                v for k, v in meta.items() if k == "Classifier" and "License" in v
            )
            assert "GPL" not in lic.upper().replace("LGPL", "GPL"), f"copyleft in {name}: {lic}"


@pytest.mark.skipif(
    os.environ.get("RUN_PACKAGING_GATES") != "1",
    reason="clean-env install gate — opt-in via RUN_PACKAGING_GATES=1 (release lane)",
)
class TestCleanEnvInstall:
    def test_wheel_installs_and_synthesizes_in_clean_env(self, tmp_path: Path) -> None:
        """R7 integration proof: build → fresh venv → import syntropy.lhm (no source
        tree on path) → fixtures → mapping → serialize."""
        # The fresh venv FIRST — the host interpreter may be a uv venv with
        # no pip; stdlib venv bootstraps pip via ensurepip, and that pip
        # does both the wheel build and the install.
        venv_dir = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=120)
        vpy = venv_dir / "bin" / "python"
        # uv-managed CPythons can ship without ensurepip having run — make
        # sure the fresh venv actually has pip before building with it.
        probe_pip = subprocess.run(
            [str(vpy), "-m", "pip", "--version"], capture_output=True, timeout=60
        )
        if probe_pip.returncode != 0:
            subprocess.run(
                [str(vpy), "-m", "ensurepip", "--upgrade"],
                check=True,
                capture_output=True,
                timeout=180,
            )
        dist = tmp_path / "dist"
        subprocess.run(
            [str(vpy), "-m", "pip", "wheel", "--no-deps", "-w", str(dist), str(LHM_ROOT)],
            check=True,
            capture_output=True,
            timeout=300,  # network: fetches the setuptools build backend
        )
        wheels = list(dist.glob("syntropy_lhm-*.whl"))
        assert wheels, f"no syntropy_lhm wheel built under {dist} (dist-name drift?)"
        wheel = wheels[0]
        subprocess.run(
            [str(vpy), "-m", "pip", "install", "--quiet", str(wheel)],
            check=True,
            capture_output=True,
            timeout=600,  # network: resolves fhir.resources/pydantic from PyPI
        )
        probe = (
            "import json;"
            "from syntropy.lhm.fixtures import synthetic_series;"
            "from syntropy.lhm.models.mapping import map_series;"
            "obs = map_series(synthetic_series(1), sentiment_fn=lambda s: 0.5);"
            "print(json.dumps(obs[0].model_dump(exclude_none=True), default=str)[:60])"
        )
        result = subprocess.run(
            [str(vpy), "-c", probe],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),  # NOT the source tree
            timeout=120,
        )
        assert "Observation" in result.stdout or "resourceType" in result.stdout
