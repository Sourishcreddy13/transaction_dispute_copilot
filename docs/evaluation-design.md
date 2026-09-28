# Evaluation Design

## Decision correctness is deterministic

Financial disposition is never scored by an LLM judge. Expected actions, escalation states, intent classes and policy IDs are compared directly against the result produced by the application.

## Blinding

The application receives only the user claim, selected transaction hint, authenticated case context and trusted data fetched through the data plane. Expected actions and expected review states exist only in the evaluator.

The qualitative DeepEval judge receives:

- the claimant input;
- the customer-safe generated response;
- trusted reference context;
- retrieved policy context.

It does not receive expected action, expected reviewer decision or gold labels.

## Hidden cases

`data/eval/hidden.json` is evaluated by the same deterministic harness but is not embedded in prompts or runtime configuration.

## Causal mutations

A baseline transaction is altered one feature at a time, for example:

- normal amount -> high value;
- known device -> unknown device;
- low prior-dispute count -> repeated rejected disputes.

The test checks the expected deterministic invariant rather than whether the LLM produces persuasive prose.

## Qualitative metrics

DeepEval is used only for answer relevance, RAG faithfulness and hallucination consistency. DeepEval's documentation defines faithfulness against retrieval context and hallucination against a curated context, so the harness keeps those sources separate.
