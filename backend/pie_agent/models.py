from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict

SourceCategory = Literal[
    "Screen", "DB Schema", "Backend Code", "Frontend Code", "API / Service Response",
    "User Manual", "Training", "Report", "Ticket", "Existing PIE Output",
    "Existing PIE Prompt", "Existing Summary", "Competitor Intelligence", "Other"
]

class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str = ""
    source_file: str
    source_type: str = ""
    locator: str = ""
    quote_or_fact: str = ""
    evidence_type: Literal["observed", "derived", "inferred", "conflict"] = "observed"
    confidence: float = 0.0

class SourceRecord(BaseModel):
    fileName: str
    fileType: str
    sourceCategory: SourceCategory = "Other"
    module: str = ""
    screen: str = ""
    businessProcess: str = ""
    relevance: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    sizeBytes: int = 0
    parentArchive: str | None = None

class RelevanceScore(BaseModel):
    source: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    selected: bool = False

class Conflict(BaseModel):
    conflict: bool = True
    field: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    resolution: str = ""
    needsReview: bool = True

class Implication(BaseModel):
    ImplicationType: Literal[1,2,3,4,5,6]
    EffortDeltaHours: float = 0
    Description: str = ""
    RiskNote: str = ""

class ConfigItem(BaseModel):
    ItemCode: str
    ItemName: str
    ItemCategory: Literal[
        "BizTransaction","Workflow","Role","DocumentFormat","MailScheduler","Infrastructure"
    ] = "BizTransaction"
    Description: str = ""
    PlainLanguageDesc: str = ""
    IsMandatory: int = 0
    DefaultValue: str | None = None
    EstimatedHours: float = 0
    needsReview: bool = False
    reviewReason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

class Variant(BaseModel):
    VariantCode: str
    VariantName: str
    Description: str = ""
    PlainLanguageDesc: str = ""
    IsDefault: int = 0
    Implications: list[Implication] = Field(default_factory=list)
    needsReview: bool = False
    reviewReason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

class TrainingTopic(BaseModel):
    TopicCode: str
    TopicName: str
    MenuPath: str = ""
    TopicCategory: Literal["Master","Transaction","Report","Process"] = "Process"
    EstimatedMinutes: int = 0
    SequenceOrder: int = 0
    IsMandatory: int = 0
    Steps: list[str] = Field(default_factory=list)
    needsReview: bool = False
    reviewReason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

class UATScenario(BaseModel):
    ScenarioCode: str
    ScenarioTitle: str
    TransactionType: str = ""
    Preconditions: str = ""
    Steps: list[str] = Field(default_factory=list)
    ExpectedResult: str = ""
    EstimatedMinutes: int = 0
    Complexity: int = 1
    IsMandatory: int = 0
    needsReview: bool = False
    reviewReason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

class EstimationRule(BaseModel):
    Feature: str
    BaseHours: float = 0
    EngagementType: Literal["Standard","Configure","Custom"] = "Standard"
    ComplexityLevel: Literal["Simple","Medium","Complex"] = "Simple"
    ConfidenceScore: int = 60
    needsReview: bool = False
    evidence: list[Evidence] = Field(default_factory=list)

class KPI(BaseModel):
    KpiCode: str
    KpiName: str
    KpiCategory: int = 0
    Unit: str = ""
    Description: str = ""
    MeasurementGuide: str = ""
    GoodPerformance: str = ""
    AveragePerformance: str = ""
    PoorPerformance: str = ""
    needsReview: bool = True
    evidence: list[Evidence] = Field(default_factory=list)

class ModuleInfo(BaseModel):
    ModuleCode: str = ""
    ModuleName: str = ""
    FunctionalArea: str = ""
    SubModule: str = ""
    Description: str = ""
    ConfidenceScore: float = 0

class ReviewItem(BaseModel):
    entity: str
    field: str
    currentValue: Any = None
    reason: str
    evidence: list[str] = Field(default_factory=list)

class Traceability(BaseModel):
    playbook: str = "PIE_Content_Prep_Playbook.html"
    methods: list[str] = Field(default_factory=list)
    promptSources: list[str] = Field(default_factory=list)
    sourceFiles: list[str] = Field(default_factory=list)
    generatedPrompt: str = ""
    modelOutput: str = ""
    postProcessing: list[str] = Field(default_factory=list)
    validationResult: dict[str, Any] = Field(default_factory=dict)


class ScreenRegistryEntry(BaseModel):
    screenId: str
    module: str = ""
    functionality: str = ""
    capability: str = ""
    feature: str = ""
    screenName: str = ""
    status: Literal["DISCOVERED", "READY", "STALE", "ERROR"] = "DISCOVERED"
    discoveryConfidence: float = 0.0
    sourceFiles: list[str] = Field(default_factory=list)
    sourceCategories: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

class KnowledgeRecord(BaseModel):
    knowledgeId: str
    screenId: str
    module: str = ""
    functionality: str = ""
    capability: str = ""
    feature: str = ""
    knowledgeType: str
    title: str
    content: str = ""
    confidence: float = 0.0
    needsReview: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    sourceFactIds: list[str] = Field(default_factory=list)
    generationMethod: Literal["deterministic", "ai", "hybrid"] = "deterministic"
    claimType: Literal["observed", "derived", "inferred"] = "observed"

class KnowledgePackage(BaseModel):
    packageVersion: str = "1.1"
    screen: ScreenRegistryEntry
    records: list[KnowledgeRecord] = Field(default_factory=list)
    generatedAt: str = ""
    sourceHash: str = ""
    status: Literal["READY", "REVIEW_REQUIRED", "ERROR"] = "REVIEW_REQUIRED"
    generationPhase: str = "ERP-wide AI Knowledge Generation"
    graphVersion: str = "0.16.0"
    reviewReasons: list[str] = Field(default_factory=list)

class DiscoveryReport(BaseModel):
    discoveredAt: str = ""
    input: str = ""
    totalFiles: int = 0
    totalScreens: int = 0
    screens: list[ScreenRegistryEntry] = Field(default_factory=list)

class PIEOutput(BaseModel):
    module: ModuleInfo
    screen: dict[str, Any] = Field(default_factory=dict)
    configItems: list[ConfigItem] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    trainingTopics: list[TrainingTopic] = Field(default_factory=list)
    uatScenarios: list[UATScenario] = Field(default_factory=list)
    estimationRules: list[EstimationRule] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    businessSummary: list[dict[str, Any]] = Field(default_factory=list)
    sourceEvidence: list[Evidence] = Field(default_factory=list)
    reviewItems: list[ReviewItem] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    traceability: Traceability = Field(default_factory=Traceability)
