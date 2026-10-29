from __future__ import annotations
import argparse, json, os, shutil, tempfile, zipfile
from pathlib import Path
from .pipeline import run
from .generation import run_generation
from .kms import JsonKMSStore
from .erp_discovery import run_erp_discovery
from .erp_generation import generate_from_erp
from .screen_identity import run_screen_identity
from .api_mapping import run_api_mapping
from .db_mapping import run_db_mapping
from .evidence_graph import build_evidence_graph
from .db_discovery import load_dotenv
from .ai_knowledge import generate_ai_knowledge
from .capability_model import build_capability_model
from .qdrant_kms import QdrantHTTP, QdrantKMS, manifest_from_knowledge_dir
from .usage import build_usage_catalog, export_static_ui, export_chat_ui, load_knowledge_local, load_knowledge_qdrant
from .activity_log import ACTIVITIES, INVENTORY_AFTER, QDRANT_AFTER, track, log_event, record_result, refresh_inventory, qdrant_snapshot
from .knowledge_ingest import publish_usage_catalog, read_ticket_file, read_ticket_sql, normalize_tickets, publish_tickets, ticket_summary

def _extract_for_refs(zip_path: Path):
    tmp=Path(tempfile.mkdtemp(prefix="pie-ref-"))
    with zipfile.ZipFile(zip_path) as z: z.extractall(tmp)
    return tmp

