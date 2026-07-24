# instantiation/

One folder per project. `_TEMPLATE/` is the empty ratification template — copy it, then
fill it ONLY through the Stage 0.5 elicitation interview. Every field must trace to a
ratified ledger item; the `SchemaReadySpec` validator (../src/lit2db/contracts/spec.py)
refuses any field that does not.

The scaffold under `../src` is domain-invariant and never edited per project. Domain
knowledge lives here and nowhere else — that separation is what makes a trained model
traceable to the exact schema, vocabularies, and source scope it saw.
