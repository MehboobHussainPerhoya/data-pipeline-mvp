import pathlib
p = pathlib.Path('D:/data-pipeline-mvp/src/mcp_server/server.py')
c = p.read_text(encoding='utf-8')

# 1. Add provenance imports after hitl_gate import
c = c.replace(
    'from agent.hitl_gate import HITLGate',
    'from agent.hitl_gate import HITLGate\nfrom provenance.lineage_tracker import LineageTracker\nfrom provenance.query import LineageQuery'
)

# 2. Add lineage MCP tools before the __main__ block
old_main = 'if __name__ == "__main__":\n    mcp.run()'

new_tools = '''# ---------------------------------------------------------------------------
# Phase 6: Provenance & Lineage Tracking MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_lineage(dataset_name: str, field_name: str = None) -> dict:
    """Returns lineage information for an output dataset or specific field.
    Traces the full derivation chain back to source fields without re-running
    the pipeline (FR-PROV-03).
    dataset_name: the output dataset to trace.
    field_name: optional specific field to trace (if omitted, traces the whole dataset)."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    query = LineageQuery(tracker.store)
    if field_name:
        chain = query.trace_field(dataset_name, field_name)
        explanation = query.explain_field(dataset_name, field_name)
    else:
        chain = query.trace_dataset(dataset_name)
        explanation = f'Traced {len(chain)} lineage records for dataset {dataset_name}.'
    return {
        'records': [r.model_dump() for r in chain],
        'explanation': explanation,
        'record_count': len(chain),
    }


@mcp.tool()
def get_join_provenance() -> list[dict]:
    """Returns provenance for all join operations in the last pipeline run.
    For each join: the join rule, source (manual/agent/human), confidence score,
    and review status (FR-PROV-02)."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    query = LineageQuery(tracker.store)
    return query.get_join_provenance()


@mcp.tool()
def get_lineage_summary() -> dict:
    """Returns a summary of all lineage records from the last pipeline run."""
    tracker = _cache.get('lineage_tracker')
    if tracker is None:
        raise RuntimeError('No lineage data available - run a pipeline with lineage tracking first.')
    return tracker.summary()


if __name__ == "__main__":
    mcp.run()'''

c = c.replace(old_main, new_tools)

p.write_text(c, encoding='utf-8')
print('OK - server.py modified')
