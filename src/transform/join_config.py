# Maps SupportCase.ticket_type -> KnowledgeArticle.category
# This is a manual, human-defined mapping (Phase 2 requirement) —
# not inferred by any model. Confidence is noted for future review.
TICKET_TYPE_TO_KB_CATEGORY = {
    "Refund request": "REFUND",
    "Cancellation request": "CANCEL",
    "Billing inquiry": "PAYMENT",
    "Product inquiry": "ORDER",       # weak match — no direct equivalent
    "Technical issue": "CONTACT",     # weak match — no direct equivalent
}