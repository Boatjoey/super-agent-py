"""JSON codec for the value types in this repository.

The JSON key of a field cannot be derived from its attribute name, so every field
whose JSON key differs carries metadata from :func:`json_field`.

Three behaviours are reproduced because the on-disk artefacts are a shared
contract:

* Map keys are sorted on output while dataclass fields keep declaration order, so
  ``sort_keys`` is applied per node rather than as a dump-wide flag.
* Unknown object keys are ignored on input.
* The zero value of a field tagged ``omitempty`` is left out of the object:
  ``false``, ``0``, ``""``, ``None``, and empty containers.

One behaviour is deliberately dropped: HTML escaping. Rewriting ``<``, ``>``, and
``&`` as ``\\u003c``, ``\\u003e``, and ``\\u0026`` only matters for byte-for-byte
equality, which is not required, so the text stays readable instead.
"""

from __future__ import annotations

import dataclasses
import json
import types
from collections.abc import Mapping, Sequence, Sized
from dataclasses import Field
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from super_agent.timeutil import FormatRFC3339Nano, ParseRFC3339Nano

_METADATA_KEY = "json"


@dataclasses.dataclass(frozen=True, slots=True)
class _JsonField:
    """How one dataclass field appears in JSON."""

    name: str | None = None
    omitempty: bool = False


def json_field(*, name: str | None = None, omitempty: bool = False) -> Mapping[str, object]:
    """Build the ``metadata`` mapping for :func:`dataclasses.field`.

    ``name`` overrides the JSON key; ``omitempty`` drops the field when its value
    is the zero value of its type.
    """
    return {_METADATA_KEY: _JsonField(name=name, omitempty=omitempty)}


def _meta(field: Field[Any]) -> _JsonField:
    meta = field.metadata.get(_METADATA_KEY)
    return meta if isinstance(meta, _JsonField) else _JsonField()


def _is_zero(value: Any) -> bool:
    """True for the zero values that ``omitempty`` leaves out."""
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, int | float):
        return value == 0
    if isinstance(value, Sized):
        return len(value) == 0
    return False


def to_json_value(value: Any) -> Any:
    """Convert ``value`` into plain JSON-able data.

    Dataclass field order is preserved; mapping keys are sorted.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _dataclass_to_json(value)
    if isinstance(value, datetime):
        # Datetimes are stored as RFC3339Nano, the format the on-disk artefacts
        # use.
        return FormatRFC3339Nano(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        mapping = cast("Mapping[Any, Any]", value)
        return {str(key): to_json_value(mapping[key]) for key in sorted(mapping, key=str)}
    if isinstance(value, (list, tuple)):
        sequence = cast("Sequence[Any]", value)
        return [to_json_value(item) for item in sequence]
    return value


def _dataclass_to_json(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in dataclasses.fields(value):
        meta = _meta(field)
        item = getattr(value, field.name)
        if meta.omitempty and _is_zero(item):
            continue
        result[meta.name or field.name] = to_json_value(item)
    return result


def dumps(value: Any, *, indent: int | None = None) -> str:
    """Serialise ``value``.

    ``indent`` of 2 gives a two-space layout; ``None`` gives the compact form,
    which uses no spaces after the separators.
    """
    separators = (",", ":") if indent is None else (",", ": ")
    return json.dumps(
        to_json_value(value),
        indent=indent,
        separators=separators,
        ensure_ascii=False,
        sort_keys=False,
    )


@cache
def _hints(cls: type[Any]) -> dict[str, Any]:
    return get_type_hints(cls)


def _decode(value: Any, annotation: Any) -> Any:
    """Build a value of ``annotation`` from decoded JSON data."""
    if annotation is Any or annotation is None:
        return value
    if value is None:
        return None

    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _decode(value, members[0]) if members else None
    if origin is list:
        item_type = get_args(annotation)[0]
        return [_decode(item, item_type) for item in value]
    if origin is tuple:
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(item, args[0]) for item in value)
        return tuple(_decode(item, arg) for item, arg in zip(value, args, strict=True))
    if origin in (dict, Mapping):
        args = get_args(annotation)
        item_type = args[1] if len(args) == 2 else Any
        return {str(key): _decode(item, item_type) for key, item in value.items()}

    if isinstance(annotation, type):
        concrete: type[Any] = annotation
        if issubclass(concrete, datetime):
            return ParseRFC3339Nano(value)
        if issubclass(concrete, Enum):
            return concrete(value)
        if dataclasses.is_dataclass(concrete):
            return from_json_value(value, concrete)
        return concrete(value)
    return value


def from_json_value[T](data: Mapping[str, Any], cls: type[T]) -> T:
    """Build a ``cls`` instance from ``data``.

    Keys the dataclass does not declare are ignored; dataclass defaults cover the
    keys that are absent.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        key = _meta(field).name or field.name
        if key in data:
            kwargs[field.name] = _decode(data[key], hints[field.name])
    return cls(**kwargs)


def loads[T](text: str, cls: type[T]) -> T:
    """Parse ``text`` and build a ``cls`` instance from it."""
    data = json.loads(text)
    if not isinstance(data, Mapping):
        raise TypeError(f"expected a JSON object, got {type(data).__name__}")
    return from_json_value(cast("Mapping[str, Any]", data), cls)
