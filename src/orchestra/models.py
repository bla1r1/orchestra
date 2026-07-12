"""Core typed data structures. No behaviour lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """What a task needs and what an agent declares it can do."""

    coding = "coding"
    review = "review"
    reasoning = "reasoning"
    architecture = "architecture"
    documentation = "documentation"
    search = "search"
    refactoring = "refactoring"
    testing = "testing"
    planning = "planning"


class Outcome(str, Enum):
    """Classification of a single agent execution."""

    success = "success"
    quota = "quota"          # quota / rate limit / daily limit -> cooldown + fall back
    timeout = "timeout"      # exceeded wall clock -> retry then fall back
    retryable = "retryable"  # transient error the agent told us to retry
    error = "error"          # anything else -> fall back


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Everything the engine needs to drive one CLI. Comes straight from YAML.

    ``command`` is an argv list where the literal token ``{prompt}`` is replaced
    with the task prompt (unless ``prompt_via_stdin`` is set, then the prompt is
    piped to stdin and no substitution happens).
    """

    name: str
    command: tuple[str, ...]
    capabilities: frozenset[Capability]
    priority: int = 100          # lower wins
    model: str | None = None     # default model; substituted for {model} in command
    install: tuple[str, ...] = ()  # argv to install the CLI when missing
    update: tuple[str, ...] = ()   # argv to update the CLI
    prompt_via_stdin: bool = False
    timeout: float = 600.0
    cooldown_seconds: float = 900.0
    max_retries: int = 1         # transient-failure retries against THIS agent
    quota_patterns: tuple[str, ...] = ()      # regex, matched case-insensitively
    retryable_patterns: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    manual: bool = False  # excluded from auto-routing/rotation; only via chain or --prefer

    def handles(self, needed: frozenset[Capability]) -> bool:
        return self.enabled and needed <= self.capabilities


@dataclass(slots=True)
class ExecutionResult:
    """Result of one agent execution attempt."""

    agent: str
    outcome: Outcome
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    reason: str = ""  # why we fell back, if we did

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.success


@dataclass(slots=True)
class TaskReport:
    """Final result plus the full attempt trail (what Claude reviews / logs)."""

    prompt: str
    capabilities: frozenset[Capability]
    attempts: list[ExecutionResult]

    @property
    def final(self) -> ExecutionResult | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def succeeded(self) -> bool:
        return bool(self.final and self.final.ok)
