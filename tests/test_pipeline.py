import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import skos_vocab, db as dbmod, versioning, change_management as cm, qa_checks


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test_governance.db")
    c = dbmod.init_db(db_path)
    yield c
    c.close()


@pytest.fixture
def base_concepts():
    return skos_vocab.concepts_to_dict(skos_vocab.base_vocabulary())


# ---------------------------------------------------------------------------
# SKOS vocabulary structure
# ---------------------------------------------------------------------------

def test_base_vocabulary_has_real_hierarchy(base_concepts):
    assert base_concepts["oncology"]["broader"] == ["therapeutic_areas"]
    assert "oncology" in base_concepts["therapeutic_areas"]["narrower"]
    assert "alkylating_agents" in base_concepts["oncology"]["narrower"]
    # related relation is symmetric
    assert "neutropenia" in base_concepts["alkylating_agents"]["related"]
    assert "alkylating_agents" in base_concepts["neutropenia"]["related"]


def test_rdf_graph_uses_real_skos_predicates():
    concepts = skos_vocab.base_vocabulary()
    g = skos_vocab.to_rdf_graph(concepts)
    from rdflib.namespace import SKOS
    concept_triples = list(g.triples((None, __import__("rdflib").RDF.type, SKOS.Concept)))
    assert len(concept_triples) == len(concepts)
    broader_triples = list(g.triples((None, SKOS.broader, None)))
    assert len(broader_triples) > 0


# ---------------------------------------------------------------------------
# QA checks: must catch deliberately injected issues
# ---------------------------------------------------------------------------

def test_qa_clean_on_base_vocabulary(base_concepts):
    report = qa_checks.run_qa(base_concepts)
    assert report.is_clean
    assert report.cycles == []
    assert report.orphans == []
    assert report.missing_labels == []


def test_qa_catches_injected_cycle():
    corrupted = skos_vocab.concepts_to_dict(
        skos_vocab.inject_cycle(skos_vocab.base_vocabulary())
    )
    report = qa_checks.run_qa(corrupted)
    assert not report.is_clean
    assert len(report.cycles) >= 1
    # the cycle should involve the two concepts we deliberately linked
    flat = {node for cycle in report.cycles for node in cycle}
    assert "oncology" in flat and "alkylating_agents" in flat


def test_qa_catches_injected_orphan():
    corrupted = skos_vocab.concepts_to_dict(
        skos_vocab.inject_orphan(skos_vocab.base_vocabulary())
    )
    report = qa_checks.run_qa(corrupted)
    assert "orphan_term" in report.orphans


def test_qa_catches_injected_missing_label():
    corrupted = skos_vocab.concepts_to_dict(
        skos_vocab.inject_missing_label(skos_vocab.base_vocabulary())
    )
    report = qa_checks.run_qa(corrupted)
    assert "myalgia" in report.missing_labels


# ---------------------------------------------------------------------------
# Versioning: old versions must remain queryable and unmodified
# ---------------------------------------------------------------------------

def test_versioning_preserves_old_version_content_after_new_version(conn, base_concepts):
    v1_id = versioning.create_version(conn, base_concepts, label="v1", created_by="tester")

    # sanity check baseline before mutation
    v1_snapshot = versioning.get_version_concepts(conn, v1_id)
    assert v1_snapshot["oncology"]["pref_label"] == "Oncology"

    # Create v2 with an injected/altered concept
    mutated = copy.deepcopy(base_concepts)
    mutated["oncology"]["pref_label"] = "Oncology (renamed)"
    mutated["new_concept"] = {
        "concept_id": "new_concept", "pref_label": "New Concept", "definition": "",
        "broader": ["therapeutic_areas"], "narrower": [], "related": [],
    }
    v2_id = versioning.create_version(conn, mutated, label="v2", created_by="tester")

    # INJECTED SANITY CHECK: re-fetch v1 AFTER v2 was created and prove it is
    # byte-for-byte identical to the original pre-v2 snapshot -- i.e. creating
    # a new version must never mutate a previously stored version.
    v1_after = versioning.get_version_concepts(conn, v1_id)
    assert v1_after == v1_snapshot, "v1 content changed after v2 was created!"
    assert v1_after["oncology"]["pref_label"] == "Oncology"  # NOT renamed
    assert "new_concept" not in v1_after

    v2_content = versioning.get_version_concepts(conn, v2_id)
    assert v2_content["oncology"]["pref_label"] == "Oncology (renamed)"
    assert "new_concept" in v2_content


