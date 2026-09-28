# Deviations and Runtime Decisions

## D-01 — Mandatory fallback provider

The original requirements file states Gemini-only. The assessment rule was subsequently changed by the project owner to require a fallback provider. The current implementation therefore uses Gemini as primary and Groq as the operational fallback. This is a documented change to the assessment requirement, not a hidden implementation choice. Every provider attempt is captured in provenance and evidence.

## D-02 — Local banking data plane

Real card-network, fraud, sanctions, bureau and customer systems are out of scope. The data plane is synthetic and local; all integration interfaces are designed so real providers can replace them later.

## D-03 — Local Phoenix evidence

The application emits OpenTelemetry spans and registers with Phoenix when available. The evidence generator uses Phoenix as the authoritative trace source in strict mode. Local JSON span logs exist as runtime diagnostics only and are not treated as equivalent Phoenix evidence for strict submission validation.
