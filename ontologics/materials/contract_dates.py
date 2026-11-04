"""Use explicit finer deadline precision only when all source values agree."""
from copy import deepcopy
from datetime import date, datetime
import re


def precise_deadline(values):
    dates=set();timestamps=set()
    try:
        for value in values:
            if not isinstance(value,str):return None
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                dates.add(date.fromisoformat(value).isoformat())
            elif re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})',value):
                datetime.fromisoformat(value.replace('Z','+00:00'))
                dates.add(value[:10]);timestamps.add(value)
            else:return None
    except ValueError:return None
    return next(iter(timestamps)) if len(dates)==1 and len(timestamps)==1 else None


def resolve_deadline(field):
    result=deepcopy(field)
    if field.get('state')!='conflict':return result
    if not all(c.get('evidence') and all(e.get('quote')==c.get('value') for e in c['evidence'])
               for c in field.get('claims',[])):return result
    value=precise_deadline([c.get('value') for c in field.get('claims',[])])
    if value is not None:
        result.update(state='supported',value=value)
    return result
