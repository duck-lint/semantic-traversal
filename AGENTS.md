# Implementation discipline

The specification supplied for the current task is the sole authority for
system behavior. 

Read requirements in their full document and section context. Do not isolate a
sentence from its surrounding scope, and do not paraphrase a requirement into a
weaker or approximately equivalent requirement before implementing it.

If the specification names a mechanism, representation, boundary, or behavior,
implement that mechanism, representation, boundary, or behavior. Do not
substitute something that appears functionally similar or easier to implement.

Do not infer additional prerequisites, dependencies, semantics, or architecture
that the specification does not require.

When work is scoped to one implementation stage, implement only that stage.
Do not opportunistically implement later stages.

Existing code may be retained only when it conforms to the specification.
Otherwise rewrite or delete it rather than preserving its behavior.

Tests verify the specification; they do not redefine or complete it.

If something appears ambiguous, contradictory, or impossible to implement
literally:
1. reread the surrounding section and relevant later/earlier references;
2. do not choose behavior yourself;
3. stop only if the ambiguity remains;
4. quote the exact conflicting or insufficient text and explain precisely what
   decision cannot be derived from it.

Do not treat a future or separately scoped concern as a blocker for the current
stage unless the specification explicitly makes it a dependency.

Do not modify source corpus/vault data unless the task explicitly authorizes it.
Do not treat diagnostic output as authority over source data until the
implementation producing it has been accepted as conformant.
