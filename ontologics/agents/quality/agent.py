"""Actual Quality contract adapter and result decoder."""
from ..base import RoleAgent
from .schemas import CONTRACTS
from .validators import verified_quality_text, validated_quality_proposal


class QualityAgent(RoleAgent):
    ROLE = 'quality'
    CONTRACTS = CONTRACTS
    VERSIONS = {'investigation': '1.0.1'}

    def decode(self, text, task, tool_results, results):
        version = task.context.data_version
        if self.flow == 'investigation':
            by_mode = {row['arguments']['query']['mode']: row['result'] for row in tool_results}
            _, observations = verified_quality_text(text, by_mode)
            ids = sorted({eid for row in observations for phase in ('before', 'after')
                          if row[phase] is not None for eid in row[phase]['evidence']})
            return {'data_version': version, 'observations': observations, 'evidence_ids': ids}
        proposal = validated_quality_proposal(text, task.data()['question'], results)
        return {'data_version': version, 'proposal': proposal}
