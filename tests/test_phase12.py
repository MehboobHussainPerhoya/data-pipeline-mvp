"""
Phase 12 tests - Ingestion, Normalization and Union upgrades.
"""
import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.failure_isolation import (
    ingest_with_isolation, run_ingestion_isolated,
)
from ingestion.cdc import (
    HighWaterMarkTracker, WaterMark,
    filter_incremental_csv, filter_incremental_by_id,
    filter_incremental_by_timestamp,
)
from normalization.transform_chain import TransformChain, TransformStep
from transform.union import (
    check_schemas_match, detect_duplicates, union_with_checks,
    SchemaMismatchError,
)
from schema.canonical import SupportCase


# ---------------------------------------------------------------------------
# Test Group 1: FR-ING-05 - Ingestion failure isolation
# ---------------------------------------------------------------------------

def test_one_source_failure_does_not_block_other_sources():
    """THE CRITICAL TEST: one source failing does not block others."""
    def good_connector():
        return [{"id": 1}, {"id": 2}, {"id": 3}]

    def bad_connector():
        raise ConnectionError("API is down")

    def another_good_connector():
        return [{"article": "a"}, {"article": "b"}]

    sources = [
        ("support_tickets", good_connector, (), {}),
        ("support_activity_api", bad_connector, (), {}),
        ("kb_articles", another_good_connector, (), {}),
    ]

    result = run_ingestion_isolated(sources)
    assert len(result.results) == 3
    assert "support_tickets" in result.successful_sources
    assert len(result.get_records("support_tickets")) == 3
    assert "support_activity_api" in result.failed_sources
    assert result.get_records("support_activity_api") == []
    assert "kb_articles" in result.successful_sources
    assert len(result.get_records("kb_articles")) == 2
    s = result.summary()
    assert s["successful"] == 2
    assert s["failed"] == 1
    assert s["any_succeeded"] is True
    assert s["all_succeeded"] is False


def test_ingest_with_isolation_captures_error():
    def failing():
        raise FileNotFoundError("file not found")
    result = ingest_with_isolation("test_source", failing)
    assert result.success is False
    assert "file not found" in result.error
    assert result.error_type == "FileNotFoundError"
    assert result.records == []


def test_ingest_with_isolation_captures_success():
    def succeeding():
        return [{"a": 1}, {"a": 2}]
    result = ingest_with_isolation("test_source", succeeding)
    assert result.success is True
    assert result.record_count == 2


def test_all_sources_failing_reports_all_failures():
    def fail1():
        raise ValueError("error 1")
    def fail2():
        raise ValueError("error 2")
    sources = [("source_a", fail1, (), {}), ("source_b", fail2, (), {})]
    result = run_ingestion_isolated(sources)
    assert len(result.results) == 2
    assert result.all_succeeded is False
    assert result.any_succeeded is False
    assert len(result.failed_sources) == 2


def test_real_example_tickets_succeed_api_fails():
    """REAL EXAMPLE: tickets succeed, API fails - both reported, no crash."""
    def tickets_connector():
        return [
            {"Ticket ID": "1", "Ticket Subject": "Refund", "Ticket Type": "Refund request"},
            {"Ticket ID": "2", "Ticket Subject": "Bug", "Ticket Type": "Technical issue"},
        ]
    def api_connector():
        raise ConnectionError("Failed to fetch from API: connection refused")

    sources = [
        ("support_tickets", tickets_connector, (), {}),
        ("support_activity_api", api_connector, (), {}),
    ]
    result = run_ingestion_isolated(sources)
    assert len(result.results) == 2
    tickets_records = result.get_records("support_tickets")
    assert len(tickets_records) == 2
    assert tickets_records[0]["Ticket Subject"] == "Refund"
    api_result = [r for r in result.results if r.source_name == "support_activity_api"][0]
    assert api_result.success is False
    assert "connection refused" in api_result.error


# ---------------------------------------------------------------------------
# Test Group 2: FR-ING-03 - CDC / Incremental ingestion
# ---------------------------------------------------------------------------

def test_hwm_first_run_full_load(tmp_path):
    tracker = HighWaterMarkTracker(state_path=str(tmp_path / "cdc.json"))
    assert tracker.get_mark("support_tickets") is None


