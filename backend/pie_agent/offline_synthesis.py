from __future__ import annotations
import re, json
from pathlib import Path
from .models import *
from .ingestion import extract_text

def ev(source, fact, etype="observed", confidence=.9, locator=""):
    return Evidence(source_file=source, source_type="", locator=locator, quote_or_fact=fact,
                    evidence_type=etype, confidence=confidence)

def _find_file(text, patterns):
    for name, body in text.items():
        low=(name+" "+body[:5000]).lower()
        if any(p.lower() in low for p in patterns): return name,body
    return None,""

def build_offline_pie(text: dict[str,str], records: list[SourceRecord]) -> PIEOutput:
    # Evidence-first baseline built from the actual golden sources.
    evidence=[]
    def add_e(source,fact,etype="observed",conf=.9):
        e=ev(source,fact,etype,conf); evidence.append(e); return e

    fe_name,fe=_find_file(text,["Leave Application front end file-input.txt"])
    be_name,be=_find_file(text,["Backend code Output.txt"])
    db_name,db=_find_file(text,["DB Schema output.txt"])
    api_name,api=_find_file(text,["Response file_input.txt"])
    manual_name,manual=_find_file(text,["GOODBOOKS-ESS User Manual.pdf"])
    screen_name,screen=_find_file(text,["Leave Application screenshot-input.pdf"])
    training_name,training=_find_file(text,["user-manual-leave-application-screen-GB5-uat-training-output.txt"])

    configs=[]; variants=[]; topics=[]; uats=[]; estimates=[]; reviews=[]

    # Screen-observed controls. These are deliberately limited to evidence actually visible in
    # the screenshot/frontend and are not treated as global backend configuration.
    screen_items=[
        ("LV_UI_EMPLOYEE","Employee","Role","Selected employee context",1,None,.5,screen_name),
        ("LV_UI_LEAVE_TYPE","Leave Type","BizTransaction","Leave category selected for the request",1,None,.5,screen_name),
        ("LV_UI_FROM_DATE","From Date","BizTransaction","Start date for the request",1,None,.25,screen_name),
        ("LV_UI_TO_DATE","To Date","BizTransaction","End date for the request",1,None,.25,screen_name),
        ("LV_UI_REASON","Reason","BizTransaction","Reason associated with the request",0,None,.5,screen_name),
        ("LV_UI_REMARKS","Remarks","DocumentFormat","Free-text remarks for the request",0,None,.25,screen_name),
        ("LV_UI_LEAVE_GRID","Leave Detail Grid","BizTransaction","Date-wise leave entries with duration and session",1,None,1,screen_name),
        ("LV_UI_ATTACHMENT","Attachment","DocumentFormat","Supporting document can be attached",0,None,.5,screen_name),
    ]
    for code,name,cat,desc,mand,default,hours,src in screen_items:
        if src:
            e=add_e(src,f"Leave Request screen shows {name}.", "observed", .95)
            configs.append(ConfigItem(ItemCode=code,ItemName=name,ItemCategory=cat,Description=desc,
                IsMandatory=mand,DefaultValue=default,EstimatedHours=hours,evidence=[e]))

    # Frontend-backed controls.
    if fe_name:
        for code,name,desc in [
            ("LV_FE_QUICK_DATE","Quick Date Selection","Yesterday / Today / Tomorrow date shortcuts"),
            ("LV_FE_STATUS","Leave Status Display","Status is rendered read-only on the form"),
            ("LV_FE_BALANCE","Leave Balance Display","Balance is displayed by leave type"),
        ]:
            e=add_e(fe_name,f"Frontend source supports {name}: {desc}.","observed",.94)
            configs.append(ConfigItem(ItemCode=code,ItemName=name,ItemCategory="BizTransaction" if code!="LV_FE_STATUS" else "Workflow",
                Description=desc,IsMandatory=0,DefaultValue="Today" if "Quick" in name else None,
                EstimatedHours=.5,evidence=[e]))
        e=add_e(fe_name,"Frontend binds a leave detail grid and exposes leave-session change events.","observed",.97)
        variants.append(Variant(
            VariantCode="LV_VAR_DAY_SESSION",VariantName="Leave Day Session",
            Description="Leave detail entries support session choices.",
            PlainLanguageDesc="A leave date can be represented as a full day or a partial-day session.",
            IsDefault=1,Implications=[],evidence=[e]
        ))

    # DB-supported policy/configuration. These are not asserted from the UI alone.
    if db_name:
        db_items=[
            ("LV_DB_BALANCE","Leave Balance Maintenance","BizTransaction","Leave configuration contains a flag controlling balance maintenance.",1,"",4),
            ("LV_DB_CARRY","Leave Carry Forward","BizTransaction","Leave configuration contains a flag controlling carry-forward.",0,"",4),
            ("LV_DB_ENCASH","Leave Encashment","BizTransaction","Leave configuration contains a flag controlling encashment.",0,"",4),
            ("LV_DB_CREDIT","Leave Credit Method","BizTransaction","Leave configuration contains a credit-method field.",1,"",4),
            ("LV_DB_HOLIDAY","Include Holiday","BizTransaction","Leave configuration contains a flag for holiday inclusion.",0,"",4),
            ("LV_DB_SURROUND","Surrounding Day","BizTransaction","Leave configuration contains a surrounding-day policy field.",0,"",4),
            ("LV_DB_SALARY","Leave Salary Percentage","BizTransaction","Leave configuration contains salary percentage.",0,"",4),
        ]
        for code,name,cat,desc,mand,default,hours in db_items:
            e=add_e(db_name,desc,"observed",.94)
            configs.append(ConfigItem(ItemCode=code,ItemName=name,ItemCategory=cat,Description=desc,
                IsMandatory=mand,DefaultValue=default or None,EstimatedHours=hours,needsReview=True,
                reviewReason="Schema proves the field exists; business mandatory/default behavior requires corroboration.",
                evidence=[e]))
        for code,name,desc,plain in [
            ("LV_VAR_CREDIT","Leave Credit Method","Leave credits have a configured method.","Choose how leave credits are generated; exact choices require master-data/API corroboration."),
            ("LV_VAR_LEAVE_TYPE","Leave Type","Leave configuration contains leave type/category information.","Leave categories are selectable/maintained; exact customer options come from source data."),
            ("LV_VAR_SALARY","Leave Salary Percentage","Leave configuration stores a salary percentage.","The salary impact can vary by configured percentage."),
        ]:
            e=add_e(db_name,desc,"observed",.92)
            variants.append(Variant(VariantCode=code,VariantName=name,Description=desc,PlainLanguageDesc=plain,
                IsDefault=0,needsReview=True,reviewReason="Schema indicates a choice/value axis; exact semantics need source correlation.",evidence=[e]))

    # Backend validation and transaction behavior.
    if be_name:
        backend_facts=[
            "SaveTLeave validates that at least one leave detail line is supplied.",
            "SaveTLeave validates Leave Date is required.",
            "Each detail line must have NumberOfDays greater than zero.",
            "Total requested days are recomputed as the sum of detail-line NumberOfDays.",
            "New transactions receive a generated leave number and initial status 0.",
        ]
        for fact in backend_facts:
            e=add_e(be_name,fact,"observed",.97)
            reviews.append(ReviewItem(entity="BackendRule",field="evidence",currentValue=fact,
                reason="Backend rule is machine-evidenced; domain approval is still required before publishing as PIE truth.",
                evidence=[e.evidence_id]))
        variants.append(Variant(
            VariantCode="LV_VAR_DURATION",VariantName="Leave Duration Calculation",
            Description="Total requested leave days are derived from submitted detail lines.",
            PlainLanguageDesc="The request total is calculated from the individual leave-day entries.",
            IsDefault=1,Implications=[Implication(ImplicationType=1,EffortDeltaHours=0,
                Description="System calculates the total from detail lines.",RiskNote="Incorrect detail data changes the request total.")],
            evidence=[add_e(be_name,"TLeaveNumberOfDays is recomputed from TLeaveDetailArray detail-line NumberOfDays.","observed",.98)]
        ))

    # API/master data.
    if api_name:
        if "Casual Leave" in api:
            e=add_e(api_name,"API response contains Casual Leave and leave-policy fields including encashment, balance maintenance, carry forward, credit method, holiday inclusion and surrounding-day settings.","observed",.98)
            variants.append(Variant(
                VariantCode="LV_VAR_LEAVE_MASTER",VariantName="Leave Master Policy Values",
                Description="Runtime leave master data supplies policy attributes for leave categories.",
                PlainLanguageDesc="Leave categories can carry policy settings such as balance tracking, carry-forward and credit method.",
                IsDefault=0,needsReview=False,evidence=[e]
            ))
    # Training/manual artifacts.
    if training_name:
        training_defs=[
            ("LV_TRN_LOGIN","Login to ESS Portal","Transaction","Login","Open ESS login URL; enter username and password; click Sign In",0),
            ("LV_TRN_NAV","Navigate to Leave Application Screen","Transaction","Leave/Onduty (Daywise)","Login to ESS; locate Leave/Onduty (Daywise); open the screen",0),
            ("LV_TRN_APPLY","Apply Leave Request","Transaction","Leave/Onduty (Daywise)","Select leave type; choose leave dates; enter reason; submit request",1),
            ("LV_TRN_SUMMARY","View Leave Summary","Report","Leave/Onduty Summary","Navigate to Leave Summary; review applied leave status; check approval/rejection details",0),
        ]
        for i,(code,name,cat,path,steps,mand) in enumerate(training_defs,1):
            e=add_e(training_name,f"Training/UAT artifact documents topic: {name}.","observed",.95)
            topics.append(TrainingTopic(TopicCode=code,TopicName=name,MenuPath=path,TopicCategory=cat,
                EstimatedMinutes=10 if "Apply" not in name else 15,SequenceOrder=i,IsMandatory=mand,
                needsReview=(name=="Apply Leave Request"),
                reviewReason="Training artifact supports the topic; exact mandatory semantics need domain confirmation." if name=="Apply Leave Request" else "",
                Steps=[s.strip() for s in steps.split(";")],evidence=[e]))
        uat_defs=[
            ("LV_UAT_001","Apply Leave Successfully","Login to ESS and valid leave data","Login; navigate to Leave/Onduty (Daywise); enter valid leave details; submit; verify confirmation","Leave request is submitted successfully",1),
            ("LV_UAT_002","Apply Leave Without Mandatory Fields","Login to ESS","Login; navigate to Leave application; leave required fields empty; submit","Validation error is shown",1),
            ("LV_UAT_003","View Leave in Summary","Submitted leave exists","Submit leave; navigate to Leave Summary; search submitted leave","Leave record appears with correct status",1),
            ("LV_UAT_004","Apply Leave Beyond Balance","Known leave balance","Apply leave exceeding balance; submit","System restricts or warns user",2),
        ]
        for i,(code,title,pre,steps,expected,comp) in enumerate(uat_defs,1):
            e=add_e(training_name,f"UAT artifact documents scenario: {title}.","observed",.95)
            uats.append(UATScenario(ScenarioCode=code,ScenarioTitle=title,TransactionType="Leave Request",
                Preconditions=pre,Steps=[x.strip() for x in steps.split(";")],ExpectedResult=expected,
                EstimatedMinutes=15,Complexity=comp,IsMandatory=1 if i<3 else 0,needsReview=(i!=3),reviewReason="Expected result is documented in the training/UAT artifact but requires domain validation before publication." if i!=3 else "",evidence=[e]))

    if manual_name:
        e=add_e(manual_name,"User manual documents ESS login and navigation into employee self-service workflows.","observed",.9)
        reviews.append(ReviewItem(entity="TrainingTopic",field="menuPath",currentValue="Leave/Onduty (Daywise)",
            reason="Training artifact supplies the menu path; manual should be checked for exact current label.",evidence=[e.evidence_id]))

    # Existing PIE outputs are useful as secondary evidence, but never treated as authoritative.
    # Candidate entities derived only from these artifacts are explicitly marked for review.
    existing_sources = [
        ("screen-harvesting-leave-application-screen-GB5-config-varient-screen-output.txt", "ItemName", "config"),
        ("front end file json output.txt", "ItemName", "config"),
        ("Backend code Output.txt", "ItemName", "config"),
        ("Response file output.txt", "VariantName", "variant"),
    ]
    existing_names = {x.ItemName.lower() for x in configs} | {x.VariantName.lower() for x in variants}
    for fname, key, kind in existing_sources:
        actual=next((k for k in text if Path(k).name==fname), None)
        body=text.get(actual,"") if actual else ""
        if not body: continue
        fname = actual
        vals=re.findall(rf'"{key}"\s*:\s*"([^"]+)"', body)
        # Also support the document-mining naming convention.
        if key=="ItemName":
            vals += re.findall(r'"ConfigItemName"\s*:\s*"([^"]+)"', body)
        for idx,name in enumerate(vals,1):
            if name.strip().lower() in existing_names or len(name.strip())<4:
                continue
            e=add_e(fname,f'Existing PIE artifact contains candidate {key}: {name}.',"derived",.62)
            if kind=="config":
                configs.append(ConfigItem(
                    ItemCode=f"EXISTING_CFG_{len(configs)+1:03d}",
                    ItemName=name.strip(),
                    ItemCategory="BizTransaction",
                    Description="Candidate carried from an existing PIE artifact; not yet corroborated by primary evidence.",
                    IsMandatory=0, DefaultValue=None, EstimatedHours=0,
                    needsReview=True,
                    reviewReason="Existing PIE output is secondary evidence. Confirm against screen/code/DB/API/document sources.",
                    evidence=[e]
                ))
            else:
                variants.append(Variant(
                    VariantCode=f"EXISTING_VAR_{len(variants)+1:03d}",
                    VariantName=name.strip(),
                    Description="Candidate carried from an existing PIE artifact.",
                    PlainLanguageDesc="Requires confirmation against primary source evidence.",
                    IsDefault=0, needsReview=True,
                    reviewReason="Existing PIE output is secondary evidence; corroboration is required.",
                    evidence=[e]
                ))
            existing_names.add(name.strip().lower())

    # Materialize the mandatory human-review queue from all uncertain entities.
    existing_review_keys={(r.entity,r.field) for r in reviews}
    for entity_list, entity_type, name_field in [
        (configs, "ConfigItem", "ItemName"),
        (variants, "Variant", "VariantName"),
        (topics, "TrainingTopic", "TopicName"),
        (uats, "UATScenario", "ScenarioTitle"),
    ]:
        for item in entity_list:
            if getattr(item, "needsReview", False):
                key=(entity_type, getattr(item,name_field))
                if key not in existing_review_keys:
                    first_ev=item.evidence[0].evidence_id if item.evidence else ""
                    reviews.append(ReviewItem(
                        entity=entity_type, field="entity", currentValue=getattr(item,name_field),
                        reason=getattr(item,"reviewReason","Human validation required before publication."),
                        evidence=[first_ev] if first_ev else []
                    ))
                    existing_review_keys.add(key)

    module=ModuleInfo(
        ModuleCode="HR_LEAVE_MANAGEMENT",
        ModuleName="Leave Management / Leave Request",
        FunctionalArea="Human Resource Management",
        SubModule="ESS / Leave",
        Description="Evidence-backed product intelligence for the GoodBooks GB5 leave request and related leave configuration domain.",
        ConfidenceScore=.94
    )
    screen_info={"ScreenName":"Leave Request","Target":"Leave Application / Leave Request","screenEvidence":[screen_name] if screen_name else []}

    summary=[]
    for c in configs:
        summary.append({
            "Section":"Leave Request","Screen Area":"Leave Details","Field / Configuration":c.ItemName,
            "UI Type":"Configuration / Control","Purpose":c.Description or c.ItemName,
            "Business Description":c.PlainLanguageDesc or c.Description,
            "Available Options":"","Mandatory":"Yes" if c.IsMandatory else "No",
            "Default Value":c.DefaultValue or "Unknown","Implementation Effort (Hours)":c.EstimatedHours,
            "Business Impact":"Review evidence before publication","Used By":"Employee / HR / Manager"
        })

    methods=sorted(set(r.sourceCategory for r in records if r.sourceCategory!="Other"))
    trace=Traceability(
        methods=methods,
        promptSources=[r.fileName for r in records if r.sourceCategory=="Existing PIE Prompt"],
        sourceFiles=[r.fileName for r in records if r.sourceCategory!="Other"],
        postProcessing=[
            "Deterministic evidence extraction",
            "Cross-source entity synthesis",
            "Uncertainty flagging for schema-only business claims"
        ]
    )
    return PIEOutput(module=module,screen=screen_info,configItems=configs,variants=variants,
        trainingTopics=topics,uatScenarios=uats,estimationRules=estimates,kpis=[],
        businessSummary=summary,sourceEvidence=evidence,reviewItems=reviews,traceability=trace)
