from app.nodes.registry import REGISTRY


def main() -> None:
    print(f"Registry valid: {len(REGISTRY.entries)} node types")
    print(f"Fingerprint: {REGISTRY.fingerprint}")
    for key, entry in REGISTRY.entries.items():
        print(
            f"- {key[0]}@{key[1]} [{entry.definition.lifecycle}] "
            f"{entry.execution_kind} ({entry.source})"
        )


if __name__ == "__main__":
    main()