def _main():
    ap=argparse.ArgumentParser(description="PIE Product Intelligence Content Preparation Agent")
    sub=ap.add_subparsers(dest="cmd",required=True)
    from .intelligence import add_commands
    add_commands(sub)
    r=sub.add_parser("run")
    r.add_argument("--input",required=True)
    r.add_argument("--output",default="outputs/run")
    r.add_argument("--llm",action="store_true")
    r.add_argument("--provider")
    g=sub.add_parser("run-golden")
    g.add_argument("--output",default="outputs/golden")
    g.add_argument("--llm",action="store_true")
    g.add_argument("--provider")
    d=sub.add_parser("discover")
    d.add_argument("--input",required=True)
    d.add_argument("--output",default="outputs/discovery")
    gen=sub.add_parser("generate")
    gen.add_argument("--input",required=True)
    gen.add_argument("--output",default="outputs/generation")
    ed=sub.add_parser("discover-erp")
    ed.add_argument("--frontend",default=os.getenv("ERP_FRONTEND_PATH","./data/erp/frontend"))
    ed.add_argument("--backend",default=os.getenv("ERP_BACKEND_PATH","./data/erp/backend"))
    ed.add_argument("--output",default="outputs/phase12-discovery")
    ed.add_argument("--database-url")
    eg=sub.add_parser("generate-erp")
    eg.add_argument("--frontend",default=os.getenv("ERP_FRONTEND_PATH","./data/erp/frontend"))
    eg.add_argument("--backend",default=os.getenv("ERP_BACKEND_PATH","./data/erp/backend"))
    eg.add_argument("--output",default="outputs/phase12-generation")
    eg.add_argument("--database-url")
    si=sub.add_parser("screen-identity")
    si.add_argument("--input",required=True,help="erp_discovery_report.json")
    si.add_argument("--output",default="outputs/phase13-identity")
    am=sub.add_parser("map-api")
    am.add_argument("--registry",required=True,help="screen_registry_v13.json")
    am.add_argument("--frontend",default=os.getenv("ERP_FRONTEND_PATH","./data/erp/frontend"))
    am.add_argument("--backend",default=os.getenv("ERP_BACKEND_PATH","./data/erp/backend"))
    am.add_argument("--output",default="outputs/phase14-api-mapping")
    am.add_argument("--module",help="Limit mapping to one frontend module, e.g. inventory")
    am.add_argument("--modules",help="Several modules in one scan, e.g. hrms,finance: writes <modules-root>/<module>/phase14-api-mapping")
    am.add_argument("--modules-root",default="outputs/modules")
    dm=sub.add_parser("map-db")
    dm.add_argument("--api-mapping",default="outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json")
    dm.add_argument("--backend",default=os.getenv("ERP_BACKEND_PATH","./data/erp/backend"))
    dm.add_argument("--output",default="outputs/phase15-db-mapping")
    dm.add_argument("--database-url")
    dm.add_argument("--modules",help="Several modules in one database read + backend scan: <modules-root>/<module>/phase15-db-mapping")
    dm.add_argument("--modules-root",default="outputs/modules")
    dm.add_argument("--db-snapshot", help="database_object_registry_v15.json from an earlier LIVE scan; used when SQL Server is unreachable")
    egph=sub.add_parser("build-evidence-graph")
    egph.add_argument("--api-mapping",default="outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json")
    egph.add_argument("--db-mapping",default="outputs/phase15-db-mapping/screen_api_database_mapping_v15.json")
    egph.add_argument("--screen-registry",default="outputs/phase13-identity/screen_registry_v13.json")
    egph.add_argument("--output",default="outputs/phase16-evidence-graph")
    ak=sub.add_parser("generate-ai-knowledge")
    ak.add_argument("--graph",default="outputs/phase16-evidence-graph/evidence_graph_v16.json")
    ak.add_argument("--output",default="outputs/phase17-ai-knowledge")
    ak.add_argument("--provider",default=os.getenv("PIE_LLM_PROVIDER","offline"))
    ak.add_argument("--limit",type=int)
    ak.add_argument("--screen-id")
    ak.add_argument("--workers",type=int,default=int(os.getenv("PIE_AI_WORKERS","1")))
    cm=sub.add_parser("capability-model")
    cm.add_argument("--input",default="outputs/phase17-ai-knowledge/knowledge")
    cm.add_argument("--output",default="outputs/phase18-qdrant-kms")
    cm.add_argument("--mapping-file")
    ki=sub.add_parser("kms-init")
    ki.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
    ki.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    ki.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    kp=sub.add_parser("kms-publish")
    kp.add_argument("--input",default="outputs/phase17-ai-knowledge/knowledge")
    kp.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
    kp.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    kp.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    kp.add_argument("--published-only",action="store_true",help="Publish only records cleared for production retrieval.")
    kp.add_argument("--batch-size",type=int,default=64)
    ks=sub.add_parser("kms-search")
    ks.add_argument("--query",required=True)
    ks.add_argument("--limit",type=int,default=10)
    ks.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
    ks.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    ks.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    ks.add_argument("--include-review",action="store_true")
    kst=sub.add_parser("kms-stats")
    kst.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
    kst.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    kst.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    pub=sub.add_parser("kms-publish-json")
    pub.add_argument("--input",required=True,help="legacy JSON manifest; audit artifact only")
    pub.add_argument("--kms",default="outputs/kms")
    ub=sub.add_parser("usage-build",help="v0.19 Usage Phase: build the marketing feature catalog for one module")
    ub.add_argument("--module",required=True)
    ub.add_argument("--module-dir",help="Module-scoped phase 13-15 outputs (default outputs/modules/<module>)")
    ub.add_argument("--backend",default=os.getenv("ERP_BACKEND_PATH","./data/erp/backend"))
    ub.add_argument("--knowledge-source",choices=["local","qdrant"],default="local")
    ub.add_argument("--knowledge-dir",default="outputs/phase17-ai-knowledge/knowledge")
    ub.add_argument("--capability-map",default="capability_map.json")
    ub.add_argument("--output",help="Default <module-dir>/phase19-usage")
    ub.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
    ub.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    ub.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    ue=sub.add_parser("usage-export",help="Write the marketing UI as a standalone HTML file with catalogs embedded")
    ue.add_argument("--modules-root",default=os.getenv("PIE_MODULES_OUTPUT","outputs/modules"))
    ue.add_argument("--output",default="outputs/usage/pie_usage_marketing.html")
    fb=sub.add_parser("fields-build",help="v0.20 field-level intelligence for one GB5 form screen (default: Item Master)")
    fb.add_argument("--form",default="itemmaster")
    fb.add_argument("--module",default="inventory")
    fb.add_argument("--entity",default="Item",help="Backend entity name (DTO/BLL/QB), e.g. Item")
    fb.add_argument("--screen-id",default="INVENTORY_ITEM_MASTER")
    fb.add_argument("--screen-name",default="Item Master")
    fb.add_argument("--erp",default="data/erp")
    fb.add_argument("--module-dir",help="Default outputs/modules/<module>")
    fb.add_argument("--enrich",action="store_true",help="Add AI-derived explanations (purpose, meaning, usage, impact, example) from the facts")
    fb.add_argument("--provider",default=os.getenv("PIE_LLM_PROVIDER","offline"))
    fb.add_argument("--no-usage",action="store_true",help="Skip the ERP-wide usage scan (faster)")
    feh=sub.add_parser("fields-export-html",help="Write the Field Explorer as a standalone HTML file with all field dictionaries embedded")
    feh.add_argument("--modules-root",default=os.getenv("PIE_MODULES_OUTPUT","outputs/modules"))
    feh.add_argument("--output",default="outputs/usage/pie_field_explorer.html")
    fbm=sub.add_parser("fields-build-module",help="Field-level knowledge for EVERY form screen of a module (one ERP-wide usage scan)")
    fbm.add_argument("--module",required=True)
    fbm.add_argument("--erp",default="data/erp")
    fbm.add_argument("--module-dir",help="Default outputs/modules/<module>")
    fbm.add_argument("--forms",nargs="*",help="Only these form names")
    fbm.add_argument("--enrich",action="store_true")
    fbm.add_argument("--provider",default=os.getenv("PIE_LLM_PROVIDER","offline"))
    kpf=sub.add_parser("kms-publish-fields",help="Publish field dictionaries to the Qdrant KMS (fields + sections)")
    src_f=kpf.add_mutually_exclusive_group(required=True)
    src_f.add_argument("--dictionary",help="One <form>_fields_v20.json")
    src_f.add_argument("--module",help="All field dictionaries of outputs/modules/<module>/phase20-fields")
    uec=sub.add_parser("usage-export-chat",help="Write the Ask PIE chat as a shareable HTML file that talks to a PIE server")
    uec.add_argument("--server",required=True,help="Address users' browsers can reach, e.g. http://pie-server:8000")
    uec.add_argument("--output",default="outputs/usage/pie_ask_marketing.html")
    def qdrant_args(parser):
        parser.add_argument("--url",default=os.getenv("QDRANT_URL","http://localhost:6333"))
        parser.add_argument("--collection",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
        parser.add_argument("--api-key",default=os.getenv("QDRANT_API_KEY",""))
    kpu=sub.add_parser("kms-publish-usage",help="Publish a v0.19 usage catalog (module, capabilities, features) to the Qdrant KMS")
    kpu.add_argument("--module",required=True)
    kpu.add_argument("--catalog",help="Default outputs/modules/<module>/phase19-usage/usage_catalog_v19.json")
    qdrant_args(kpu)
    kit=sub.add_parser("kms-import-tickets",help="Import support tickets from a CSV/Excel/JSON export or a read-only SQL SELECT into the KMS")
    src=kit.add_mutually_exclusive_group(required=True)
    src.add_argument("--file")
    src.add_argument("--sql-file",help="File with one read-only SELECT statement")
    kit.add_argument("--source",default="",help="Source label stored with each ticket, e.g. redmine or gb5-crm")
    kit.add_argument("--mapping",help='JSON {"title": "Subject", "module": "Product", ...} when columns are not detected')
    kit.add_argument("--database-url")
    kit.add_argument("--dry-run",action="store_true",help="Show what would be imported without writing to Qdrant")
    qdrant_args(kit)
    qdrant_args(kpf)
    ch=sub.add_parser("chat",help="Ask the Marketing Intelligence assistant a question (Qdrant + the configured LLM)")
    ch.add_argument("--question",required=True)
    ch.add_argument("--module")
    ch.add_argument("--include-review",action="store_true")
    ch.add_argument("--audience",default="marketing",help="marketing | client_nontechnical | client_technical | developer | stakeholder")
    ch.add_argument("--provider",default=os.getenv("PIE_CHAT_LLM_PROVIDER",os.getenv("PIE_LLM_PROVIDER","offline")))
    qdrant_args(ch)
    # v0.25: semantic search, source-code index, business knowledge, documents.
    mig=sub.add_parser("kms-migrate",help="Copy a KMS collection into a new one with semantic (OpenAI + keyword) vectors")
    mig.add_argument("--source",default=os.getenv("QDRANT_COLLECTION","pie_knowledge"))
    mig.add_argument("--target",required=True)
    qdrant_args(mig)
    ci=sub.add_parser("code-index",help="Index the ERP source code (C#, TS, HTML, SQL) for developer answers (incremental)")
    ci.add_argument("--erp",default="data/erp")
    ci.add_argument("--collection",default=None,help="Default PIE_CODE_COLLECTION or pie_code_v2")
    ci.add_argument("--full",action="store_true",help="Re-index every file, not only changed ones")
    ci.add_argument("--limit-files",type=int)
    bb=sub.add_parser("business-build",help="AI-draft business value, how-to, FAQs, marketing copy, brochure and processes from verified evidence")
    bbs=bb.add_mutually_exclusive_group(required=True)
    bbs.add_argument("--module")
    bbs.add_argument("--all",action="store_true")
    bb.add_argument("--limit",type=int,help="Only the first N features per module (trial run)")
    bb.add_argument("--workers",type=int)
    bb.add_argument("--provider",default=os.getenv("PIE_LLM_PROVIDER","offline"))
    kpb=sub.add_parser("kms-publish-business",help="Publish business knowledge (business-build output) to the KMS")
    kpbs=kpb.add_mutually_exclusive_group(required=True)
    kpbs.add_argument("--module")
    kpbs.add_argument("--all",action="store_true")
    qdrant_args(kpb)
    di=sub.add_parser("docs-ingest",help="Ingest manuals, brochures, training and SOP documents (PDF, Word, PowerPoint, Excel, text)")
    di.add_argument("--path",nargs="+",default=["data/docs"],help="Folders, files or .zip archives")
    di.add_argument("--include",nargs="*",help="File-name patterns, e.g. *manual*.pdf")
    di.add_argument("--module",help="Tag every document with this module (default: from the folder name or content)")
    qdrant_args(di)
    load_dotenv()
    args=ap.parse_args()
    if args.cmd in {"intelligence-build", "intelligence-context"}:
        from .intelligence import run_command
        run_command(args)
    elif args.cmd=="run":
        pie,val,ev=run(args.input,args.output,args.llm,args.provider)
        emit({"validation":val,"evaluation":ev},indent=2)
    elif args.cmd=="run-golden":
        pie,val,ev=run("data/PIE_Data.zip",args.output,args.llm,args.provider)
        emit({"validation":val,"evaluation":ev},indent=2)
    elif args.cmd=="discover":
        report,_=run_generation(args.input,args.output)
        emit(report.model_dump(mode="json"),indent=2)
    elif args.cmd=="generate":
        report,packages=run_generation(args.input,args.output)
        emit({"status":"COMPLETED","totalScreens":len(packages),"screenIds":[p.screen.screenId for p in packages]},indent=2)
    elif args.cmd=="discover-erp":
        report=run_erp_discovery(args.frontend,args.backend,args.output,args.database_url)
        emit({"status":"COMPLETED","totalScreens":report["totalScreens"],"backendEndpoints":report["backend"]["endpoints"],"databaseStatus":report["database"].get("status")},indent=2)
    elif args.cmd=="generate-erp":
        report,packages=generate_from_erp(args.frontend,args.backend,args.output,args.database_url)
        emit({"status":"COMPLETED","totalScreens":len(packages),"screenIds":[p.screen.screenId for p in packages]},indent=2)
    elif args.cmd=="screen-identity":
        report=run_screen_identity(args.input,args.output)
        emit({"status":"COMPLETED","inputCandidates":report["inputCandidates"],"uniqueScreens":report["uniqueScreens"],"duplicatesConsolidated":report["duplicatesConsolidated"],"statusCounts":report["statusCounts"]},indent=2)
    elif args.cmd=="map-api" and args.modules:
        from .api_mapping import run_api_mapping_modules
        mods=[m.strip() for m in args.modules.split(",") if m.strip()]
        reports=run_api_mapping_modules(args.registry,args.frontend,args.backend,mods,args.modules_root,progress=_progress)
        emit({"status":"COMPLETED","modules":len(reports),"screens":sum(r["stats"].get("screens",0) for r in reports),
              "screensWithApis":sum(r["stats"].get("screensWithApis",0) for r in reports)},indent=2)
    elif args.cmd=="map-api":
        report=run_api_mapping(args.registry,args.frontend,args.backend,args.output,args.module)
        emit(report,indent=2)
    elif args.cmd=="map-db" and args.modules:
        from .db_mapping import run_db_mapping_modules
        mods=[m.strip() for m in args.modules.split(",") if m.strip()]
        reports=run_db_mapping_modules(mods,args.modules_root,args.backend,args.database_url,args.db_snapshot,progress=_progress)
        emit({"status":"COMPLETED","modules":len(reports),"database":reports[0]["database"].get("source") if reports else None,
              "apiMappings":sum(r["stats"].get("apiMappings",0) for r in reports),
              "apiMappingsWithDatabase":sum(r["stats"].get("apiMappingsWithDatabase",0) for r in reports)},indent=2)
    elif args.cmd=="map-db":
        report=run_db_mapping(args.api_mapping,args.backend,args.output,args.database_url,args.db_snapshot)
        emit(report,indent=2)
    elif args.cmd=="build-evidence-graph":
        report=build_evidence_graph(args.api_mapping,args.db_mapping,args.screen_registry,args.output)
        emit(report,indent=2)
    elif args.cmd=="generate-ai-knowledge":
        report=generate_ai_knowledge(args.graph,args.output,args.provider,args.limit,args.screen_id,args.workers)
        emit(report,indent=2)
    elif args.cmd=="capability-model":
        report=build_capability_model(args.input,args.output,args.mapping_file)
        emit({k:report[k] for k in ("status","totalScreens","capabilityReviewRequired")},indent=2)
    elif args.cmd=="kms-init":
        kms=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection)
        kms.ensure_collection()
        emit({"status":"READY","kms":"Qdrant","collection":args.collection,"url":args.url},indent=2)
    elif args.cmd=="kms-publish":
        manifest=manifest_from_knowledge_dir(args.input)
        kms=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection)
        result=kms.publish_packages(manifest["knowledgePackages"],include_review=not args.published_only,batch_size=args.batch_size)
        emit(result,indent=2)
    elif args.cmd=="kms-search":
        kms=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection)
        filt=None if args.include_review else {"must":[{"key":"publication_status","match":{"value":"PUBLISHED"}}]}
        emit(kms.search(args.query,args.limit,filt),indent=2)
    elif args.cmd=="kms-stats":
        client=QdrantHTTP(args.url,args.api_key)
        emit(client.collection(args.collection),indent=2)
    elif args.cmd=="usage-build":
        mdir=args.module_dir or f"outputs/modules/{args.module}"
        if args.knowledge_source=="qdrant":
            knowledge=load_knowledge_qdrant(QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection),args.module)
        else:
            knowledge=load_knowledge_local(args.knowledge_dir)
        catalog=build_usage_catalog(args.module,mdir,args.backend,knowledge,args.capability_map,args.output or f"{mdir}/phase19-usage")
        emit({"status":"COMPLETED","module":args.module,**{k:v for k,v in catalog["statistics"].items() if k!="sharedPlatformTables"}},indent=2)
    elif args.cmd=="usage-export":
        catalogs=[json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.modules_root).glob("*/phase19-usage/usage_catalog_v19.json"))]
        out=export_static_ui(catalogs,args.output)
        emit({"status":"COMPLETED","modules":[c["module"] for c in catalogs],"output":str(out.resolve())},indent=2)
    elif args.cmd=="fields-build":
        from .field_intel import extract_screen_fields, enrich_fields, export_fields_xlsx
        from .llm import get_provider
        mdir=Path(args.module_dir or f"outputs/modules/{args.module}")
        out=mdir/"phase20-fields"; out.mkdir(parents=True,exist_ok=True)
        d=extract_screen_fields(args.erp,args.form,args.module,args.entity,args.screen_id,args.screen_name,
                                db_registry=mdir/"phase15-db-mapping"/"database_object_registry_v15.json",
                                api_mapping=mdir/"phase14-api-mapping"/"screen_api_backend_mapping_v14.json",scan_usage=not args.no_usage)
        if args.enrich:
            d=enrich_fields(d,get_provider(args.provider),out/f"{args.form}_explanations_cache.json",progress=_progress)
            from .field_intel import validate_explanations
            d=validate_explanations(d)
        base=out/f"{args.form}_fields_v20"
        base.with_suffix(".json").write_text(json.dumps(d,indent=1,ensure_ascii=False,default=str),encoding="utf-8")
        xlsx=export_fields_xlsx(d,base.with_suffix(".xlsx"))
        emit({"status":"COMPLETED","screen":d["screen"],"table":d["table"],**d["statistics"],
                          "json":str(base.with_suffix(".json").resolve()),"excel":str(xlsx.resolve())},indent=2)
    elif args.cmd=="fields-export-html":
        from .usage import export_fields_ui
        ds=[json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.modules_root).glob("*/phase20-fields/*_fields_v20.json"))]
        out=export_fields_ui(ds,args.output)
        emit({"status":"COMPLETED","screens":[d["screen"] for d in ds],"output":str(out.resolve())},indent=2)
    elif args.cmd=="fields-build-module":
        from .field_intel import build_module_fields
        from .llm import get_provider
        mdir=Path(args.module_dir or f"outputs/modules/{args.module}")
        ds=build_module_fields(args.erp,args.module,mdir,get_provider(args.provider) if args.enrich else None,args.forms,
                               progress=_progress)
        summary=json.loads((mdir/"phase20-fields"/"module_fields_summary_v20.json").read_text(encoding="utf-8"))
        emit({"status":"COMPLETED","module":args.module,"screens":len(ds),**summary["totals"]},indent=2)
    elif args.cmd=="kms-publish-fields":
        from .knowledge_ingest import publish_field_dictionary, screen_contexts
        kms=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection)
        paths=[Path(args.dictionary)] if args.dictionary else sorted(Path(f"outputs/modules/{args.module}/phase20-fields").glob("*_fields_v20.json"))
        results,contexts=[],{}
        for p in paths:
            d=json.loads(p.read_text(encoding="utf-8"))
            # Each point also carries its screen's functionality and capability (registry + Usage catalog).
            if d["module"] not in contexts: contexts[d["module"]]=screen_contexts(f"outputs/modules/{d['module']}")
            ctx=contexts[d["module"]].get(d["screenId"])
            if not ctx: log_event(f"no functionality/capability found for {d['screenId']}","WARNING")
            results.append(publish_field_dictionary(kms,d,context=ctx))
        with_ctx=sum(1 for p in paths for m in [json.loads(p.read_text(encoding="utf-8"))] if contexts.get(m["module"],{}).get(m["screenId"],{}).get("capability"))
        emit({"status":"PUBLISHED","dictionaries":len(results),"fields":sum(r["fields"] for r in results),
                          "sections":sum(r.get("sections",0) for r in results),"screensWithCapability":with_ctx,
                          "screens":[r["screen"] for r in results]},indent=2)
    elif args.cmd=="usage-export-chat":
        out=export_chat_ui(args.server,args.output)
        emit({"status":"COMPLETED","server":args.server,"output":str(out.resolve()),
                          "note":"Start the server with --host 0.0.0.0 so other machines can reach it."},indent=2)
    elif args.cmd=="kms-publish-usage":
        path=Path(args.catalog or f"outputs/modules/{args.module}/phase19-usage/usage_catalog_v19.json")
        catalog=json.loads(path.read_text(encoding="utf-8"))
        from .knowledge_ingest import screen_contexts
        func={sid:c["functionality"] for sid,c in screen_contexts(f"outputs/modules/{catalog['module']}").items() if c.get("functionality")}
        emit(publish_usage_catalog(QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection),catalog,functionality=func),indent=2)
    elif args.cmd=="kms-import-tickets":
        mapping=json.loads(Path(args.mapping).read_text(encoding="utf-8")) if args.mapping else None
        if args.file:
            header,rows=read_ticket_file(args.file); source=args.source or Path(args.file).stem
        else:
            header,rows=read_ticket_sql(Path(args.sql_file).read_text(encoding="utf-8"),args.database_url); source=args.source or Path(args.sql_file).stem
        known=set()
        reg=Path("outputs/phase13-identity/screen_registry_v13.json")
        if reg.exists(): known={r.get("module","") for r in json.loads(reg.read_text(encoding="utf-8")) if r.get("module")}
        tickets,cols=normalize_tickets(header,rows,source,mapping,known)
        summary=ticket_summary(tickets,cols)
        if args.dry_run or not tickets:
            emit({"status":"DRY_RUN" if args.dry_run else "NOTHING_TO_IMPORT","source":source,"rowsRead":len(rows),**summary},indent=2,default=str)
        else:
            result=publish_tickets(QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection),tickets)
            emit({**result,"source":source,"rowsRead":len(rows),**summary},indent=2,default=str)
    elif args.cmd=="kms-migrate":
        from .kms_migrate import migrate
        from .embeddings import HybridEmbedder
        from .qdrant_kms import EmbeddingProvider
        src=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.source,EmbeddingProvider())
        dst=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.target,HybridEmbedder())
        emit(migrate(src,dst,progress=_progress),indent=2)
    elif args.cmd=="code-index":
        from .code_index import build_code_index, code_kms
        emit(build_code_index(args.erp,code_kms(collection=args.collection),args.full,args.limit_files,progress=_progress),indent=2)
    elif args.cmd=="business-build":
        from .business_knowledge import build_business_knowledge
        from .llm import get_provider
        llm=get_provider(args.provider)
        mods=[args.module] if args.module else sorted(p.parent.parent.name for p in Path("outputs/modules").glob("*/phase19-usage/usage_catalog_v19.json"))
        results=[]
        for m in mods:
            try:
                results.append(build_business_knowledge(m,llm,workers=args.workers,limit=args.limit,progress=_progress))
            except Exception as exc:
                results.append({"status":"FAILED","module":m,"error":f"{type(exc).__name__}: {exc}"[:300]})
                log_event(f"{m}: business-build failed: {exc}","ERROR")
        emit({"status":"COMPLETED" if all(r["status"]!="FAILED" for r in results) else "PARTIAL","modules":results,
              "features":sum(r.get("features",0) for r in results)},indent=2)
    elif args.cmd=="kms-publish-business":
        from .business_knowledge import publish_business
        kms=QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection)
        mods=[args.module] if args.module else sorted(p.parent.parent.name for p in Path("outputs/modules").glob("*/phase21-business/business_knowledge_v25.json"))
        results=[publish_business(kms,m) for m in mods]
        emit({"status":"PUBLISHED","modules":len(results),"points":sum(r["points"] for r in results),"results":results},indent=2)
    elif args.cmd=="docs-ingest":
        from .docs_ingest import ingest_documents
        emit(ingest_documents(QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection),args.path,args.include,args.module,progress=_progress),indent=2)
    elif args.cmd=="chat":
        from .chat import MarketingChat
        from .llm import get_provider
        code=None
        if os.getenv("PIE_CODE_COLLECTION"):
            from .code_index import code_kms
            code=code_kms(url=args.url,api_key=args.api_key)
        bot=MarketingChat(QdrantKMS(QdrantHTTP(args.url,args.api_key),args.collection),get_provider(args.provider),code)
        r=bot.answer(args.question,[],args.module,args.include_review,args.audience)
        from .chat import trace_digest
        log_event("answer trace",trace=trace_digest(r))
        print(r["answer"]); print()
        emit({k:r[k] for k in ("intent","grounding","plan","timings")},indent=2)
        for src_ in r["sources"]: print(f"  [{src_['ref']}] {src_['status']:8} {src_['kind']:10} {src_['title']}")
    elif args.cmd=="kms-publish-json":
        # Explicit legacy/audit command retained for backward compatibility.
        # It is NOT the KMS and is intentionally separate from `kms-publish`.
        data=json.loads(Path(args.input).read_text(encoding="utf-8"))
        from .models import KnowledgePackage
        packages=[KnowledgePackage.model_validate(x) for x in data.get("knowledgePackages",[])]
        JsonKMSStore(args.kms).publish_many(packages)
        emit({"status":"AUDIT_EXPORT_ONLY","count":len(packages),"path":str(Path(args.kms).resolve())},indent=2)


