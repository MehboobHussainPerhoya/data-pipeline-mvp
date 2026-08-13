import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from normalization.tickets_mapper import map_ticket_to_supportcase
from normalization.kb_mapper import map_kb_to_knowledgearticle
from normalization.api_mapper import map_api_to_supportcase
from transform.join_engine import join_cases_to_articles
from transform.join_config import TICKET_TYPE_TO_KB_CATEGORY
from validation.output_validator import build_output_records, is_safe_to_deploy
from validation.deploy_gate import deploy_pipeline


def test_ticket_mapping_produces_valid_case():
    raw = {
        "Ticket ID": "1", "Ticket Subject": "Test subject", "Ticket Description": "desc",
        "Ticket Status": "Open", "Ticket Priority": "Low", "Ticket Channel": "Email",
        "First Response Time": "2023-01-01 10:00:00", "Resolution": "", "Ticket Type": "Technical issue",
    }
    case = map_ticket_to_supportcase(raw)
    assert case.case_id == "ticket_1"
    assert case.resolution is None  # empty string must become None


def test_api_mapping_maps_completed_to_status():
    raw = {"id": 5, "title": "Sample task", "completed": True}
    case = map_api_to_supportcase(raw)
    assert case.case_id == "api_5"
    assert case.status == "Closed"


def test_kb_mapping_requires_question_and_answer():
    raw = {"category": "ORDER", "intent": "cancel_order", "instruction": "", "response": ""}
    with pytest.raises(Exception):
        map_kb_to_knowledgearticle(raw, 0)  # empty question/answer must fail validation


def test_join_matches_known_ticket_type():
    case = map_ticket_to_supportcase({
        "Ticket ID": "1", "Ticket Subject": "x", "Ticket Description": "x",
        "Ticket Status": "Open", "Ticket Priority": "Low", "Ticket Channel": "Email",
        "First Response Time": "2023-01-01 10:00:00", "Resolution": "", "Ticket Type": "Refund request",
    })
    article = map_kb_to_knowledgearticle(
        {"category": "REFUND", "intent": "get_refund", "instruction": "q", "response": "a"}, 0
    )
    joined = join_cases_to_articles([case], [article], TICKET_TYPE_TO_KB_CATEGORY)
    assert joined[0]["matched_category"] == "REFUND"
    assert len(joined[0]["matched_articles"]) == 1


def test_deploy_blocked_without_approval(tmp_path):
    case = map_api_to_supportcase({"id": 1, "title": "t", "completed": False})
    output_records, errors = build_output_records(
        [{"case": case, "matched_category": None, "matched_articles": []}]
    )
    is_safe, reasons = is_safe_to_deploy(output_records, errors)
    output_path = str(tmp_path / "test_output.json")

    with pytest.raises(RuntimeError, match="approval"):
        deploy_pipeline(output_records, is_safe, reasons, approved=False, output_path=output_path)


def test_deploy_succeeds_with_approval(tmp_path):
    case = map_api_to_supportcase({"id": 1, "title": "t", "completed": False})
    output_records, errors = build_output_records(
        [{"case": case, "matched_category": None, "matched_articles": []}]
    )
    is_safe, reasons = is_safe_to_deploy(output_records, errors)
    output_path = str(tmp_path / "test_output.json")

    deploy_pipeline(output_records, is_safe, reasons, approved=True, output_path=output_path)
    assert Path(output_path).exists()