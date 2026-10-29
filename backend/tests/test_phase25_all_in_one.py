"""v0.25 all-in-one knowledge: semantic search, source-code index, business knowledge, documents, chat routing."""
import json
import math
import zipfile
from types import SimpleNamespace

import pytest

from pie_agent import business_knowledge as B
from pie_agent.chat import MarketingChat, detect_intents, load_snapshot
from pie_agent.code_index import build_code_index, chunk_lines, file_chunks, numbered
from pie_agent.docs_ingest import guess_module, ingest_documents, passages, read_document
from pie_agent.embeddings import DENSE, LEXICAL, HybridEmbedder, OpenAIDense, fuse
from pie_agent.kms_migrate import embed_text, migrate
from pie_agent.qdrant_kms import EmbeddingProvider, QdrantKMS
from test_phase19_chat import CATALOG, FakeLLM, FakeQdrant
from pie_agent.knowledge_ingest import publish_usage_catalog


# ---------------------------------------------------------------- fakes

class FakeEmbeddings:
    """OpenAI client stand-in: a deterministic bag-of-words vector, records each request."""

    def __init__(self, dim=64):
        self.dim, self.requests = dim, []
        self.embeddings = SimpleNamespace(create=self.create)

    def vec(self, text):
        v = [0.0] * self.dim
        for w in text.lower().split():
            v[hash(w.strip(".,:;()")) % self.dim] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]

    def create(self, model, input):
        self.requests.append(list(input))
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=self.vec(t)) for i, t in enumerate(input)],
                               usage=SimpleNamespace(total_tokens=sum(len(t.split()) for t in input)))


def hybrid(client=None):
    d = OpenAIDense("text-embedding-3-small", client or FakeEmbeddings())
    d.dimension = 64
    return HybridEmbedder(d)


class FakeHybridQdrant(FakeQdrant):
    """Named vectors (dense + lexical) like a v0.25 collection."""

    def __init__(self):
        super().__init__()
        self.created = None
        self.deleted = []

    def ensure_collection(self, name, size, index_fields=()):
        self.created = size
        return {"result": {"config": {"params": {"vectors": size if isinstance(size, dict) else {"size": size}}}}}

    def collection(self, name):
        return {"result": {"points_count": len(self.points)}}

    def search(self, name, vector, limit=10, query_filter=None):
        vname, vec = (vector["name"], vector["vector"]) if isinstance(vector, dict) else (None, vector)
        rows = [p for p in self.points.values() if self._ok(p, query_filter)]
        pick = lambda p: p["vector"][vname] if vname else p["vector"]
        sim = lambda p: sum(a * b for a, b in zip(vec, pick(p)))
        rows.sort(key=sim, reverse=True)
        return {"result": [{"id": p["id"], "score": sim(p), "payload": p["payload"]} for p in rows[:limit] if sim(p) > 0]}

    def delete_by_filter(self, name, flt):
        self.deleted.append(flt)
        for pid in [k for k, p in self.points.items() if self._ok(p, flt)]:
            del self.points[pid]
        return {"status": "ok"}


# ---------------------------------------------------------------- embeddings

def test_hybrid_embedder_batches_and_uses_named_vectors():
    client = FakeEmbeddings()
    e = hybrid(client)
    e.prime(["stock reorder level", "bill of materials", "stock reorder level"])
    assert len(client.requests) == 1 and len(client.requests[0]) == 2        # one batched request, de-duplicated
    v = e.embed("bill of materials")
    assert set(v) == {DENSE, LEXICAL} and len(client.requests) == 1          # primed: no new request
    e.embed("not primed")
    assert len(client.requests) == 2
    assert set(e.vectors_config()) == {DENSE, LEXICAL}


def test_hybrid_search_fuses_meaning_and_keywords():
    q = FakeHybridQdrant()
    kms = QdrantKMS(q, "kb", hybrid())
    kms.upsert_points([{"id": "a", "text": "reorder level keeps stock from running out", "payload": {"title": "a"}},
                       {"id": "b", "text": "MITEM item master table", "payload": {"title": "b"}}])
    assert q.created and DENSE in q.created
    hits = kms.search("stock running out")["result"]
    assert hits[0]["id"] == "a"
    assert fuse([{"id": 1, "score": .9}], [{"id": 2, "score": .9}], 5)[0]["id"] == 1   # meaning weighs more


