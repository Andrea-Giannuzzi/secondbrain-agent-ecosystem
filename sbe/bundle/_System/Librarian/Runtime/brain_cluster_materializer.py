#!/usr/bin/env python3
"""Deterministically compile minimal cluster semantics into an Executor draft."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path

KNOWLEDGE_THRESHOLD = 0.75
LINK_THRESHOLD = 0.82
AREA_CREATE_THRESHOLD = 0.88
AREA_UPDATE_THRESHOLD = 0.82
ALLOWED_RELATIONS = {
    "supports", "derives", "related_to", "part_of", "contradicts",
    "extends", "generalizes", "specializes", "uses",
}


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def stamp() -> str:
    return now().strftime("%Y%m%d-%H%M%S")


def wiki(path: str) -> str:
    target = path[:-3] if path.endswith(".md") else path
    return f"[[{target}|{Path(path).stem}]]"


def clean_label(value: object, *, field: str) -> str:
    label = unicodedata.normalize("NFC", str(value or ""))
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        raise ValueError(f"{field} must not be empty.")
    if len(label) > 80:
        raise ValueError(f"{field} exceeds 80 characters.")
    if any(ord(ch) < 32 for ch in label) or "[[" in label or "]]" in label:
        raise ValueError(f"{field} contains unsafe Markdown/control characters.")
    return label


def filename_for(label: str) -> str:
    name = re.sub(r'[/:\\?*"<>|]', "-", label)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:140] or "Untitled"


def label_key(label: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", label)).strip().casefold()


def unique_target(vault: Path, prefix: str, label: str, reserved: set[str]) -> str:
    stem = filename_for(label)
    candidate = f"{prefix}/{stem}.md"
    number = 2
    while candidate in reserved or (vault / candidate).exists():
        candidate = f"{prefix}/{stem} ({number}).md"
        number += 1
    reserved.add(candidate)
    return candidate


def _id_map(items: list[dict], kind: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in items:
        item_id = item.get("id")
        if item_id in out:
            raise ValueError(f"Duplicate {kind} id: {item_id}")
        out[item_id] = item
    return out


def validate_semantics(bundle: dict, context: dict, canonical: dict) -> None:
    """Validate cross-record invariants not expressible in JSON Schema."""
    errors: list[str] = []
    if bundle.get("cluster_id") != context.get("cluster_id"):
        errors.append("cluster_id mismatch.")

    source_map = _id_map(context.get("sources", []), "context Source")
    concepts = bundle.get("concepts", [])
    if not concepts:
        errors.append("at least one reusable concept is required for an AI candidate cluster.")
    concept_map = _id_map(concepts, "concept")
    mappings = bundle.get("source_concepts", [])

    mapped_ids = [item.get("source_id") for item in mappings]
    if len(mapped_ids) != len(set(mapped_ids)):
        errors.append("source_concepts contains duplicate source_id values.")
    if set(mapped_ids) != set(source_map):
        errors.append("source_concepts must cover every context Source exactly once.")

    known_knowledge = _id_map(canonical.get("knowledge", []), "Knowledge")
    known_areas = _id_map(canonical.get("areas", []), "Area")
    reused: set[str] = set()

    for concept in concepts:
        cid = concept.get("id")
        try:
            clean_label(concept.get("label"), field=f"concept {cid} label")
        except ValueError as exc:
            errors.append(str(exc))
        confidence = float(concept.get("confidence", 0))
        if confidence < KNOWLEDGE_THRESHOLD:
            errors.append(f"concept {cid} confidence < {KNOWLEDGE_THRESHOLD}.")
        existing = concept.get("existing_knowledge_id")
        if existing is not None:
            if existing not in known_knowledge:
                errors.append(f"concept {cid} references unknown Knowledge id {existing}.")
            elif existing in reused:
                errors.append(f"Knowledge id {existing} is reused by multiple concepts.")
            reused.add(existing)

    used_concepts: set[str] = set()
    for mapping in mappings:
        sid = mapping.get("source_id")
        if sid not in source_map:
            errors.append(f"unknown source_id in mapping: {sid}.")
        cids = mapping.get("concept_ids", [])
        if len(cids) != len(set(cids)):
            errors.append(f"source {sid} repeats a concept id.")
        unknown = set(cids) - set(concept_map)
        if unknown:
            errors.append(f"source {sid} references unknown concepts: {sorted(unknown)}.")
        used_concepts.update(cids)

    unused = set(concept_map) - used_concepts
    if unused:
        errors.append(f"concepts without Source provenance: {sorted(unused)}.")

    relation_keys: set[tuple[str, str, str]] = set()
    for relation in bundle.get("relations", []):
        src = relation.get("from_concept")
        dst = relation.get("to_concept")
        kind = relation.get("relation")
        if src not in concept_map or dst not in concept_map:
            errors.append(f"relation references unknown concept: {src} -> {dst}.")
        if src == dst:
            errors.append(f"self relation is forbidden: {src}.")
        if kind not in ALLOWED_RELATIONS:
            errors.append(f"unsupported relation: {kind}.")
        key = (src, dst, kind)
        if key in relation_keys:
            errors.append(f"duplicate relation: {src} -> {dst} ({kind}).")
        relation_keys.add(key)

    area = bundle.get("area_proposal")
    if area is not None:
        try:
            clean_label(area.get("label"), field="area label")
        except ValueError as exc:
            errors.append(str(exc))
        cids = area.get("concept_ids", [])
        if len(cids) != len(set(cids)):
            errors.append("area_proposal repeats a concept id.")
        unknown = set(cids) - set(concept_map)
        if unknown:
            errors.append(f"area_proposal references unknown concepts: {sorted(unknown)}.")
        existing = area.get("existing_area_id")
        if existing is not None and existing not in known_areas:
            errors.append(f"area_proposal references unknown Area id {existing}.")

    if errors:
        raise ValueError("\n".join(errors))


def compose_knowledge(label: str, sources: list[str], confidence: float) -> str:
    date = now().date().isoformat()
    return (
        "---\n"
        'type: "concept"\n'
        'status: "seed"\n'
        f'created: "{date}"\n'
        f'updated: "{date}"\n'
        'generated_by: "cluster-semantics-v1"\n'
        f'confidence: {confidence:.3f}\n'
        f'source_notes: {json.dumps(sources, ensure_ascii=False)}\n'
        "---\n\n"
        f"# {label}\n\n"
        "## Status\n\n"
        "Nodo concettuale creato automaticamente da evidenze locali. "
        "Le Source collegate restano l'autorità per i contenuti.\n\n"
        "## Sources\n\n"
        + "\n".join(f"- {wiki(source)}" for source in sources)
        + "\n"
    )


def compose_area(label: str, members: list[str]) -> str:
    date = now().date().isoformat()
    return (
        "---\n"
        'type: "area"\n'
        'status: "active"\n'
        f'created: "{date}"\n'
        f'updated: "{date}"\n'
        'generated_by: "cluster-semantics-v1"\n'
        "---\n\n"
        f"# {label}\n\n"
        "## Purpose\n\n"
        "Mappa concettuale generata dalle relazioni e dalle fonti del vault.\n\n"
        "## Knowledge Map\n\n"
        + "\n".join(f"- {wiki(member)}" for member in members)
        + "\n"
    )


def materialize_semantics(
    bundle: dict,
    context: dict,
    canonical: dict,
    vault: Path,
) -> tuple[dict, dict[str, str]]:
    """Return the deterministic patch payload and newly created Markdown files."""
    validate_semantics(bundle, context, canonical)
    source_map = _id_map(context["sources"], "context Source")
    concept_map = _id_map(bundle["concepts"], "concept")
    knowledge_map = _id_map(canonical.get("knowledge", []), "Knowledge")
    area_map = _id_map(canonical.get("areas", []), "Area")
    existing_knowledge_by_label = {
        label_key(str(item.get("title", Path(item["path"]).stem))): item
        for item in knowledge_map.values()
    }
    existing_area_by_label = {
        label_key(str(item.get("title", Path(item["path"]).stem))): item
        for item in area_map.values()
    }
    reserved: set[str] = set()
    concept_targets: dict[str, str] = {}
    new_files: dict[str, str] = {}

    sources_by_concept: dict[str, list[str]] = {cid: [] for cid in concept_map}
    mapping_by_source: dict[str, list[str]] = {}
    for mapping in bundle["source_concepts"]:
        sid = mapping["source_id"]
        mapping_by_source[sid] = list(mapping["concept_ids"])
        for cid in mapping["concept_ids"]:
            sources_by_concept[cid].append(source_map[sid]["source_note"])

    for concept in bundle["concepts"]:
        cid = concept["id"]
        existing = concept.get("existing_knowledge_id")
        if existing:
            target = knowledge_map[existing]["path"]
        else:
            label = clean_label(concept["label"], field=f"concept {cid} label")
            existing_match = existing_knowledge_by_label.get(label_key(label))
            if existing_match:
                target = existing_match["path"]
            else:
                target = unique_target(vault, "30_Knowledge", label, reserved)
                new_files[target] = compose_knowledge(
                    label, sorted(set(sources_by_concept[cid])), float(concept["confidence"])
                )
        concept_targets[cid] = target

    target_owners: dict[str, str] = {}
    for cid, target in concept_targets.items():
        previous = target_owners.get(target)
        if previous is not None and previous != cid:
            raise ValueError(f"concepts {previous} and {cid} resolve to the same Knowledge note: {target}")
        target_owners[target] = cid

    ensure_links: list[dict] = []
    for cid, sources in sources_by_concept.items():
        target = concept_targets[cid]
        confidence = float(concept_map[cid]["confidence"])
        for source in sorted(set(sources)):
            ensure_links.append({
                "from": source,
                "to": target,
                "relation": "source_for",
                "confidence": confidence,
            })

    for relation in bundle.get("relations", []):
        confidence = float(relation["confidence"])
        if confidence < LINK_THRESHOLD:
            continue
        src = concept_targets[relation["from_concept"]]
        dst = concept_targets[relation["to_concept"]]
        if src != dst:
            ensure_links.append({
                "from": src,
                "to": dst,
                "relation": relation["relation"],
                "confidence": confidence,
            })

    source_statuses: list[dict] = []
    for source in context["sources"]:
        cids = mapping_by_source[source["id"]]
        targets = sorted({concept_targets[cid] for cid in cids})
        disposition = "knowledge" if targets else "reference"
        reason = (
            f"Mapped to {len(targets)} reusable concept(s)."
            if targets
            else "No reusable concept was identified in this cluster."
        )
        source_statuses.append({
            "source_note": source["source_note"],
            "disposition": disposition,
            "reason": reason,
            "knowledge_notes": targets,
        })

    area_decision = {"action": "none", "policy": "no proposal"}
    moc_updates: list[dict] = []
    area = bundle.get("area_proposal")
    if area is not None:
        members = sorted({concept_targets[cid] for cid in area["concept_ids"]})
        evidence = {
            source
            for cid in area["concept_ids"]
            for source in sources_by_concept[cid]
        }
        confidence = float(area["confidence"])
        existing = area.get("existing_area_id")
        area_target: str | None = None
        if existing is None:
            label_match = existing_area_by_label.get(label_key(str(area["label"])))
            if label_match:
                existing = label_match["id"]
        if existing and confidence >= AREA_UPDATE_THRESHOLD and members:
            area_target = area_map[existing]["path"]
            moc_updates.append({
                "target_note": area_target,
                "description": "Aggiornamento automatico della mappa concettuale.",
                "member_notes": members,
            })
            area_decision = {"action": "update_moc", "target_note": area_target}
        elif (
            not existing
            and confidence >= AREA_CREATE_THRESHOLD
            and len(evidence) >= 4
            and members
        ):
            area_target = unique_target(
                vault,
                "20_Areas",
                clean_label(area["label"], field="area label"),
                reserved,
            )
            new_files[area_target] = compose_area(
                clean_label(area["label"], field="area label"), members
            )
            area_decision = {"action": "create_moc", "target_note": area_target}
        else:
            area_decision = {
                "action": "none",
                "policy": (
                    f"threshold/evidence not met: confidence={confidence:.3f}, "
                    f"sources={len(evidence)}, members={len(members)}"
                ),
            }

        if area_target:
            for member in members:
                ensure_links.append({
                    "from": member,
                    "to": area_target,
                    "relation": "part_of",
                    "confidence": confidence,
                })

    unique_links: dict[tuple[str, str, str], dict] = {}
    for link in ensure_links:
        key = (link["from"], link["to"], link["relation"])
        old = unique_links.get(key)
        if old is None or link["confidence"] > old["confidence"]:
            unique_links[key] = link

    patches = {
        "version": "cluster-2.0",
        "knowledge_updates": [],
        "moc_updates": moc_updates,
        "ensure_links": [unique_links[key] for key in sorted(unique_links)],
        "source_statuses": source_statuses,
        "area_decision": area_decision,
        "non_destructive": True,
    }
    return patches, new_files


def make_draft(
    bundle: dict,
    context: dict,
    canonical: dict,
    vault: Path,
    drafts: Path,
    run_dir: Path,
) -> Path:
    patches, new_files = materialize_semantics(bundle, context, canonical, vault)
    cluster_id = context["cluster_id"]
    safe_cluster = filename_for(cluster_id)[:100]
    draft = drafts / f"{stamp()}-{safe_cluster}"
    suffix = 2
    while draft.exists():
        draft = drafts / f"{stamp()}-{safe_cluster}-{suffix}"
        suffix += 1
    new_root = draft / "new"
    new_root.mkdir(parents=True, exist_ok=False)

    for rel, text in new_files.items():
        path = new_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    (draft / "patches.json").write_text(
        json.dumps(patches, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    source_sha256 = {}
    for source in context["sources"]:
        rel = source["source_note"]
        path = (vault / rel).resolve()
        if vault not in path.parents or not path.is_file():
            raise ValueError(f"Source missing while snapshotting draft: {rel}")
        source_sha256[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = {
        "status": "CLUSTER_DRAFT_VALID",
        "draft_version": "cluster-2.1",
        "created_at": now().isoformat(timespec="seconds"),
        "cluster_id": cluster_id,
        "cluster_title": context.get("cluster_title", cluster_id),
        "source_notes": [source["source_note"] for source in context["sources"]],
        "source_sha256": source_sha256,
        "semantic_bundle": str(run_dir / "semantic-bundle.json"),
        "run_dir": str(run_dir),
        "creates": sorted(new_files),
        "canonical_writes": 0,
    }
    (draft / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return draft
