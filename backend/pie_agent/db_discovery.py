from __future__ import annotations
import os, json
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs


def _env(name, default=''):
    return os.getenv(name, default)

def load_dotenv(path='.env'):
    p=Path(path)
    if not p.exists():
        from .paths import REPO_ROOT
        p=REPO_ROOT/path
    if not p.exists(): return
    for line in p.read_text(encoding='utf-8',errors='ignore').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); v=v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(),v)

def connection_string():
    url=_env('DATABASE_URL')
    if url: return url
    host=_env('DB_HOST'); db=_env('DB_NAME'); user=_env('DB_USER'); pwd=_env('DB_PASSWORD'); driver=_env('DB_DRIVER','ODBC Driver 18 for SQL Server'); port=_env('DB_PORT','1433')
    if not (host and db and user): return ''
    return f'mssql+pyodbc://{user}:{pwd}@{host}:{port}/{db}?driver={driver.replace(" ","+")}&TrustServerCertificate=yes'

def _sqlalchemy_url_to_pyodbc(url:str)->str:
    if url.startswith('mssql+pyodbc://'):
        return url.replace('mssql+pyodbc://','mssql://',1)
    return url

def scan_sqlserver(database_url:str|None=None):
    load_dotenv()
    url=database_url or connection_string()
    if not url: return {'status':'NOT_CONFIGURED','objects':[],'error':'DATABASE_URL or DB_* variables not configured'}
    try:
        import pyodbc
    except ImportError:
        return {'status':'ERROR','objects':[],'error':'pyodbc is not installed. Install with: pip install pyodbc'}
    try:
        # Parse SQLAlchemy URL ourselves so passwords containing common URL characters are handled by SQLAlchemy when encoded.
        from sqlalchemy.engine import make_url
        u=make_url(url)
        driver=u.query.get('driver','ODBC Driver 18 for SQL Server')
        server=u.host or ''
        if u.port: server=f'{server},{u.port}'
        pwd=u.password or ''
        db=u.database or ''
        conn_str=(f'DRIVER={{{driver}}};SERVER={server};DATABASE={db};UID={u.username};PWD={pwd};TrustServerCertificate=yes;Encrypt=no')
        cn=pyodbc.connect(conn_str,timeout=int(_env('DB_TIMEOUT_SECONDS','10')))
        cur=cn.cursor()
        objects=[]
        tables=cur.execute("SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_SCHEMA,TABLE_NAME").fetchall()
        for schema,name,typ in tables:
            objects.append({'schema':schema,'name':name,'type':typ,'columns':[],'primaryKeys':[],'foreignKeys':[]})
        by={(o['schema'],o['name']):o for o in objects}
        cols=cur.execute("""SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE,IS_NULLABLE,ORDINAL_POSITION,CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION""").fetchall()
        for r in cols:
            o=by.get((r[0],r[1]));
            if o:o['columns'].append({'name':r[2],'dataType':r[3],'nullable':r[4]=='YES','ordinal':r[5],'maxLength':r[6]})
        pks=cur.execute("""SELECT tc.TABLE_SCHEMA,tc.TABLE_NAME,kcu.COLUMN_NAME,kcu.ORDINAL_POSITION FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu ON tc.CONSTRAINT_NAME=kcu.CONSTRAINT_NAME AND tc.TABLE_SCHEMA=kcu.TABLE_SCHEMA AND tc.TABLE_NAME=kcu.TABLE_NAME WHERE tc.CONSTRAINT_TYPE='PRIMARY KEY' ORDER BY tc.TABLE_SCHEMA,tc.TABLE_NAME,kcu.ORDINAL_POSITION""").fetchall()
        for r in pks:
            o=by.get((r[0],r[1]));
            if o:o['primaryKeys'].append(r[2])
        fks=cur.execute("""SELECT fk.TABLE_SCHEMA,fk.TABLE_NAME,kcu.COLUMN_NAME,rcu.TABLE_SCHEMA,rcu.TABLE_NAME,rcu.COLUMN_NAME FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS fk ON rc.CONSTRAINT_NAME=fk.CONSTRAINT_NAME AND rc.CONSTRAINT_SCHEMA=fk.TABLE_SCHEMA JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu ON fk.CONSTRAINT_NAME=kcu.CONSTRAINT_NAME AND fk.TABLE_SCHEMA=kcu.TABLE_SCHEMA JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk ON rc.UNIQUE_CONSTRAINT_NAME=pk.CONSTRAINT_NAME AND rc.UNIQUE_CONSTRAINT_SCHEMA=pk.TABLE_SCHEMA JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE rcu ON pk.CONSTRAINT_NAME=rcu.CONSTRAINT_NAME AND pk.TABLE_SCHEMA=rcu.TABLE_SCHEMA AND kcu.ORDINAL_POSITION=rcu.ORDINAL_POSITION WHERE fk.CONSTRAINT_TYPE='FOREIGN KEY' ORDER BY fk.TABLE_SCHEMA,fk.TABLE_NAME""").fetchall()
        for r in fks:
            o=by.get((r[0],r[1]));
            if o:o['foreignKeys'].append({'column':r[2],'refSchema':r[3],'refTable':r[4],'refColumn':r[5]})
        views=cur.execute("SELECT TABLE_SCHEMA,TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS ORDER BY TABLE_SCHEMA,TABLE_NAME").fetchall()
        procs=cur.execute("SELECT ROUTINE_SCHEMA,ROUTINE_NAME,ROUTINE_TYPE FROM INFORMATION_SCHEMA.ROUTINES ORDER BY ROUTINE_SCHEMA,ROUTINE_NAME").fetchall()
        cn.close()
        return {'status':'CONNECTED','database':db,'server':server,'tableCount':len(objects),'viewCount':len(views),'routineCount':len(procs),'objects':objects,'views':[{'schema':r[0],'name':r[1]} for r in views],'routines':[{'schema':r[0],'name':r[1],'type':r[2]} for r in procs]}
    except Exception as e:
        return {'status':'ERROR','objects':[],'error':str(e)}