def test_existing_hybrid_collection_is_detected_without_env():
    q = FakeHybridQdrant()
    q.ensure_collection = lambda name, size, *a: {"result": {"config": {"params": {"vectors": {"dense": {"size": 1536}, "lexical": {"size": 384}}}}}}
    kms = QdrantKMS(q, "kb")
    assert not kms.hybrid
    kms.ensure_collection()
    assert kms.hybrid and kms.embedder.dense.model == "text-embedding-3-small"


def test_migrate_copies_payloads_with_new_vectors():
    src_q, dst_q = FakeQdrant(), FakeHybridQdrant()
    src = QdrantKMS(src_q, "old", EmbeddingProvider())
    publish_usage_catalog(src, CATALOG)
    r = migrate(src, QdrantKMS(dst_q, "new", hybrid()), progress=lambda m: None)
    assert r["points"] == len(src_q.points) == len(dst_q.points)
    assert {p["payload"]["knowledge_id"] for p in dst_q.points.values()} == {p["payload"]["knowledge_id"] for p in src_q.points.values()}
    assert all(isinstance(p["vector"], dict) for p in dst_q.points.values())
    again = migrate(src, QdrantKMS(dst_q, "new", hybrid()), progress=lambda m: None)
    assert again["points"] == 0 and again["alreadyPresent"] == r["points"]      # resumable: nothing re-uploaded
    assert "MITEM" in embed_text({"knowledge_type": "FieldKnowledge", "title": "t", "field_column": "MITEM"})
    with pytest.raises(ValueError):
        migrate(src, src)


# ---------------------------------------------------------------- code index

CS = "namespace GB5.MM\n{\n    public class BOMBLL : IBOMBLL\n    {\n" + "".join(
    f"        public async Task<int> Method{i}(BOMDTO dto)\n        {{\n" + "".join(f"            var x{j} = dto.Qty * {j};\n" for j in range(25)) + "        }\n"
    for i in range(4)) + "    }\n}\n"


def test_code_chunks_follow_members_and_keep_line_numbers():
    spans = chunk_lines(CS.split("\n"), "csharp")
    assert spans[0][0] == 1 and spans[-1][1] == len(CS.split("\n"))
    assert all(b - a + 1 <= 151 for a, b in spans)
    chunks = file_chunks(CS, "backend/GB5Solution/MM/MMBLL/BOM/BOMBLL.cs", "abc")
    p = chunks[1]["payload"]
    assert p["layer"] == "bll" and p["code_module"] == "mm" and p["class_name"] == "BOMBLL" and p["namespace"] == "GB5.MM"
    assert any(m.startswith("Method") for m in p["members"])
    text = numbered(p)
    assert text.split("\n")[0].lstrip().startswith(str(p["start_line"]) + "|")


def test_code_index_is_incremental(tmp_path):
    erp = tmp_path / "erp"
    f = erp / "backend/GB5Solution/MM/MMSL/EndPoints/BOM/SaveBOM.cs"
    f.parent.mkdir(parents=True)
    f.write_text(CS, encoding="utf-8")
    (erp / "frontend/projects/inventory/bom").mkdir(parents=True)
    (erp / "frontend/projects/inventory/bom/bom.component.ts").write_text("export class BomComponent {\n  save() {\n    return 1;\n  }\n}\n")
    q = FakeHybridQdrant()
    kms = QdrantKMS(q, "code", hybrid(), index_fields=("file",))
    r1 = build_code_index(erp, kms, progress=lambda m: None)
    assert r1["filesIndexed"] == 2 and r1["chunksWritten"] == len(q.points) > 2
    assert {p["payload"]["layer"] for p in q.points.values()} == {"endpoint", "component"}
    r2 = build_code_index(erp, kms, progress=lambda m: None)
    assert r2["filesIndexed"] == 0                                       # unchanged files are skipped
    f.write_text(CS.replace("Qty", "Quantity"), encoding="utf-8")
    r3 = build_code_index(erp, kms, progress=lambda m: None)
    assert r3["filesIndexed"] == 1 and q.deleted                          # changed file: old chunks removed first
    assert all("Qty" not in p["payload"]["content"] for p in q.points.values() if p["payload"]["file"].endswith("SaveBOM.cs"))


# ---------------------------------------------------------------- documents

