from admcms_mcp.schema import SCHEMA_KNOWLEDGE


def test_schema_knowledge_is_non_empty():
    assert isinstance(SCHEMA_KNOWLEDGE, str)
    assert len(SCHEMA_KNOWLEDGE) > 1000


def test_schema_knowledge_mentions_core_tables():
    # Sanity check: key ADMCMS tables should be documented
    for table in ["CUSTSUPPLIER", "PRODUCT", "SALESINVOICE"]:
        assert table in SCHEMA_KNOWLEDGE.upper()
