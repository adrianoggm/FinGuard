"""The provider's answer, validated whole against the closed sets `visual_schema()` names.

`visual_schema()` is the contract the provider is asked to honour; this is the same contract read
back. Both are built from the domain `Literal`s -- `Mark`, `Channel`, `FieldType`, `Stack`,
`SortMode` -- so neither can drift from the other, and a test asserts the enums agree. Strict mode
and `extra="forbid"` make a wrong JSON type, an unknown enum value, a missing required key or an
extra key a *structure* failure: the adapter repairs once and then reports a provider failure,
because a provider that does not honour its schema is what failed. Nothing here coerces, defaults
or drops a value the model wrote -- Section C: refuse, never repair.

Not checked here, on purpose: `limit`'s bounds and `rationale`'s length. A grammar compiled from
the schema cannot enforce either, so an out-of-range `limit` is the model's proposal -- the
verifier refuses it as `invalid_proposal`, which is the record it belongs in -- and the rationale
bound is a token guard for the provider, never read.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finguard_orchestrator.domain.models.chart import Channel, FieldType, Mark, SortMode, Stack


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SortAnswer(_Strict):
    mode: SortMode
    field: str | None = None


class EncodingAnswer(_Strict):
    channel: Channel
    field: str
    type: FieldType
    sort: SortAnswer | None = None


class VisualAnswer(_Strict):
    mark: Mark | None
    encodings: list[EncodingAnswer] = Field(default_factory=list)
    sort: SortMode | None = None
    sort_field: str | None = None
    stack: Stack | None = None
    limit: int | None = None
    rationale: str | None = None


def parse_visual_answer(text: str) -> VisualAnswer | None:
    """The whole answer, or `None` for a structure failure -- never a partial reading."""
    try:
        answer = VisualAnswer.model_validate_json(text)
    except ValidationError:
        return None
    if answer.mark is not None and "encodings" not in answer.model_fields_set:
        # The schema requires `encodings`. Only the abstention may omit it: a terse
        # `{"mark": null}` is the abstention it is, not a provider that failed (finding 9).
        return None
    return answer
