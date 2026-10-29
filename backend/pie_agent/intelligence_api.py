"""Shared departmental knowledge routes. Qdrant only, no disk fallback/cache."""
from fastapi import APIRouter, HTTPException, Query, Response

from .intelligence import context, module_name
from .intelligence_store import configured_store

router=APIRouter(prefix='/usage/api/intelligence',tags=['department intelligence'])


def _run(response: Response,module: str,fn):
    response.headers['Cache-Control']='no-store'
    try:
        module_name(module)
        return fn(configured_store())
    except LookupError:
        raise HTTPException(404,'No departmental knowledge published for this module')
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    except RuntimeError:
        raise HTTPException(503,'Qdrant departmental knowledge is unavailable or failed integrity checks')


@router.get('/{module}/summary')
def summary(module: str,response: Response):
    return _run(response,module,lambda store:store.summary(module))


@router.get('/{module}/records')
def records(module: str,response: Response,department: str='marketing',kind: str='',
            q: str=Query('',max_length=2000),offset: int=Query(0,ge=0),limit: int=Query(30,ge=1,le=100)):
    def retrieve(store):
        package=store.load(module)
        if department not in package['departments']:
            raise ValueError('Unknown department')
        rows=[r for r in package['records'] if department in r['departments']
              and (not kind or r['kind']==kind)
              and (not q or q.casefold() in (r['title']+' '+r['content']+' '+r['category']).casefold())]
        return {'module':module,'storage':'qdrant','total':len(rows),'offset':offset,'records':rows[offset:offset+limit]}
    return _run(response,module,retrieve)


@router.get('/{module}/context')
def department_context(module: str,response: Response,department: str='marketing',
                       q: str=Query('',max_length=2000),audience: str='internal',limit: int=Query(12,ge=1,le=100)):
    return _run(response,module,lambda store:context(store.load(module),department,q,audience,limit))
