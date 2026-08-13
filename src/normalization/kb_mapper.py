from schema.canonical import KnowledgeArticle

def _clean(value):
    if value is None or value == "":
        return None
    return value

def map_kb_to_knowledgearticle(raw: dict, row_index: int) -> KnowledgeArticle:
    """
    Maps one raw kb_articles record (from kb_connector) into a
    validated KnowledgeArticle object.

    row_index is required because this source has no native ID column —
    we generate one deterministically from its position in the file.
    """
    return KnowledgeArticle(
        article_id=f"kb_{row_index}",
        category=_clean(raw.get("category")),
        intent=_clean(raw.get("intent")),
        question=_clean(raw.get("instruction")),
        answer=_clean(raw.get("response")),
        source_system="kb_articles",
    )