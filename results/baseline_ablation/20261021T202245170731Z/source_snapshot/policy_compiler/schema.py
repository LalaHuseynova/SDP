"""Version-one policy contracts. All authority inputs are structured and deny by default."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
    StringConstraints,
)


Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]
Request = Annotated[str, StringConstraints(min_length=1, max_length=16000, pattern=r"\S")]
Count = Annotated[int, Field(ge=1, le=1000)]
Duration = Annotated[int, Field(ge=1, le=86400)]
Scalar = Annotated[StrictStr, StringConstraints(max_length=4000)] | StrictBool | StrictInt | StrictFloat
Values = Annotated[list[Scalar], Field(min_length=1, max_length=100)]
Constraints = Annotated[dict[Identifier, Values], Field(max_length=32)]
IDs = Annotated[list[Identifier], Field(max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ArgumentSchema(StrictModel):
    value_type: Literal["string", "integer", "number", "boolean"]
    enum: Values | None = None


class ToolSchema(StrictModel):
    tool: Identifier
    operation: Identifier
    description: Text
    requires_resource: bool
    requires_recipient: bool
    arguments: Annotated[dict[Identifier, ArgumentSchema], Field(max_length=32)]


class ResourceMetadata(StrictModel):
    resource_id: Identifier
    name: Text
    kind: Literal["memory", "document", "directory", "workspace", "dataset"]


class RecipientMetadata(StrictModel):
    recipient_id: Identifier
    name: Text
    aliases: Annotated[list[Text], Field(max_length=20)]


class TrustedMetadata(StrictModel):
    resources: Annotated[list[ResourceMetadata], Field(max_length=256)]
    recipients: Annotated[list[RecipientMetadata], Field(max_length=256)]


class OperationLimit(StrictModel):
    tool: Identifier
    operation: Identifier
    resource_ids: IDs
    recipient_ids: IDs
    argument_constraints: Constraints
    max_calls: Count


class PermissionCeiling(StrictModel):
    read_resource_ids: IDs
    operations: Annotated[list[OperationLimit], Field(max_length=64)]
    max_total_actions: Annotated[int, Field(ge=0, le=1000)]
    max_duration_seconds: Duration


class CompilerContext(StrictModel):
    existing_user_permissions: PermissionCeiling
    global_rules: PermissionCeiling
    available_tool_schemas: Annotated[list[ToolSchema], Field(max_length=64)]
    trusted_resource_metadata: TrustedMetadata


class Evidence(StrictModel):
    basis: Literal["explicit", "necessary_inference", "uncertain"]
    request_excerpt: Text
    rationale: Text


class ReadPermission(StrictModel):
    resource_id: Identifier
    evidence: Evidence


class RecipientConstraint(StrictModel):
    recipient_id: Identifier
    request_reference: Text


class ActionPermission(StrictModel):
    tool: Identifier
    operation: Identifier
    resource_ids: IDs
    recipients: Annotated[list[RecipientConstraint], Field(max_length=256)]
    argument_constraints: Constraints
    max_calls: Count
    evidence: Evidence


class Expiration(StrictModel):
    condition: Literal["task_completion"]
    max_duration_seconds: Duration


class Ambiguity(StrictModel):
    field: Text
    question: Text
    candidate_ids: IDs


class TaskPolicy(StrictModel):
    schema_version: Literal["1"]
    original_request: Request
    status: Literal["ready", "clarification_required", "denied"]
    reads: Annotated[list[ReadPermission], Field(max_length=256)]
    actions: Annotated[list[ActionPermission], Field(max_length=64)]
    expiration: Expiration
    ambiguities: Annotated[list[Ambiguity], Field(max_length=32)]
    denial_reason: Text | None