def test_hwm_update_and_persist(tmp_path):
    state_path = str(tmp_path / "cdc.json")
    tracker = HighWaterMarkTracker(state_path=state_path)
    tracker.update("support_tickets", last_seen_index=5000, records_processed=5000)
    tracker2 = HighWaterMarkTracker(state_path=state_path)
    mark = tracker2.get_mark("support_tickets")
    assert mark is not None
    assert mark.last_seen_index == 5000
    assert mark.total_processed == 5000


def test_hwm_cumulative_total(tmp_path):
    tracker = HighWaterMarkTracker(state_path=str(tmp_path / "cdc.json"))
    tracker.update("source_a", last_seen_index=100, records_processed=100)
    tracker.update("source_a", last_seen_index=200, records_processed=100)
    mark = tracker.get_mark("source_a")
    assert mark.total_processed == 200
    assert mark.last_seen_index == 200


def test_filter_incremental_csv_full_load():
    records = [{"id": i} for i in range(10)]
    filtered, new_index = filter_incremental_csv(records, mark=None)
    assert len(filtered) == 10
    assert new_index == 9


def test_filter_incremental_csv_incremental():
    records = [{"id": i} for i in range(10)]
    mark = WaterMark(source_name="test", last_seen_index=4)
    filtered, new_index = filter_incremental_csv(records, mark=mark)
    assert len(filtered) == 5
    assert new_index == 9


def test_filter_incremental_csv_no_new():
    records = [{"id": i} for i in range(5)]
    mark = WaterMark(source_name="test", last_seen_index=4)
    filtered, new_index = filter_incremental_csv(records, mark=mark)
    assert len(filtered) == 0
    assert new_index == 4


def test_filter_incremental_by_id():
    records = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    mark = WaterMark(source_name="test", last_seen_id=2)
    filtered, new_id = filter_incremental_by_id(records, "id", mark=mark)
    assert len(filtered) == 2
    assert new_id == 4


def test_filter_incremental_by_timestamp():
    records = [{"ts": "2024-01-01"}, {"ts": "2024-01-02"}, {"ts": "2024-01-03"}]
    mark = WaterMark(source_name="test", last_seen_timestamp="2024-01-01")
    filtered, new_ts = filter_incremental_by_timestamp(records, "ts", mark=mark)
    assert len(filtered) == 2
    assert new_ts == "2024-01-03"


def test_cdc_reset(tmp_path):
    tracker = HighWaterMarkTracker(state_path=str(tmp_path / "cdc.json"))
    tracker.update("source_a", last_seen_index=100, records_processed=100)
    tracker.reset("source_a")
    assert tracker.get_mark("source_a") is None


# ---------------------------------------------------------------------------
# Test Group 3: FR-NORM-02/04 - Transform chains with preview
# ---------------------------------------------------------------------------

def test_chain_applies_all_steps():
    chain = TransformChain(name="test", steps=[
        TransformStep(name="trim", transform="trim_whitespace", field="name"),
        TransformStep(name="clean", transform="empty_to_none", field="desc"),
    ])
    records = [{"name": "  hello  ", "desc": ""}, {"name": "world", "desc": "text"}]
    result = chain.apply(records)
    assert result[0]["name"] == "hello"
    assert result[0]["desc"] is None
    assert result[1]["name"] == "world"


def test_chain_preview_at_step():
    chain = TransformChain(name="test", steps=[
        TransformStep(name="trim", transform="trim_whitespace", field="name"),
        TransformStep(name="prefix", transform="prefix_id", field="id", params={"prefix": "ticket_"}),
    ])
    records = [{"id": "1", "name": "  hello  "}]
    preview = chain.preview_at_step(records, step_index=0)
    assert preview[0]["name"] == "hello"
    assert preview[0]["id"] == "1"  # not yet prefixed
    preview = chain.preview_at_step(records, step_index=1)
    assert preview[0]["id"] == "ticket_1"


def test_chain_preview_all_steps():
    chain = TransformChain(name="test", steps=[
        TransformStep(name="trim", transform="trim_whitespace", field="name"),
        TransformStep(name="clean", transform="empty_to_none", field="desc"),
        TransformStep(name="prefix", transform="prefix_id", field="id", params={"prefix": "t_"}),
    ])
    records = [{"id": "1", "name": "  x  ", "desc": ""}]
    previews = chain.preview_all_steps(records)
    assert len(previews) == 3
    assert previews[0]["records"][0]["name"] == "x"
    assert previews[0]["records"][0]["desc"] == ""
    assert previews[1]["records"][0]["desc"] is None
    assert previews[2]["records"][0]["id"] == "t_1"


