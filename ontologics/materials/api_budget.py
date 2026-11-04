"""Budget-only opener composition for the EXISTING fixed OpenAIProvider.

No HTTP/model protocol replacement. All live calls in one authorization must use
one ledger path. No source, headers, keys, raw responses or errors are persisted.
Standard GPT-5.4-mini prices, conservative uncached billing, integer nanodollars.
Unknown usage retains the model context-window worst case, including failures.
"""
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import threading
from urllib import request

from ..openai_provider import ENDPOINT, MODEL, _NoRedirect
from ..service import DomainError

_LOCK=threading.Lock()
INPUT_PRICE=750  # nanodollars/token; $0.75 per million
OUTPUT_PRICE=4500
CONTEXT_LIMIT=400000


class _Response(io.BytesIO):
    def __init__(self,raw,status,headers):
        super().__init__(raw);self.status=status;self.headers=headers


class BudgetedOpener:
    """Reserve before network; persist count even if a process crashes afterwards.

    The fixed standard endpoint/model and text/function-only payload prevent
    extra hosted-tool fees. Ledger caps cannot be enlarged when reopened.
    A caller may stop earlier, but must not create a new ledger to reset a budget.
    """
    def __init__(self,ledger,*,open_fn=None,max_calls=24,max_nanodollars=2000000000):
        if type(max_calls) is not int or not 1<=max_calls<=24:raise ValueError('Invalid call cap')
        if type(max_nanodollars) is not int or not 1<=max_nanodollars<=2000000000:raise ValueError('Invalid cost cap')
        self.path=Path(ledger)
        self.open_fn=open_fn or request.build_opener(_NoRedirect()).open
        with self._ledger() as state:
            if not state:state.update(model=MODEL,max_calls=max_calls,max_nanodollars=max_nanodollars,records=[])
            elif (state.get('model'),state.get('max_calls'),state.get('max_nanodollars'))!=(MODEL,max_calls,max_nanodollars):
                raise ValueError('Budget limits cannot change on reopen')

    @contextmanager
    def _ledger(self):
        # O_NOFOLLOW rejects an accidentally redirected ledger. Protect inode
        # with flock and never replace it; process/thread users share the lock.
        with _LOCK:
            fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'r+',encoding='utf-8') as f:
                fcntl.flock(f,fcntl.LOCK_EX)
                raw=f.read();state=json.loads(raw) if raw else {}
                yield state
                f.seek(0);json.dump(state,f,sort_keys=True,indent=2);f.truncate();f.flush();os.fsync(f.fileno())

    def summary(self):
        with self._ledger() as s:
            return dict(attempts=len(s['records']),accounted_nanodollars=sum(r['accounted_nanodollars'] for r in s['records']),
                reported_nanodollars=sum(r.get('reported_nanodollars',0) for r in s['records']),
                unreported_calls=sum('reported_nanodollars' not in r for r in s['records']),
                max_calls=s['max_calls'],max_nanodollars=s['max_nanodollars'],records=list(s['records']))

    def __call__(self,req,*,timeout):
        p=json.loads(req.data)
        if (req.full_url!=ENDPOINT or p.get('model')!=MODEL or p.get('store') is not False or
            p.get('reasoning')!={'effort':'none'} or p.get('stream',False) is not False or
            p.get('service_tier','auto') not in ('auto','default') or
            any(t.get('type')!='function' for t in p.get('tools',[])) or
            type(p.get('max_output_tokens')) is not int or not 256<=p['max_output_tokens']<=8192):
            raise ValueError('Only fixed standard text/function provider requests are budgeted')
        # Existing provider produces text/function inputs; prohibit paid media.
        for item in p.get('input',[]):
            if 'content' in item and not isinstance(item['content'],str):raise ValueError('Text only')
        reserve=CONTEXT_LIMIT*INPUT_PRICE+p['max_output_tokens']*OUTPUT_PRICE
        with self._ledger() as s:
            if len(s['records'])>=s['max_calls'] or sum(r['accounted_nanodollars'] for r in s['records'])+reserve>s['max_nanodollars']:
                raise DomainError('API_BUDGET_EXHAUSTED','Authorized API budget exhausted')
            index=len(s['records']);s['records'].append(dict(attempt=index+1,accounted_nanodollars=reserve,status='reserved'))
        # Exceptions deliberately leave the full reservation in the ledger.
        with self.open_fn(req,timeout=timeout) as response:
            raw=response.read(2000001);status=response.status;headers=response.headers
        try:
            body=json.loads(raw);usage=body.get('usage',{})
            inp,out=usage.get('input_tokens'),usage.get('output_tokens')
            valid=(type(inp) is int and 0<=inp<=CONTEXT_LIMIT and type(out) is int and 0<=out<=p['max_output_tokens'])
        except (ValueError,AttributeError,TypeError):valid=False
        with self._ledger() as s:
            row=s['records'][index];row['status']='response_received'
            if valid:
                cost=inp*INPUT_PRICE+out*OUTPUT_PRICE
                row.update(input_tokens=inp,output_tokens=out,reported_nanodollars=cost,accounted_nanodollars=cost)
        return _Response(raw,status,headers)
