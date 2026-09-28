# Output Risk Classification

| Tier | Example | Release control |
|---|---|---|
| Low | dispute category, transaction facts, policy citation | schema validation |
| Medium | fraud explanation, investigation recommendation | analyst view + output guardrail |
| High | provisional credit, chargeback, deny, security flag, high-value/high-fraud case | deterministic escalation gate; human review where required; no unaudited release |

The customer view never exposes fraud scores, internal policy rule IDs, internal paths or raw customer text.
