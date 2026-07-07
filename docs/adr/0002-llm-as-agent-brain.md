# 0002. LLM is the agent brain

- **Status**: accepted
- **Date**: 2026-06-22

## Context

The original design (pre-2026-06-22) considered a Python orchestrator
in this repo: a long-running loop that fetches signals, vets risk, and
calls the execution adapter without human-in-the-loop. The risk was
that any such orchestrator would become a second safety surface
bypassing the user's actual judgment.

## Decision

This repo owns the **analysis + execution primitives**. The LLM
agent is the **agent brain**: it reads `SKILL.md`, calls skills as
tools, narrates, asks the user to confirm, and — with explicit
approval — calls `execution-kraken-spot` or `execution-kraken-perps`.
Cron is analytics-only (`run-all-l3`, `position-watchdog`); it never
places orders.

The interactive confirm at the execution layer is the actual safety
gate. The LLM may override an advisory `REJECT` from `risk-engine`,
but the user must press the button.

## Consequences

- (+) One safety surface (the confirm prompt). The LLM can
  re-narrate, ask follow-ups, or override; the user always has the
  final say at the moment of execution.
- (+) Crons can't accidentally place orders. Analytics-only by
  contract — no code path from cron to `execution-*`.
- (-) The LLM must read `SKILL.md` on every call. Schema drift
  between docs and code is a real risk; that's why the repo's stance
  is "no backward compatibility — update every caller in the same
  commit" (see `AGENTS.md`).
- (-) Loss of the LLM = no execution. The user must be present (or
  have explicitly typed `--yes`).
