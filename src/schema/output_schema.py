from pydantic import BaseModel

class JoinedCaseOutput(BaseModel):
    """
    The final, deployable shape of one support case after joining.
    This is what actually leaves the pipeline — stricter than SupportCase,
    since intermediate nulls are fine but final output shouldn't be.
    """
    case_id: str
    subject: str                    # required in final output — no blank subjects allowed through
    ticket_type: str | None = None
    matched_category: str | None = None
    matched_article_count: int
    source_system: str