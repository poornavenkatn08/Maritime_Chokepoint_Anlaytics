# Event table — pre-registration protocol

`config/event_table.yml` is the machine-readable version. This file explains the
rules that produced it.

## The protocol

1. Windows, treated units, and control units are written into
   `config/event_table.yml` and **committed**.
2. `06_validate_method.py` runs and its output is committed.
3. Only then does `07_event_study.py` run.
4. `07_event_study.py` writes the commit SHA and the event-table commit timestamp
   into every result file, and warns if the working tree is dirty.

If a window must change after results exist, a **new entry** is added with its
rationale. The old entry stays. Both are reported.

## Why the git history matters

Anyone can claim they picked windows in advance. `git log -- config/event_table.yml`
shows when the file was last touched relative to when results were produced. That
is checkable, which is the point.

## Choosing the treatment date

For routing questions, the date is the **carrier decision point**, not the date of
any individual incident. The outcome measured is routing behaviour, and routing
changes when operators decide, not when an event occurs. Using an incident date
would put part of the response inside the pre-period.

## Control selection rules (fixed before estimation)

A chokepoint is control-eligible unless it:

- sits on the affected routing corridor (it is an outcome, not a control — this
  excludes Gibraltar and Dover for the Asia–Europe window);
- carries its own concurrent geopolitical shock (Kerch, Bosporus, Taiwan, Hormuz);
- averages under 5 transits/day (Bering at ~0.7/day, Magellan at ~4/day — too
  noisy in logs).

Kerch is the clearest exclusion: its 2022–2023 pre-period trend runs about
−0.99 log points per year. Using it as a "stable" control would be indefensible.

## Reporting requirement

Every reported result must state: treated units, control units, event date, date
rationale, parallel-trends verdict, both specifications, the randomization
p-value, and the lead check. A result missing any of these is not finished.