def _progress(message):
    print("  ", message, flush=True)
    log_event(message)


def emit(obj, **kw):
    """Print a command result and record its counts/status in the activity log."""
    print(json.dumps(obj, **kw))
    record_result(obj)


LONG_COMMANDS = {"kms-migrate", "code-index", "business-build", "kms-publish-business", "kms-publish-fields", "kms-publish-usage",
                 "docs-ingest", "fields-build-module", "map-api", "map-db", "generate-ai-knowledge", "kms-publish"}


def _keep_awake() -> None:
    """Long builds: ask Windows not to sleep while this process runs (released automatically when it exits).
    PIE_KEEP_AWAKE=0 turns it off; the power settings themselves are never changed."""
    if os.name != "nt" or os.getenv("PIE_KEEP_AWAKE", "1") == "0":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass


def main():
    """Run one PIE command as a tracked activity (backend/pie_agent/activity_log.py)."""
    import sys
    argv = sys.argv[1:]
    cmd = next((a for a in argv if not a.startswith("-")), "help")
    if cmd in LONG_COMMANDS:
        _keep_awake()

    # Qdrant context must not be copied into local or remote activity logs.
    if cmd in {"intelligence-build", "intelligence-context"}:
        _main()
        return

    def opt(name):
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return None

    load_dotenv()
    enrich = cmd in ("fields-build", "fields-build-module") and "--enrich" in argv
    with track("field_knowledge" if enrich else ACTIVITIES.get(cmd, "other"), command=cmd,
               source=os.getenv("PIE_LOG_SOURCE", "cli"), module=opt("--module"),
               screen=opt("--screen-name") or opt("--form"), params=argv):
        _main()
        # Keep the dashboard's knowledge inventory and Qdrant storage status current
        # (PIE_LOG_AUTO_REFRESH=0 skips this during batch builds; refresh once at the end).
        auto = os.getenv("PIE_LOG_AUTO_REFRESH", "1") != "0"
        if auto and cmd in INVENTORY_AFTER:
            try:
                refresh_inventory(url=opt("--url"), api_key=opt("--api-key"), collection=opt("--collection"), source="auto")
            except Exception:
                pass
        if auto and cmd in QDRANT_AFTER:
            try:
                qdrant_snapshot(url=opt("--url"), api_key=opt("--api-key"), collection=opt("--collection"), source="auto")
            except Exception:
                pass


if __name__=="__main__": main()
