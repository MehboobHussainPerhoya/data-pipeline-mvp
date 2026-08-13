from schema.canonical import SupportCase

def _clean(value):
    """Empty strings from CSV should become None, not stay as ''."""
    if value is None or value == "":
        return None
    return value

def map_ticket_to_supportcase(raw: dict) -> SupportCase:
    """
    Maps one raw support_tickets record (from tickets_connector)
    into a validated SupportCase object.
    """
    return SupportCase(
        case_id=f"ticket_{raw.get('Ticket ID')}",
        subject=_clean(raw.get("Ticket Subject")),
        description=_clean(raw.get("Ticket Description")),
        status=_clean(raw.get("Ticket Status")),
        priority=_clean(raw.get("Ticket Priority")),
        channel=_clean(raw.get("Ticket Channel")),
        created_at=_clean(raw.get("First Response Time")),  # best available timestamp proxy — see note below
        resolution=_clean(raw.get("Resolution")),
        source_system="support_tickets",
        ticket_type=_clean(raw.get("Ticket Type")),
    )