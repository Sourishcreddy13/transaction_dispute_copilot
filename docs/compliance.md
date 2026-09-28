# Compliance Engineering Mapping

This document records engineering mappings, not legal conclusions.

| Framework | Concern | Engineering control | Evidence |
|---|---|---|---|
| EU AI Act | traceability / record keeping | `DecisionProvenance`, audit chain | `src/graph.py`, `app/core/db.py` |
| EU AI Act | human oversight | HITL review task + CAS | `app/core/db.py`, `src/graph.py` |
| EU AI Act | transparency | customer-safe response + provider provenance | `app/workflow.py`, audit |
| NIST AI RMF | Govern | risk register / control IDs | `docs/risk-register.md` |
| NIST AI RMF | Map | intended use / out-of-scope in model card | `docs/model-card.md` |
| NIST AI RMF | Measure | deterministic golden set + DeepEval qualitative metrics | `reports/eval_report.json` |
| NIST AI RMF | Manage | fallback, budgets, fault injection, incident evidence | `config/app.yaml`, `evidence/fault_matrix.yaml` |
| DPDP | data minimization | masking + structured memory | `app/core/pii.py`, `src/memory/store.py` |
| DPDP | storage limitation | TTL + erasure script | `src/memory/store.py`, `scripts/erase_customer.py` |
| DPDP | security safeguards | signed auth context + audit | `app/core/security.py`, `app/core/db.py` |