def test_diff_versions_reports_additions_and_field_changes(conn, base_concepts):
    v1_id = versioning.create_version(conn, base_concepts, label="v1", created_by="tester")
    mutated = copy.deepcopy(base_concepts)
    mutated["statins"]["definition"] = "Updated definition text."
    mutated["new_thing"] = {
        "concept_id": "new_thing", "pref_label": "New Thing", "definition": "",
        "broader": [], "narrower": [], "related": [],
    }
    v2_id = versioning.create_version(conn, mutated, label="v2", created_by="tester")

    diff = versioning.diff_versions(conn, v1_id, v2_id)
    assert diff.added == ["new_thing"]
    assert diff.removed == []
    assert "statins" in diff.changed
    assert diff.changed["statins"]["definition"] == (
        base_concepts["statins"]["definition"], "Updated definition text."
    )


def test_list_versions_returns_all_versions_in_order(conn, base_concepts):
    versioning.create_version(conn, base_concepts, label="v1", created_by="tester")
    versioning.create_version(conn, base_concepts, label="v2", created_by="tester")
    versions = versioning.list_versions(conn)
    assert [v["version_number"] for v in versions] == [1, 2]


# ---------------------------------------------------------------------------
# Change management state machine
# ---------------------------------------------------------------------------

def test_change_request_starts_in_draft(conn):
    cr_id = cm.create_change_request(conn, "Add term", "desc", "tester")
    assert cm.get_state(conn, cr_id) == cm.DRAFT


def test_valid_transition_path_succeeds(conn):
    cr_id = cm.create_change_request(conn, "Add term", "desc", "tester")
    cm.transition(conn, cr_id, cm.UNDER_REVIEW, actor="tester")
    cm.transition(conn, cr_id, cm.APPROVED, actor="reviewer")
    cm.transition(conn, cr_id, cm.PUBLISHED, actor="board")
    assert cm.get_state(conn, cr_id) == cm.PUBLISHED


def test_invalid_transition_is_rejected_and_state_unchanged(conn):
    cr_id = cm.create_change_request(conn, "Add term", "desc", "tester")
    with pytest.raises(cm.InvalidTransitionError):
        cm.transition(conn, cr_id, cm.PUBLISHED, actor="tester")  # DRAFT -> PUBLISHED illegal
    assert cm.get_state(conn, cr_id) == cm.DRAFT  # unchanged


def test_invalid_transition_out_of_terminal_state_is_rejected(conn):
    cr_id = cm.create_change_request(conn, "Add term", "desc", "tester")
    cm.transition(conn, cr_id, cm.UNDER_REVIEW, actor="tester")
    cm.transition(conn, cr_id, cm.REJECTED, actor="reviewer")
    with pytest.raises(cm.InvalidTransitionError):
        cm.transition(conn, cr_id, cm.UNDER_REVIEW, actor="tester")  # REJECTED is terminal
    assert cm.get_state(conn, cr_id) == cm.REJECTED


def test_audit_log_records_both_success_and_rejection(conn):
    cr_id = cm.create_change_request(conn, "Add term", "desc", "tester")
    with pytest.raises(cm.InvalidTransitionError):
        cm.transition(conn, cr_id, cm.PUBLISHED, actor="tester")
    cm.transition(conn, cr_id, cm.UNDER_REVIEW, actor="tester")

    trail = cm.get_audit_trail(conn, cr_id)
    actions = [row["action"] for row in trail]
    assert "CREATED" in actions
    assert "TRANSITION_REJECTED" in actions
    assert "STATE_TRANSITION" in actions