def _docx(path, paras):
    body = "".join(f'<w:p><w:pPr><w:pStyle w:val="{"Heading1" if h else "Normal"}"/></w:pPr><w:r><w:t>{t}</w:t></w:r></w:p>' for h, t in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document><w:body>{body}</w:body></w:document>')


def test_documents_are_read_split_and_tagged(tmp_path):
    d = tmp_path / "docs" / "inventory"
    d.mkdir(parents=True)
    _docx(d / "Item Manual.docx", [(True, "Creating an item"), (False, "Open Item Master and enter the Item Code. " * 30),
                                   (False, "Save the record to create the item. " * 30)])
    (d / "notes.md").write_text("# Gate entry\n\n" + "Record vehicle arrival at the gate. " * 80, encoding="utf-8")
    secs = read_document(d / "Item Manual.docx")
    assert "# Creating an item" in secs[0][1]
    assert len(passages(secs)) >= 2
    assert guess_module("inventory/x.pdf", "", ["inventory", "ess"]) == "inventory"
    q = FakeHybridQdrant()
    r = ingest_documents(QdrantKMS(q, "kb", hybrid()), [str(tmp_path / "docs")], progress=lambda m: None)
    assert r["documents"] == 2 and r["passages"] == len(q.points)
    assert {p["payload"]["module"] for p in q.points.values()} == {"inventory"}
    assert all(p["payload"]["knowledge_type"] == "Document" for p in q.points.values())
    ingest_documents(QdrantKMS(q, "kb", hybrid()), [str(tmp_path / "docs")], progress=lambda m: None)
    assert r["passages"] == len(q.points)                                # re-ingest replaces, never duplicates


# ---------------------------------------------------------------- business knowledge

DRAFT = {"overview": "Bills of materials define the components of each product.", "users": ["Production planner"],
         "benefits": ["Fewer errors: BOM Name is mandatory."], "useCases": ["Define a new assembly"],
         "industries": [{"industry": "Pump manufacturing", "why": "multi-level assemblies"}],
         "howTo": {"title": "How to create a BOM", "steps": ["Open BOM", "Enter BOM Name (mandatory)", "Save"]},
         "faqs": [{"q": "Why can't I save a BOM with a past date?", "a": "Assembly Type BOM cannot be created with a past From Date."}],
         "marketing": {"headline": "Engineer products right first time", "blurb": "Define every assembly with real-time dashboards " * 3, "bullets": ["Validated BOMs"]},
         "confidence": "high"}


class BizLLM(FakeLLM):
    def generate(self, prompt):
        self.calls.append(("gen", prompt))
        if "SHARED DATA FLOWS" in prompt:
            return json.dumps({"processes": [{"name": "Receive and build", "summary": "s",
                                              "steps": [{"screen": "Gate Entry", "action": "record arrival"}, {"screen": "BOM", "action": "build"},
                                                        {"screen": "Invented Screen", "action": "x"}]},
                                             {"name": "Too short", "steps": [{"screen": "BOM", "action": "x"}]}]})
        if "product brochure section" in prompt:
            return json.dumps({"tagline": "Inventory under control", "overview": "Inventory overview.", "capabilities": [], "idealFor": ["Stores"], "keyFacts": ["BOM Name is validated"]})
        return json.dumps(DRAFT)


@pytest.fixture
def module_dir(tmp_path):
    m = tmp_path / "inventory"
    (m / "phase19-usage").mkdir(parents=True)
    cat = json.loads(json.dumps(CATALOG))
    for f in cat["features"]:
        f["technical"] = {"tables": [{"name": "TGATEENTRY", "role": "write" if f["name"] == "Gate Entry" else "read", "confidence": 1, "shared": False}]}
    (m / "phase19-usage" / "usage_catalog_v19.json").write_text(json.dumps(cat), encoding="utf-8")
    return m


def test_business_build_drafts_caches_and_validates(module_dir):
    llm = BizLLM()
    r = B.build_business_knowledge("inventory", llm, module_dir, workers=2, progress=lambda m: None)
    assert r["features"] == 2 and r["brochure"] and r["processes"] == 1 and not r["failures"]
    pkg = json.loads((module_dir / "phase21-business" / "business_knowledge_v25.json").read_text(encoding="utf-8"))
    steps = pkg["processes"]["draft"]["processes"][0]["steps"]
    assert [s["screen"] for s in steps] == ["Gate Entry", "BOM"]           # invented screen dropped; 1-step process dropped
    calls = len(llm.calls)
    B.build_business_knowledge("inventory", llm, module_dir, progress=lambda m: None)
    assert len(llm.calls) == calls                                         # cached by fact fingerprint: nothing paid again


def test_business_points_are_ai_labelled_and_flag_unsupported_claims(module_dir):
    B.build_business_knowledge("inventory", BizLLM(), module_dir, progress=lambda m: None)
    q = FakeHybridQdrant()
    r = B.publish_business(QdrantKMS(q, "kb", hybrid()), "inventory", module_dir)
    kinds = r["byType"]
    assert kinds["BusinessValue"] == 2 and kinds["HowTo"] == 2 and kinds["FAQ"] == 2 and kinds["ModuleBrochure"] == 1 and kinds["Process"] == 1
    payloads = [p["payload"] for p in q.points.values()]
    assert all(p["generation_method"] == "ai" and p["publication_status"] == "PUBLISHED" for p in payloads)
    copy = next(p for p in payloads if p["knowledge_type"] == "MarketingCopy")
    assert "real-time" in copy["review_note"] and r["flagged"] >= 1        # claim word not in the evidence
    B.publish_business(QdrantKMS(q, "kb", hybrid()), "inventory", module_dir)
    assert len(q.points) == r["points"]                                   # republish replaces


# ---------------------------------------------------------------- chat routing

def test_new_intents():
    assert detect_intents("Write a brochure for the Inventory module")[0] == "content"
    assert detect_intents("How do I create a BOM?")[0] == "howto"
    assert detect_intents("Why can't I save the gate entry?")[0] == "support"
    assert detect_intents("Explain the procure-to-pay process")[0] == "process"
    assert detect_intents("Which line throws the null reference exception in SaveBOM?")[0] == "code"
    assert "code" not in detect_intents("What is the Leave Credit Method field?")
    assert detect_intents("Prepare a demo flow for a pump industry's IT Manager")[0] == "demo"


class AnswerLLM(FakeLLM):
    def chat(self, system, messages):
        self.calls.append(("chat", messages[-1]["content"]))
        return "Answer [K1]."


@pytest.fixture
def biz_kms(module_dir):
    q = FakeHybridQdrant()
    kms = QdrantKMS(q, "kb25", EmbeddingProvider())
    publish_usage_catalog(kms, CATALOG)
    B.build_business_knowledge("inventory", BizLLM(), module_dir, progress=lambda m: None)
    B.publish_business(kms, "inventory", module_dir)
    load_snapshot(kms, refresh=True)
    return kms


def test_content_question_uses_brochure_and_copy(biz_kms):
    llm = AnswerLLM()
    r = MarketingChat(biz_kms, llm).answer("Write a brochure for the inventory module", module="inventory")
    prompt = llm.calls[-1][1]
    assert r["intent"] == "content" and "To confirm before publishing" in prompt
    assert "module brochure" in prompt
    assert "(MarketingCopy" in prompt or "(BusinessValue" in prompt


def test_howto_question_uses_howto_and_faq(biz_kms):
    llm = AnswerLLM()
    MarketingChat(biz_kms, llm).answer("How do I create a BOM?", module="inventory", audience="client_nontechnical")
    prompt = llm.calls[-1][1]
    assert "How to create a BOM" in prompt and "; howto;" in prompt


class CodeKMS:
    collection = "code"
    embedder = EmbeddingProvider()

    def search(self, q, limit, flt=None):
        return {"result": [{"id": 1, "score": .9, "payload": {"file": "backend/GB5Solution/MM/MMBLL/BOM/BOMBLL.cs", "start_line": 40,
                                                              "end_line": 42, "language": "csharp", "layer": "bll", "code_module": "mm",
                                                              "class_name": "BOMBLL", "members": ["SaveBOM"], "knowledge_id": "CODE-x",
                                                              "content": "if (dto.FromDate < DateTime.Today)\n    throw new Exception(\"past\");\nreturn 1;"}}]}


def test_code_goes_to_developers_only(biz_kms):
    llm = AnswerLLM()
    bot = MarketingChat(biz_kms, llm, CodeKMS())
    r = bot.answer("Which line throws the exception when saving a BOM with a past date?", audience="developer")
    prompt = llm.calls[-1][1]
    assert r["intent"] == "code" and "BOMBLL.cs:40-42" in prompt and "   41|     throw" in prompt and "path:line" in prompt
    r2 = bot.answer("Which line throws the exception when saving a BOM with a past date?", audience="client_technical")
    assert "   41|" not in llm.calls[-1][1] and "BOMBLL.cs:40-42" not in llm.calls[-1][1]   # no source lines for clients
    assert any("only shown to the Developer" in n for n in r2["grounding"]["notes"])


def test_developer_without_code_index_is_told(biz_kms):
    r = MarketingChat(biz_kms, AnswerLLM()).answer("Which line throws the exception in SaveBOM?", audience="developer")
    assert "source-code index" in r["grounding"]["missing"]
