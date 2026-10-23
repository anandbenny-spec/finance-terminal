"""Writing documents, chunks and diffs as job_writer (Prompt 01 B2): every test document's chunks pass the database's
own check at commit (core.check_document_chunks); new text is a new version, the same text writes nothing; a chunked
text can't change; diffs are stored as diff version 2."""
import uuid

import psycopg
import pytest
from conftest import dsn
from policy_docs import FOMC_A, FOMC_B
from test_document_chunks import documents, plain

from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.jobs.runner import connect

JOB, ADMIN = dsn("job_writer"), dsn("postgres")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def ref(title: str, key: str | None = None) -> DocumentRef:
    fed_entity, fed_source = q("select (select id from core.entity where slug='fed'), (select id from core.source where key='fed')")[0]
    key = key or f"/test/b2/{uuid.uuid4().hex}.htm"
    return DocumentRef(doc_type="fomc_statement", entity_id=fed_entity, source_id=fed_source, external_key=key,
                       source_url=f"https://www.federalreserve.gov{key}", title=title)


@pytest.mark.db
@pytest.mark.parametrize("name", list(documents()))
def test_every_test_documents_chunks_pass_the_databases_check_at_commit(name):
    doc, kw = documents()[name]
    chunks = chunk_text(doc, **kw)
    with connect(JOB) as c, c.transaction():
        saved = save_version(c, ref(name), doc)
        assert save_chunks(c, saved.document_id, doc.text, chunks) == len(chunks)
    (n, stored, chars, version, chunked), = q(
        """select count(*), string_agg(c.body, '' order by c.chunk_no), d.char_count, d.extractor_version,
                  d.chunked_at is not null
             from core.document d join core.chunk c on c.document_id = d.id where d.id = %s
            group by d.char_count, d.extractor_version, d.chunked_at""", saved.document_id)
    assert (n, stored, chars, version, chunked) == (len(chunks), doc.text, len(doc.text), doc.extractor_version, True)
    sections = q("select section, speaker from core.chunk where document_id=%s order by chunk_no", saved.document_id)
    assert sections == [(c.section, c.speaker) for c in chunks]


@pytest.mark.db
def test_chunks_that_dont_reproduce_the_text_fail_at_commit():
    doc = plain(["The Committee decided to maintain the target range.", "Inflation remains elevated."])
    with pytest.raises(psycopg.Error, match="do not reproduce its text exactly"), connect(JOB) as c, c.transaction():
        saved = save_version(c, ref("broken"), doc)
        wrong = doc.text.replace("maintain", "mAintain")                 # same length, different text
        c.execute("""insert into core.chunk (document_id, chunk_no, char_start, char_end, body)
                     values (%s, 0, 0, %s, %s)""", (saved.document_id, len(wrong), wrong))
    with pytest.raises(ValueError), connect(JOB) as c, c.transaction():  # the Python check stops it first
        saved = save_version(c, ref("broken"), doc)
        save_chunks(c, saved.document_id, doc.text, chunk_text(plain(["Something else entirely."])))


@pytest.mark.db
def test_new_text_is_a_new_version_and_the_same_text_writes_nothing():
    r = ref("FOMC statement")
    a, b = plain(FOMC_A.split("\n\n")), plain(FOMC_B.split("\n\n"))
    with connect(JOB) as c, c.transaction():
        v1 = save_version(c, r, a)
        save_chunks(c, v1.document_id, a.text, chunk_text(a))
    with connect(JOB) as c, c.transaction():
        again = save_version(c, r, a)
        v2 = save_version(c, r, b)
        save_chunks(c, v2.document_id, b.text, chunk_text(b))
    assert (v1.version_no, v1.new_text, v1.supersedes_id) == (1, True, None)
    assert (again.document_id, again.version_no, again.new_text) == (v1.document_id, 1, False)
    assert (v2.version_no, v2.new_text, v2.supersedes_id) == (2, True, v1.document_id)
    assert q("select version_no, supersedes_id from core.document where external_key=%s order by version_no",
             r.external_key) == [(1, None), (2, v1.document_id)]
    with pytest.raises(psycopg.Error, match="immutable here"), connect(JOB) as c:     # a chunked text is fixed
        c.execute("update core.document set text_sha256=sha256('x'::bytea) where id=%s", (v1.document_id,))
    with connect(JOB) as c, c.transaction():                             # re-chunking the same text is allowed
        assert save_chunks(c, v1.document_id, a.text, chunk_text(a, target=300, max_chars=400)) > len(chunk_text(a))


@pytest.mark.db
def test_a_document_row_without_text_gets_its_text_in_place():
    r = ref("placeholder")
    with connect(JOB) as c:
        doc_id = c.execute("""insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
                              values (%s,%s,%s,%s,%s,%s) returning id""",
                           (r.doc_type, r.entity_id, r.title, r.source_id, r.external_key, r.source_url)).fetchone()[0]
        c.commit()
    doc = plain(["Text that arrived later."])
    with connect(JOB) as c, c.transaction():
        saved = save_version(c, r, doc)
        save_chunks(c, saved.document_id, doc.text, chunk_text(doc))
    assert (saved.document_id, saved.version_no, saved.new_text) == (doc_id, 1, True)


@pytest.mark.db
def test_a_diff_is_stored_as_version_2_and_replaced_on_a_rerun():
    a, b = plain(FOMC_A.split("\n\n")), plain(FOMC_B.split("\n\n"))
    with connect(JOB) as c, c.transaction():
        base = save_version(c, ref("FOMC statement, before"), a)
        new = save_version(c, ref("FOMC statement, after"), b)
        d = diff_texts(a.text, b.text)
        save_diff(c, new.document_id, base.document_id, d)
        save_diff(c, new.document_id, base.document_id, d)                # a re-run replaces it
    (version, added, removed, seg_version, pairs), = q(
        """select diff_version, words_added, words_removed, segments->>'version', jsonb_array_length(segments->'pairs')
             from core.document_diff where document_id=%s""", new.document_id)
    assert (version, added, removed, seg_version, pairs) == ("2", d.words_added, d.words_removed, "2",
                                                             len(d.segments["pairs"]))
