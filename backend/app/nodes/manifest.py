from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManifestError(ValueError):
    """Raised when a node manifest is structurally or semantically invalid."""


class StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ManifestMetadata(StrictManifestModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+(?:\.\d+)?$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    lifecycle: Literal["active", "deprecated"] = "active"


class ExecutionManifest(StrictManifestModel):
    kind: Literal["python", "manual_wait", "durable_poll", "callback_wait"]
    handler: str | None = None

    @model_validator(mode="after")
    def validate_handler(self) -> "ExecutionManifest":
        handler_kinds = {"python", "durable_poll"}
        if self.kind in handler_kinds and not self.handler:
            raise ValueError(f"{self.kind} execution requires a handler")
        if self.kind not in handler_kinds and self.handler is not None:
            raise ValueError(f"{self.kind} execution cannot declare a handler")
        return self


class PortManifest(StrictManifestModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str = Field(min_length=1, max_length=100)
    data_type: Literal["string", "number", "boolean", "object", "array", "any"] = Field(
        alias="dataType"
    )


class SpecManifest(StrictManifestModel):
    category: str = Field(min_length=1, max_length=100)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    execution: ExecutionManifest
    inputs: list[PortManifest]
    outputs: list[PortManifest]
    config_schema: dict[str, Any] = Field(alias="configSchema")
    default_config: dict[str, Any] = Field(alias="defaultConfig")

    @model_validator(mode="after")
    def validate_unique_ports(self) -> "SpecManifest":
        for direction, ports in (("input", self.inputs), ("output", self.outputs)):
            names = [port.name for port in ports]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {direction} port names")
        return self


class NodeManifest(StrictManifestModel):
    api_version: Literal["flowforge/v1"] = Field(alias="apiVersion")
    kind: Literal["NodeType"]
    metadata: ManifestMetadata
    spec: SpecManifest


def validate_manifest(document: Any, source: str) -> NodeManifest:
    try:
        manifest = NodeManifest.model_validate(document)
    except Exception as exc:
        raise ManifestError(f"Invalid node manifest {source}: {exc}") from exc

    try:
        Draft202012Validator.check_schema(manifest.spec.config_schema)
        Draft202012Validator(manifest.spec.config_schema).validate(
            manifest.spec.default_config
        )
    except SchemaError as exc:
        raise ManifestError(f"Invalid configSchema in {source}: {exc.message}") from exc
    except JsonSchemaValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ManifestError(
            f"defaultConfig in {source} does not satisfy configSchema at {path}: "
            f"{exc.message}"
        ) from exc
    return manifest
