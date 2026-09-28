from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class PIIResult:
    masked_text: str
    entities: list[dict]
    engine: str

class PIIService:
    def __init__(self, mode='presidio'):
        self.mode = mode
        self.analyzer = None
        self.anonymizer = None
        if mode == 'presidio':
            try:
                from presidio_analyzer import AnalyzerEngine
                from presidio_anonymizer import AnonymizerEngine
                self.analyzer = AnalyzerEngine()
                self.anonymizer = AnonymizerEngine()
            except Exception:
                self.mode = 'regex'

    def scan(self, text: str) -> PIIResult:
        if self.mode == 'presidio' and self.analyzer:
            results = self.analyzer.analyze(text=text, language='en')
            replacements = {r.entity_type: '<REDACTED_%s>' % r.entity_type for r in results}
            from presidio_anonymizer.entities import OperatorConfig
            ops = {k: OperatorConfig('replace', {'new_value': v}) for k, v in replacements.items()}
            masked = self.anonymizer.anonymize(text=text, analyzer_results=results, operators=ops).text
            return PIIResult(masked, [{'type': r.entity_type, 'start': r.start, 'end': r.end, 'score': r.score} for r in results], 'presidio')
        patterns = {
            'PAN': r'\b(?:\d[ -]?){13,19}\b',
            'EMAIL': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
            'PHONE': r'\b(?:\+?91[- ]?)?[6-9]\d{9}\b',
            'ACCOUNT': r'\b\d{8,20}\b',
        }
        masked = text
        entities=[]
        for typ, pat in patterns.items():
            masked, n = re.subn(pat, f'<REDACTED_{typ}>', masked)
            if n: entities.append({'type':typ,'count':n})
        return PIIResult(masked, entities, 'regex')
