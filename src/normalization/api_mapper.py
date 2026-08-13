from schema.canonical import SupportCase

def map_api_to_supportcase(raw: dict) -> SupportCase:
    """
    Maps one raw support_activity_api record (from api_connector)
    into a validated SupportCase object.
    This source only has id/title/completed — everything else is null.
    """
    return SupportCase(
        case_id=f"api_{raw.get('id')}",
        subject=raw.get("title"),
        description=None,
        status="Closed" if raw.get("completed") else "Open",
        priority=None,
        channel="jsonplaceholder_api",
        created_at=None,
        resolution=None,
        source_system="support_activity_api",
        ticket_type=None,
    )