def test_chain_does_not_mutate_input():
    chain = TransformChain(name="test", steps=[
        TransformStep(name="trim", transform="trim_whitespace", field="name"),
    ])
    records = [{"name": "  hello  "}]
    original = dict(records[0])
    chain.apply(records)
    assert records[0] == original


def test_chain_compiles_to_ir_operators():
    from ir.operators import MapOperator
    chain = TransformChain(name="test", steps=[
        TransformStep(name="trim", transform="trim_whitespace", field="name"),
        TransformStep(name="prefix", transform="prefix_id", field="id", params={"prefix": "t_"}),
    ])
    operators = chain.to_ir_operators("raw_data", "normalized")
    assert len(operators) == 2
    assert all(isinstance(op, MapOperator) for op in operators)
    assert operators[-1].output == "normalized"


# ---------------------------------------------------------------------------
# Test Group 4: FR-UNION-01 - Schema-matched union
# ---------------------------------------------------------------------------

def test_schema_mismatch_raises():
    with pytest.raises(SchemaMismatchError) as exc_info:
        check_schemas_match(["SupportCase", "KnowledgeArticle"])
    assert "Schema mismatch" in str(exc_info.value)


def test_schema_match_passes():
    check_schemas_match(["SupportCase", "SupportCase"])


def test_union_schema_mismatch_raises():
    records_a = [SupportCase(case_id="1", source_system="a")]
    records_b = [{"article_id": "1", "question": "q", "answer": "a", "source_system": "b"}]
    with pytest.raises(SchemaMismatchError):
        union_with_checks([
            ("source_a", records_a, "SupportCase"),
            ("source_b", records_b, "KnowledgeArticle"),
        ])


def test_union_schema_match_succeeds():
    records_a = [SupportCase(case_id="1", source_system="a")]
    records_b = [SupportCase(case_id="2", source_system="b")]
    result = union_with_checks([
        ("source_a", records_a, "SupportCase"),
        ("source_b", records_b, "SupportCase"),
    ])
    assert len(result.records) == 2
    assert result.schema_checked is True


# ---------------------------------------------------------------------------
# Test Group 5: FR-UNION-03 - Duplicate detection
# ---------------------------------------------------------------------------

def test_dup_detection_flags_exact_duplicates():
    records = [
        {"case_id": "1", "subject": "test", "source_system": "tickets"},
        {"case_id": "1", "subject": "test", "source_system": "api"},
        {"case_id": "2", "subject": "other", "source_system": "tickets"},
    ]
    flags = detect_duplicates(records)
    assert len(flags) == 1
    assert set(flags[0].sources) == {"tickets", "api"}


def test_dup_detection_does_not_remove():
    records = [
        {"case_id": "1", "subject": "test", "source_system": "a"},
        {"case_id": "1", "subject": "test", "source_system": "b"},
    ]
    flags = detect_duplicates(records)
    assert len(records) == 2
    assert len(flags) == 1


def test_no_duplicates():
    records = [
        {"case_id": "1", "subject": "a", "source_system": "tickets"},
        {"case_id": "2", "subject": "b", "source_system": "api"},
    ]
    flags = detect_duplicates(records)
    assert len(flags) == 0


def test_union_detects_duplicates():
    records_a = [SupportCase(case_id="1", subject="test", source_system="tickets")]
    records_b = [SupportCase(case_id="1", subject="test", source_system="api")]
    result = union_with_checks([
        ("tickets", records_a, "SupportCase"),
        ("api", records_b, "SupportCase"),
    ])
    assert result.duplicate_count == 1
    assert len(result.records) == 2


def test_dup_excludes_source_field():
    records = [
        {"case_id": "1", "subject": "test", "source_system": "a"},
        {"case_id": "1", "subject": "test", "source_system": "b"},
    ]
    flags = detect_duplicates(records)
    assert len(flags) == 1


def test_dup_different_content_not_flagged():
    records = [
        {"case_id": "1", "subject": "a", "source_system": "tickets"},
        {"case_id": "2", "subject": "b", "source_system": "api"},
    ]
    flags = detect_duplicates(records)
    assert len(flags) == 0
