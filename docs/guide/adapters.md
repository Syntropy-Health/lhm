# Adapters — bring your own data

The `LhmAdapter` is **the only integration you must write**. Three async
readers, each taking your opaque `user_scope` token and returning plain
record shapes — CSV, SQLite, a wearable export, a full app backend, anything
that can fill them:

```python
from typing import Protocol, Sequence

from syntropy.lhm import OrderRecord, RegimenStartRecord, SeriesRecord

class LhmAdapter(Protocol):
    async def read_orders(self, user_scope: str) -> Sequence[OrderRecord]: ...
    async def read_regimen_starts(self, user_scope: str) -> Sequence[RegimenStartRecord]: ...
    async def read_outcome_series(self, user_scope: str) -> Sequence[SeriesRecord]: ...
```

It's a `typing.Protocol` — no base class to inherit; any object with these
three methods qualifies. The adapter owns all I/O; the SDK never sees your
database, your models, or your users.

## The three record shapes

All three are frozen dataclasses importable from `syntropy.lhm`:

**`OrderRecord`** — one purchase/order event (the metric's *input* side):

| Field | Type | Notes |
|---|---|---|
| `subject_ref` | `str` | opaque subject token — see the privacy posture below |
| `item_display` | `str` | what was ordered ("Magnesium glycinate") |
| `occurred_at` | `datetime` | **must be tz-aware** |
| `is_rx` | `bool = False` | the Rx-tier bit — see below |
| `code` | `str = ""` | curated-table key (`"supplement-order"`, `"rx-order"`, …) |
| `source` | `str = ""` | provenance tag ("stripe", "prescription", "csv", …) |

**`RegimenStartRecord`** — when a regimen actually began (the alignment
anchor; distinct from the purchase date):

| Field | Type | Notes |
|---|---|---|
| `subject_ref` | `str` | opaque token |
| `substance_display` | `str` | what was started |
| `started_at` | `datetime` | **must be tz-aware** |
| `code` | `str = ""` | curated-table key |
| `dose_value` / `dose_unit` | `float \| None` / `str` | UCUM unit code (e.g. `"mg"`); optional |

**`SeriesRecord`** — one outcome series (the metric's *outcome* side), with
`points: Sequence[SeriesPoint]` where each point carries a tz-aware
`observed_at` plus either a numeric `value` (journal/wearable sample) or a
`note` (check-in text — see the privacy posture). The record's `code`
selects the outcome kind downstream: `"wearable-metric"` →
`wearable_series`, `"checkin-sentiment"` → `checkin_sentiment`, anything
else → `journal_series`.

## The rules your adapter must honor

**Timezone-awareness is a hard boundary.** Every `datetime` crossing into
the SDK must carry a tzinfo. `fetch` (and the NL binder) enforce this at the
boundary with a labeled, typed refusal — never a bare `TypeError` from
naive/aware math deep in the pipeline:

```python
import asyncio
from datetime import datetime
from syntropy.lhm import MappingError, OrderRecord, fetch

naive = OrderRecord(
    subject_ref="demo-subject-001",
    item_display="Magnesium glycinate",
    occurred_at=datetime(2026, 1, 10),   # no tzinfo — refused
)

class OneOrder:
    async def read_orders(self, user_scope): return [naive]
    async def read_regimen_starts(self, user_scope): return []
    async def read_outcome_series(self, user_scope): return []

try:
    asyncio.run(fetch(OneOrder(), "demo-subject-001"))
except MappingError as err:
    print(f"MappingError: {err}")
```

```text
MappingError: OrderRecord.occurred_at must be timezone-aware (got naive datetime.datetime(2026, 1, 10, 0, 0))
```

**`user_scope` / `subject_ref` is an opaque token — never identity (C9).**
You choose the token; to LHM it is a meaningless string. It must never
be an email, name, or external identifier — email-shaped tokens are refused
structurally at the mapping layer, the publish envelope, and the reference
backend route:

```python
from datetime import datetime, timezone
from syntropy.lhm import MappingError, OrderRecord, map_order

try:
    map_order(OrderRecord(
        subject_ref="someone@example.com",
        item_display="Magnesium glycinate",
        occurred_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    ))
except MappingError as err:
    print(f"MappingError: {err}")
```

```text
MappingError: subject_ref must be an opaque token, not an email
```

Name-shaped tokens can't be detected structurally — opacity beyond the email
check is your adapter's contract. Free-text check-in notes never enter the
SDK's outputs either: `map_series` consumes a `note` only through an
adapter-injected `sentiment_fn` and emits the resulting float; the text
stays on your side of the boundary. A single-user/self-hosted adapter can
ignore `user_scope` entirely (`syntropy.lhm.DEFAULT_USER_SCOPE` is
`"local-subject"`).

**The Rx-tier bit is the adapter's job — never an LLM's (D9).** Set
`OrderRecord.is_rx` from the *source record type* (a prescription-system
row → `True`; a supplement order → `False`). If your source can't tell,
fail **closed** to `True`: an Rx-tier metric is merely more restricted
(user-private surfaces only, never a `strong` tier in v0.1 — see
[the honesty layer](honesty-layer.md)); a mislabeled non-Rx metric would
under-protect. Downstream, any Rx order in the input set marks the whole
metric `rx_input=True`.

## A complete minimal adapter: three CSV files

`orders.csv`, `regimen_starts.csv`, `series.csv` — headers matching the
record fields, tz-aware ISO-8601 timestamps:

```csv
subject_ref,item_display,occurred_at,is_rx,code
demo-subject-001,Magnesium glycinate,2026-01-10T00:00:00+00:00,false,supplement-order
```

```csv
subject_ref,substance_display,started_at,code,dose_value,dose_unit
demo-subject-001,Magnesium glycinate,2026-01-10T00:00:00+00:00,regimen-start,400,mg
```

```csv
subject_ref,metric_display,unit,code,observed_at,value
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-03T08:00:00+00:00,5
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-04T08:00:00+00:00,4
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-05T08:00:00+00:00,5
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-06T08:00:00+00:00,6
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-07T08:00:00+00:00,5
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-08T08:00:00+00:00,4
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-09T08:00:00+00:00,5
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-10T08:00:00+00:00,7
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-11T08:00:00+00:00,8
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-12T08:00:00+00:00,7
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-13T08:00:00+00:00,8
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-14T08:00:00+00:00,9
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-15T08:00:00+00:00,8
demo-subject-001,Sleep quality (self-rated),,journal-metric,2026-01-16T08:00:00+00:00,8
```

The adapter, complete and runnable:

```python
import asyncio
import csv
import pathlib
from datetime import datetime

from syntropy.lhm import OrderRecord, RegimenStartRecord, SeriesPoint, SeriesRecord, analyze, fetch


class CsvAdapter:
    """LhmAdapter backed by three CSV files in ``data_dir``.

    Timestamps must be tz-aware ISO-8601 strings (e.g. ``…T08:00:00+00:00``)
    — ``datetime.fromisoformat`` preserves the offset, and the SDK refuses
    naive datetimes at the pipeline boundary.
    """

    def __init__(self, data_dir: pathlib.Path) -> None:
        self.data_dir = pathlib.Path(data_dir)

    def _rows(self, name: str) -> list[dict]:
        with open(self.data_dir / name, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    async def read_orders(self, user_scope: str) -> list[OrderRecord]:
        return [
            OrderRecord(
                subject_ref=r["subject_ref"],
                item_display=r["item_display"],
                occurred_at=datetime.fromisoformat(r["occurred_at"]),
                is_rx=r["is_rx"].strip().lower() == "true",
                code=r["code"],
                source="csv",
            )
            for r in self._rows("orders.csv")
            if r["subject_ref"] == user_scope
        ]

    async def read_regimen_starts(self, user_scope: str) -> list[RegimenStartRecord]:
        return [
            RegimenStartRecord(
                subject_ref=r["subject_ref"],
                substance_display=r["substance_display"],
                started_at=datetime.fromisoformat(r["started_at"]),
                code=r["code"],
                dose_value=float(r["dose_value"]) if r["dose_value"] else None,
                dose_unit=r["dose_unit"],
            )
            for r in self._rows("regimen_starts.csv")
            if r["subject_ref"] == user_scope
        ]

    async def read_outcome_series(self, user_scope: str) -> list[SeriesRecord]:
        by_metric: dict[str, list[dict]] = {}
        for r in self._rows("series.csv"):
            if r["subject_ref"] == user_scope:
                by_metric.setdefault(r["metric_display"], []).append(r)
        return [
            SeriesRecord(
                subject_ref=user_scope,
                metric_display=metric,
                unit=rows[0]["unit"],
                code=rows[0]["code"],
                points=tuple(
                    SeriesPoint(
                        observed_at=datetime.fromisoformat(r["observed_at"]),
                        value=float(r["value"]),
                    )
                    for r in rows
                ),
            )
            for metric, rows in by_metric.items()
        ]


async def main() -> None:
    adapter = CsvAdapter(pathlib.Path("data"))
    aligned = await fetch(adapter, "demo-subject-001")
    candidates = await analyze(aligned)
    c = candidates[0]
    print(f"{c.label}")
    print(f"tier: {c.confidence}, posterior_mean: {c.fit.posterior_mean:.2f}, "
          f"n_effective: {c.fit.n_effective}")


asyncio.run(main())
```

Output against the CSVs above:

```text
Magnesium glycinate ↔ Sleep quality (self-rated) (association)
tier: strong, posterior_mean: 0.60, n_effective: 7
```

Notes on the run: `fetch` returns `()` when there are no orders (nothing to
correlate — a normal early-adoption state, not an error), and multiple
outcome series produce one `AlignedData` (and one candidate) each. Filtering
rows by `user_scope` inside each reader, as above, is the whole multi-user
story on the adapter side.
