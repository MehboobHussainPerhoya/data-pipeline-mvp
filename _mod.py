import pathlib
p = pathlib.Path('D:/data-pipeline-mvp/src/ir/executor.py')
c = p.read_text(encoding='utf-8')
c = c.replace(
    '        self._transforms = builtin_transform_registry',
    '        self._transforms = builtin_transform_registry\n        self._lineage_tracker = None  # Phase 6'
)
c = c.replace(
    '        self._mappers[name] = func\n\n    # --- Execution ---',
    '        self._mappers[name] = func\n\n    def attach_lineage_tracker(self, tracker) -> None:\n        """Attach a LineageTracker for provenance recording (Phase 6)."""\n        self._lineage_tracker = tracker\n\n    # --- Execution ---'
)
c = c.replace(
    '        for i, step in enumerate(ir.steps):\n            datasets[step.output] = self._execute_operator(step, datasets)\n\n        return datasets',
    '        for i, step in enumerate(ir.steps):\n            datasets_before = dict(datasets)\n            output = self._execute_operator(step, datasets)\n            datasets[step.output] = output\n            if self._lineage_tracker is not None:\n                self._lineage_tracker.record_execution(\n                    step=step, step_index=i,\n                    datasets_before=datasets_before, output_data=output,\n                )\n\n        return datasets'
)
p.write_text(c, encoding='utf-8')
print('OK - executor.py modified')
