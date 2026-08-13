from schema.canonical import SupportCase, KnowledgeArticle

def join_cases_to_articles(
    cases: list[SupportCase],
    articles: list[KnowledgeArticle],
    ticket_type_to_category: dict,
) -> list[dict]:
    """
    Joins each SupportCase to all KnowledgeArticles sharing its mapped category.
    One-to-many join: each case gets a list of matched articles (possibly empty).
    `ticket_type_to_category` is passed in as config, not hardcoded, so this
    function can later become a generic MCP 'run_join' tool.
    """
    # Pre-group articles by category for fast lookup instead of re-scanning
    # all 26,872 articles for every case
    articles_by_category: dict[str, list[KnowledgeArticle]] = {}
    for article in articles:
        articles_by_category.setdefault(article.category, []).append(article)

    results = []
    for case in cases:
        mapped_category = ticket_type_to_category.get(case.ticket_type)
        matched_articles = articles_by_category.get(mapped_category, []) if mapped_category else []
        results.append({
            "case": case,
            "matched_category": mapped_category,
            "matched_articles": matched_articles,
        })
    return results