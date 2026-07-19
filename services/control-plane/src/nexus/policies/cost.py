"""Cost policy (docs/COST-GUARDRAILS.md).

The initial deployment uses only subscription-authenticated CLIs already paid
for by the owner (Claude Max, ChatGPT Plus). Everything paid is disabled and
cannot be enabled without an explicit owner approval gate of kind
'enable-paid-service' or 'change-billing'.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CostPolicy:
    paid_model_apis: bool = False
    paid_hosting: bool = False
    paid_databases: bool = False
    paid_queues: bool = False
    paid_monitoring: bool = False
    paid_domain_services: bool = False
    automatic_purchasing: bool = False
    notes: str = "Subscription CLIs only (Claude Max, ChatGPT Plus). No metered APIs."

    def describe(self) -> dict[str, object]:
        return {
            "paid_apis_enabled": self.paid_model_apis,
            "description": self.notes,
            "flags": {
                "paid_model_apis": self.paid_model_apis,
                "paid_hosting": self.paid_hosting,
                "paid_databases": self.paid_databases,
                "paid_queues": self.paid_queues,
                "paid_monitoring": self.paid_monitoring,
                "paid_domain_services": self.paid_domain_services,
                "automatic_purchasing": self.automatic_purchasing,
            },
        }


DEFAULT_COST_POLICY = CostPolicy()


@dataclass
class WorkerBudgetState:
    """Tracks subscription-limit exhaustion per worker (not monetary cost —
    Nexus never claims exact monetary figures it cannot determine)."""

    exhausted: dict[str, str] = field(default_factory=dict)  # worker -> reason

    def mark_exhausted(self, worker: str, reason: str) -> None:
        self.exhausted[worker] = reason

    def clear(self, worker: str) -> None:
        self.exhausted.pop(worker, None)

    def is_available(self, worker: str) -> bool:
        return worker not in self.exhausted
