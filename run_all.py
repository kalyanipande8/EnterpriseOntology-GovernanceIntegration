#!/usr/bin/env python3
"""
End-to-end demo pipeline:

1. Build the SKOS-based v1 pharma taxonomy (rdflib) and QA it (clean).
2. Run QA against deliberately-injected cycle / orphan / missing-label
   vocabularies to prove the checks actually catch real problems.
3. Store v1 in the (SQLite, Postgres-stand-in) database as version 1.
4. Run a real change-management workflow: open a change request, walk it
   through review -> approval, and demonstrate that an invalid transition
   (e.g. DRAFT -> PUBLISHED) is rejected and audited.
5. Apply the approved change (add a new drug class + adverse event term) as
   version 2, and compute a real diff between version 1 and version 2.
"""
from __future__ import annotations

import json
from pathlib import Path

from src import skos_vocab, db as dbmod, versioning, change_management as cm, qa_checks

ACTOR = "kalyani.pande@stonybrook.edu"
DB_PATH = str(Path(__file__).parent / "data" / "ontology_governance.db")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    Path(DB_PATH).unlink(missing_ok=True)

    section("1. Build SKOS vocabulary (rdflib) and export RDF/Turtle")
    base = skos_vocab.base_vocabulary()
    graph = skos_vocab.to_rdf_graph(base)
    print(f"RDF triples generated: {len(graph)}")
    ttl_path = Path(__file__).parent / "data" / "pharma_taxonomy_v1.ttl"
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(ttl_path), format="turtle")
    print(f"Serialized SKOS vocabulary to {ttl_path}")

    concepts_v1 = skos_vocab.concepts_to_dict(base)
    print(f"Concepts in v1: {len(concepts_v1)}")

    section("2. QA checks on the clean v1 vocabulary")
    report = qa_checks.run_qa(concepts_v1)
    print(report.summary())
    assert report.is_clean, "Base vocabulary should be QA-clean"
    print("RESULT: v1 vocabulary is QA-clean (no cycles, orphans, or missing labels).")

    section("2b. QA checks against DELIBERATELY INJECTED issues")
    cyclic = skos_vocab.concepts_to_dict(skos_vocab.inject_cycle(base))
    cyclic_report = qa_checks.run_qa(cyclic)
    print("-- injected cycle --")
    print(cyclic_report.summary())
    assert cyclic_report.cycles, "QA must detect the injected cycle"

    orphaned = skos_vocab.concepts_to_dict(skos_vocab.inject_orphan(base))
    orphan_report = qa_checks.run_qa(orphaned)
    print("-- injected orphan --")
    print(orphan_report.summary())
    assert orphan_report.orphans, "QA must detect the injected orphan"

    missing = skos_vocab.concepts_to_dict(skos_vocab.inject_missing_label(base))
    missing_report = qa_checks.run_qa(missing)
    print("-- injected missing label --")
    print(missing_report.summary())
    assert missing_report.missing_labels, "QA must detect the injected missing label"
    print("RESULT: QA checks correctly caught all three injected defects.")

    section("3. Persist v1 to relational DB (SQLite, Postgres stand-in) as version 1")
    conn = dbmod.init_db(DB_PATH)
    v1_id = versioning.create_version(conn, concepts_v1, label="Initial pharma taxonomy",
                                       created_by=ACTOR)
    print(f"Stored version_id={v1_id} (version_number=1) with {len(concepts_v1)} concepts.")

    section("4. Change-management workflow (real state machine + audit log)")
    cr_id = cm.create_change_request(
        conn,
        title="Add PARP inhibitors drug class and thrombocytopenia adverse event",
        description="Expand oncology drug classes and adverse event terms per Q3 taxonomy review.",
        requested_by=ACTOR,
    )
    print(f"Created change request cr_id={cr_id} in state={cm.get_state(conn, cr_id)}")

    # Demonstrate an INVALID transition is rejected (DRAFT -> PUBLISHED)
    try:
        cm.transition(conn, cr_id, cm.PUBLISHED, actor=ACTOR, reason="skip review (invalid)")
        raise SystemExit("ERROR: invalid transition was NOT rejected -- bug!")
    except cm.InvalidTransitionError as e:
        print(f"Correctly rejected invalid transition DRAFT -> PUBLISHED: {e}")

    cm.transition(conn, cr_id, cm.UNDER_REVIEW, actor=ACTOR, reason="submitted for review")
    cm.transition(conn, cr_id, cm.APPROVED, actor="reviewer@stonybrook.edu",
                  reason="reviewed, structurally sound, approved")
    cm.transition(conn, cr_id, cm.PUBLISHED, actor="governance-board@stonybrook.edu",
                  reason="published to production vocabulary")
    print(f"Final change request state: {cm.get_state(conn, cr_id)}")

    trail = cm.get_audit_trail(conn, cr_id)
    print(f"Audit log entries for cr_id={cr_id}: {len(trail)}")
    for row in trail:
        print(f"  [{row['at']}] {row['action']} by {row['actor']}: {row['detail']}")

    section("5. Apply approved change as version 2 and diff v1 vs v2")
    import copy
    updated = copy.deepcopy(base)
    updated["parp_inhibitors"] = skos_vocab.Concept(
        concept_id="parp_inhibitors",
        pref_label="PARP Inhibitors",
        definition="Class of drugs inhibiting poly (ADP-ribose) polymerase, used in BRCA-mutated cancers.",
        broader=["oncology"],
    )
    updated["oncology"].narrower.append("parp_inhibitors")
    updated["thrombocytopenia"] = skos_vocab.Concept(
        concept_id="thrombocytopenia",
        pref_label="Thrombocytopenia",
        definition="Abnormally low platelet count, a known PARP-inhibitor adverse event.",
        broader=["adverse_events"],
        related=["parp_inhibitors"],
    )
    updated["adverse_events"].narrower.append("thrombocytopenia")
    updated["parp_inhibitors"].related.append("thrombocytopenia")

    concepts_v2 = skos_vocab.concepts_to_dict(updated)
    v2_report = qa_checks.run_qa(concepts_v2)
    assert v2_report.is_clean, "v2 must remain QA-clean before it is stored"

    v2_id = versioning.create_version(conn, concepts_v2, label="Add PARP inhibitors + thrombocytopenia",
                                       created_by=ACTOR, change_request_id=cr_id)
    print(f"Stored version_id={v2_id} (version_number=2) with {len(concepts_v2)} concepts.")

    diff = versioning.diff_versions(conn, v1_id, v2_id)
    print(f"Diff v1 -> v2: added={diff.added} removed={diff.removed}")
    for cid, changes in diff.changed.items():
        print(f"  changed concept '{cid}': {changes}")

    section("6. Confirm old version 1 is still queryable unchanged")
    v1_reloaded = versioning.get_version_concepts(conn, v1_id)
    assert "parp_inhibitors" not in v1_reloaded, "v1 must NOT contain the v2-only concept"
    assert len(v1_reloaded) == len(concepts_v1)
    print(f"Version 1 reloaded from DB still has {len(v1_reloaded)} concepts "
          f"(parp_inhibitors correctly absent).")

    section("Summary")
    versions = versioning.list_versions(conn)
    print(f"Total versions stored: {len(versions)}")
    for v in versions:
        print(f"  v{v['version_number']} ({v['label']}) created_by={v['created_by']}")
    total_audit = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    print(f"Total audit log entries: {total_audit}")
    print(f"RDF triples in v1 SKOS graph: {len(graph)}")
    print(f"Concepts: v1={len(concepts_v1)}, v2={len(concepts_v2)}")

    conn.close()


if __name__ == "__main__":
    main()
