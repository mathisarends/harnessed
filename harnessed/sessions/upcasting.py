import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BeforeValidator, TypeAdapter, ValidationError

from harnessed.errors import SessionFormatError
from harnessed.models.values import JSONValue

type Document = dict[str, JSONValue]
type Upcaster = Callable[[Document], Document]


def _integer(value: object) -> int:
    # Literal[1] alone also accepts True and 1.0.
    if type(value) is not int:
        raise ValueError("Document versions must be integers")
    return value


# Keep each model's literal visible in its generated JSON schema.
integer_version = BeforeValidator(_integer)


@dataclass(frozen=True)
class DocumentFormat:
    """A contiguous read window. Step i maps oldest+i to oldest+i+1.

    Steps receive a private JSON tree and return the next document body. The
    dispatcher stamps its version. Children are visited after the parent has
    reached its current layout, and only at explicitly owned document fields.
    """

    name: str
    oldest: int
    steps: tuple[Upcaster, ...] = ()
    children: Callable[[Document], None] | None = None

    def upgrade(self, value: object) -> Document:
        if not isinstance(value, dict):
            raise SessionFormatError(f"Expected a JSON object for {self.name}")
        current = self.oldest + len(self.steps)
        version = _integer(value.get("version", current))
        if not self.oldest <= version <= current:
            raise SessionFormatError(
                f"Unsupported {self.name} version {version}; readable versions "
                f"are {self.oldest} through {current}"
            )
        document = value
        for source in range(version, current):
            document = self.steps[source - self.oldest](document)
            document["version"] = source + 1
        if self.children is not None:
            self.children(document)
        return document

    def decode[T](self, adapter: TypeAdapter[T], payload: str | bytes) -> T:
        try:
            try:
                return adapter.validate_json(payload)
            except ValidationError as error:
                # Current documents take just the native typed JSON decoder.
                problems = error.errors(include_input=False, include_context=False)
                if not any(
                    p["type"] == "literal_error" and p["loc"][-1:] == ("version",) for p in problems
                ):
                    raise
            document = self.upgrade(json.loads(payload))
            return adapter.validate_json(json.dumps(document, allow_nan=False))
        except SessionFormatError:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise SessionFormatError(f"Invalid {self.name}") from error

    def encode_input(self, payload: Mapping[str, JSONValue]) -> str:
        """Keep JSON validation semantics, including base64 assets, for mapping callers."""
        try:
            return json.dumps(dict(payload), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise SessionFormatError(f"Invalid {self.name}") from error
