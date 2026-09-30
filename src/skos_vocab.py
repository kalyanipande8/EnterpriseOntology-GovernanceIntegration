"""
Real SKOS-based controlled vocabulary for a pharma / life-sciences taxonomy
domain, built with rdflib's actual SKOS vocabulary (skos:Concept,
skos:prefLabel, skos:broader, skos:narrower, skos:related, skos:inScheme).

Domain: therapeutic areas -> drug classes -> adverse event terms, modeled as
a single SKOS ConceptScheme so real broader/narrower/related graph
relationships can be exercised (and, for QA testing, deliberately broken).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rdflib import Graph, Namespace, RDF, RDFS, Literal, URIRef
from rdflib.namespace import SKOS

EX = Namespace("http://example.org/pharma-vocab/")
SCHEME_URI = EX["scheme/pharma-taxonomy"]


@dataclass
class Concept:
    concept_id: str          # local id, e.g. "oncology"
    pref_label: str
    definition: str = ""
    broader: List[str] = field(default_factory=list)   # ids
    narrower: List[str] = field(default_factory=list)  # ids
    related: List[str] = field(default_factory=list)   # ids


def base_vocabulary() -> Dict[str, Concept]:
    """Return the canonical (correct) v1 vocabulary as a dict of id -> Concept.

    Structure:
      Therapeutic Areas (top concepts)
        - Oncology
            - Drug classes: Alkylating Agents, Immune Checkpoint Inhibitors
        - Cardiology
            - Drug classes: Beta Blockers, Statins
      Adverse Event Terms (related to drug classes, not hierarchical children)
        - Neutropenia (related to Alkylating Agents)
        - Myalgia (related to Statins)
        - Bradycardia (related to Beta Blockers)
    """
    concepts: Dict[str, Concept] = {}

    def add(cid, label, definition, broader=None, related=None):
        concepts[cid] = Concept(
            concept_id=cid,
            pref_label=label,
            definition=definition,
            broader=list(broader or []),
            related=list(related or []),
        )

    # Top concepts
    add("therapeutic_areas", "Therapeutic Areas",
        "Top concept grouping clinical/therapeutic domains.")
    add("oncology", "Oncology", "Branch of medicine concerned with cancer.",
        broader=["therapeutic_areas"])
    add("cardiology", "Cardiology", "Branch of medicine dealing with heart disorders.",
        broader=["therapeutic_areas"])

    # Drug classes under Oncology
    add("alkylating_agents", "Alkylating Agents",
        "Class of chemotherapy drugs that damage DNA by alkylation.",
        broader=["oncology"])
    add("immune_checkpoint_inhibitors", "Immune Checkpoint Inhibitors",
        "Class of drugs blocking checkpoint proteins to boost immune response to cancer.",
        broader=["oncology"])

    # Drug classes under Cardiology
    add("beta_blockers", "Beta Blockers",
        "Class of drugs that block beta-adrenergic receptors.",
        broader=["cardiology"])
    add("statins", "Statins",
        "Class of lipid-lowering drugs that inhibit HMG-CoA reductase.",
        broader=["cardiology"])

    # Adverse event terms (own top concept, related-linked to drug classes)
    add("adverse_events", "Adverse Event Terms",
        "Top concept grouping standardized adverse event terminology.")
    add("neutropenia", "Neutropenia",
        "Abnormally low concentration of neutrophils in the blood.",
        broader=["adverse_events"], related=["alkylating_agents"])
    add("myalgia", "Myalgia", "Muscle pain, a known statin-associated adverse event.",
        broader=["adverse_events"], related=["statins"])
    add("bradycardia", "Bradycardia", "Abnormally slow heart rate.",
        broader=["adverse_events"], related=["beta_blockers"])

    # Derive narrower from broader (inverse relation) and symmetric related
    for cid, c in concepts.items():
        for b in c.broader:
            if cid not in concepts[b].narrower:
                concepts[b].narrower.append(cid)
    for cid, c in concepts.items():
        for r in list(c.related):
            if cid not in concepts[r].related:
                concepts[r].related.append(cid)

    return concepts


def inject_cycle(concepts: Dict[str, Concept]) -> Dict[str, Concept]:
    """Deliberately corrupt the vocabulary by introducing a broader/narrower
    cycle: oncology -> broader -> alkylating_agents (which is already
    narrower than oncology), forming a 2-node cycle. Used to prove the QA
    cycle-detector actually works."""
    import copy
    corrupted = copy.deepcopy(concepts)
    corrupted["oncology"].broader.append("alkylating_agents")
    return corrupted


def inject_orphan(concepts: Dict[str, Concept]) -> Dict[str, Concept]:
    """Deliberately add a concept with no broader/narrower/related links at
    all (an orphan disconnected from the taxonomy) to prove the orphan
    detector works."""
    import copy
    corrupted = copy.deepcopy(concepts)
    corrupted["orphan_term"] = Concept(
        concept_id="orphan_term",
        pref_label="Orphan Term",
        definition="Accidentally created with no relations to anything.",
    )
    return corrupted


def inject_missing_label(concepts: Dict[str, Concept]) -> Dict[str, Concept]:
    """Deliberately blank out a required prefLabel to prove the missing-label
    check works."""
    import copy
    corrupted = copy.deepcopy(concepts)
    corrupted["myalgia"].pref_label = ""
    return corrupted


def to_rdf_graph(concepts: Dict[str, Concept]) -> Graph:
    """Serialize the vocabulary as a real rdflib Graph using genuine SKOS
    predicates (skos:Concept, skos:prefLabel, skos:definition, skos:broader,
    skos:narrower, skos:related, skos:inScheme, skos:hasTopConcept)."""
    g = Graph()
    g.bind("skos", SKOS)
    g.bind("ex", EX)

    g.add((SCHEME_URI, RDF.type, SKOS.ConceptScheme))
    g.add((SCHEME_URI, RDFS.label, Literal("Pharma Life-Sciences Taxonomy")))

    for cid, c in concepts.items():
        uri = EX[f"concept/{cid}"]
        g.add((uri, RDF.type, SKOS.Concept))
        if c.pref_label:
            g.add((uri, SKOS.prefLabel, Literal(c.pref_label, lang="en")))
        if c.definition:
            g.add((uri, SKOS.definition, Literal(c.definition, lang="en")))
        g.add((uri, SKOS.inScheme, SCHEME_URI))
        if not c.broader:
            g.add((SCHEME_URI, SKOS.hasTopConcept, uri))
            g.add((uri, SKOS.topConceptOf, SCHEME_URI))
        for b in c.broader:
            g.add((uri, SKOS.broader, EX[f"concept/{b}"]))
        for n in c.narrower:
            g.add((uri, SKOS.narrower, EX[f"concept/{n}"]))
        for r in c.related:
            g.add((uri, SKOS.related, EX[f"concept/{r}"]))

    return g


def concepts_to_dict(concepts: Dict[str, Concept]) -> Dict[str, dict]:
    return {
        cid: {
            "concept_id": c.concept_id,
            "pref_label": c.pref_label,
            "definition": c.definition,
            "broader": sorted(c.broader),
            "narrower": sorted(c.narrower),
            "related": sorted(c.related),
        }
        for cid, c in concepts.items()
    }
