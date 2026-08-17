from semantic_traversal import BuildConfig, SemanticIdentifierDeclaration


def build_config(
    vault_name="test",
    uuid_field="uuid",
    excluded_folders=(),
    fields=(),
):
    return BuildConfig(
        vault_name,
        uuid_field,
        tuple(excluded_folders),
        tuple(SemanticIdentifierDeclaration(field, "test-authored declaration") for field in fields),
    )
