from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.run_variables import get_variable, get_variable_path, set_variable, variables_as_dict


@dataclass(frozen=True)
class NodeContext:
    run_id: str
    node_run_id: str
    node_id: str
    attempt: int
    idempotency_key: str
    db: Session
    flow_id: str = ""
    flow_config_data: dict[str, Any] = field(default_factory=dict)

    def get_variable(self, name: str, default: Any = None) -> Any:
        return get_variable(self.db, self.run_id, name, default)

    def get_variable_path(self, path: str, default: Any = None) -> Any:
        return get_variable_path(self.db, self.run_id, path, default)

    def variables(self) -> dict[str, Any]:
        return variables_as_dict(self.db, self.run_id)

    def set_variable(
        self, name: str, value: Any, mode: str = "REPLACE"
    ) -> Any:
        return set_variable(self.db, self.run_id, self.node_id, name, value, mode).value

    def flow_config(self) -> dict[str, Any]:
        return self.flow_config_data

    def resolve_credential(self, alias: str, url: str):
        from app.security.credentials import resolve_credential

        return resolve_credential(
            self.db,
            self.flow_id,
            alias,
            url,
            run_id=self.run_id,
            node_id=self.node_id,
        )


@dataclass(frozen=True)
class PollPending:
    delay_seconds: float
    last_output: dict[str, Any]


class NodeHandler(Protocol):
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any] | PollPending: ...
