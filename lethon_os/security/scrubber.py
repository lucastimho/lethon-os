"""Memory Scrubber — indirect prompt-injection defense.

Scope: inspect every shard retrieved from L2 (Qdrant) or L3 (Archive)
before it is re-injected into the agent's context window. The goal is
to catch *indirect* injection — malicious instructions embedded in
research data, tool outputs, or retrieved documents — before the model
reads them.

Detection layers:

  1. **Regex** (always on) — cheap, catches the overt ~80%: role-hijack
     prefixes, fake system tags, override directives, known jailbreak
     keywords, common data-exfiltration templates.

  2. **Semantic** (optional) — caller supplies an async callable
     (e.g. a quantised Llama-Guard or Prompt-Guard-2 inference). Called
     only when the regex layer returns SAFE, so cost scales with
     suspicion rather than traffic.

Severity vocabulary:

  SAFE       — clean; deliver to context
  WARN       — low-confidence signal; surface in UI but allow
  FLAG       — high-confidence signal; block from context
  QUARANTINE — multiple independent signals; hard isolate

A rolling-window counter tracks blocking decisions (FLAG or QUARANTINE).
When the count exceeds ``spike_threshold`` within ``spike_window_seconds``
the scrubber raises :class:`ScrubberAlert`, which upstream converts to
HTTP 503 + suspension of autonomous tool-calling. That turns a suspected
adversarial campaign into a human-in-the-loop event, not a silent
content filter.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from lethon_os.schemas import MemoryShard


class Severity(str, Enum):
    SAFE = "safe"
    WARN = "warn"
    FLAG = "flag"
    QUARANTINE = "quarantine"


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.SAFE: 0,
    Severity.WARN: 1,
    Severity.FLAG: 2,
    Severity.QUARANTINE: 3,
}


# Rule: (severity, compiled pattern, human-readable reason).
# Patterns are case-insensitive where it matters and anchored loosely —
# precision beats recall here because false-blocks on user data are a
# worse failure mode than letting a rare injection slip to semantic layer.
_RULES: list[tuple[Severity, re.Pattern[str], str]] = [
    # --- FLAG: high-confidence injection patterns --------------------------
    (
        Severity.FLAG,
        re.compile(r"(?im)^\s*(system|assistant|developer)\s*:\s*\S"),
        "role-hijack prefix",
    ),
    (
        Severity.FLAG,
        re.compile(r"</?(system|instruction|assistant|user|im_start|im_end)\b", re.I),
        "fake role / chat-template tag",
    ),
    (
        Severity.FLAG,
        re.compile(
            r"(?i)\b(ignore|disregard|forget|override|bypass)\s+"
            r"(all\s+|any\s+)?(prior|previous|above|prev|earlier|your)?\s*"
            r"(instruction|prompt|rule|system|constraint|guideline|polic(y|ies))s?",
        ),
        "override directive",
    ),
    (
        Severity.FLAG,
        re.compile(
            r"(?i)\b(jailbreak|\bDAN\b|do\s+anything\s+now|developer\s+mode|"
            r"unrestricted\s+mode|god\s+mode)",
        ),
        "jailbreak keyword",
    ),
    (
        Severity.FLAG,
        re.compile(r"(?i)\bprint\s+(the\s+)?(system|above|prior)\s+prompt"),
        "prompt extraction attempt",
    ),
    # --- WARN: suspicious but ambiguous ------------------------------------
    (
        Severity.WARN,
        re.compile(
            r"(?i)\b(act\s+as|pretend\s+to\s+be|you\s+are\s+now|"
            r"roleplay\s+as)\s+\w+",
        ),
        "persona swap request",
    ),
    (
        Severity.WARN,
        re.compile(
            r"(?i)\b(send|email|post|upload|transmit|exfiltrate)\s+"
            r"(this|the\s+above|the\s+conversation|your\s+history)",
        ),
        "data-exfiltration pattern",
    ),
    (
        Severity.WARN,
        re.compile(r"data:text/[a-z]+;base64,[A-Za-z0-9+/=]{100,}"),
        "oversized base64 data URI",
    ),
]


SemanticScorer = Callable[[str], Awaitable["tuple[Severity, str]"]]
"""Async classifier: content → (severity, reason). SAFE if no concern."""


@dataclass(frozen=True)
class ScrubDecision:
    """Result of a single scrub. Frozen so it can be logged/audited safely."""

    severity: Severity
    reasons: tuple[str, ...]
    shard_id: str

    @property
    def is_blocking(self) -> bool:
        return self.severity in (Severity.FLAG, Severity.QUARANTINE)


class ScrubberAlert(Exception):
    """Raised when blocking-severity decisions spike within the rolling window.

    The upstream handler should convert this to HTTP 503 and halt any
    autonomous tool-calls until a human clears the alert. Silent filtering
    is the wrong response — a spike almost certainly means someone is
    probing the agent.
    """

    def __init__(self, flags_in_window: int, window_seconds: float) -> None:
        super().__init__(
            f"Adversarial spike: {flags_in_window} blocking flags "
            f"in {window_seconds:.0f}s",
        )
        self.flags_in_window = flags_in_window
        self.window_seconds = window_seconds


class MemoryScrubber:
    """Stateful scrubber — owns the rolling window for spike detection."""

    def __init__(
        self,
        semantic_scorer: SemanticScorer | None = None,
        *,
        spike_threshold: int = 5,
        spike_window_seconds: float = 60.0,
    ) -> None:
        self._scorer = semantic_scorer
        self._spike_threshold = spike_threshold
        self._spike_window = spike_window_seconds
        self._flag_timestamps: list[float] = []

    async def scrub(self, shard: MemoryShard) -> ScrubDecision:
        severity, reasons = self._regex_scan(shard.content)

        # Only defer to the (expensive) semantic layer when regex is
        # inconclusive — never override a confirmed FLAG down to SAFE.
        if severity is Severity.SAFE and self._scorer is not None:
            sem_sev, sem_reason = await self._scorer(shard.content)
            if sem_sev is not Severity.SAFE:
                severity = sem_sev
                reasons = (*reasons, f"semantic: {sem_reason}")

        decision = ScrubDecision(
            severity=severity,
            reasons=reasons,
            shard_id=shard.id,
        )
        self._record(decision)
        return decision

    # -- internals -------------------------------------------------------

    def _regex_scan(self, content: str) -> tuple[Severity, tuple[str, ...]]:
        hits: list[tuple[Severity, str]] = []
        for sev, pattern, reason in _RULES:
            if pattern.search(content):
                hits.append((sev, reason))

        if not hits:
            return Severity.SAFE, ()

        # Two or more FLAG-level hits at once is almost always a real
        # attack, not a coincidence → QUARANTINE.
        if sum(1 for s, _ in hits if s is Severity.FLAG) >= 2:
            return Severity.QUARANTINE, tuple(r for _, r in hits)

        max_sev = max((s for s, _ in hits), key=_SEVERITY_RANK.__getitem__)
        return max_sev, tuple(r for _, r in hits)

    def _record(self, decision: ScrubDecision) -> None:
        if not decision.is_blocking:
            return
        now = time.monotonic()
        cutoff = now - self._spike_window
        self._flag_timestamps = [t for t in self._flag_timestamps if t > cutoff]
        self._flag_timestamps.append(now)

        if len(self._flag_timestamps) >= self._spike_threshold:
            # Reset the window so upstream's 503 window isn't retriggered
            # every subsequent request — operator acks the spike by
            # unblocking, which is an operational decision we don't own.
            flags = len(self._flag_timestamps)
            self._flag_timestamps.clear()
            raise ScrubberAlert(
                flags_in_window=flags,
                window_seconds=self._spike_window,
            )
