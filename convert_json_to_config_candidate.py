from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import yaml

EXCLUDED_ROOM_IDS = {
    "1.1",
    "1.3",
    "3.1",
    "3.2",
    "3.3",
    "3.4",
    "3.5",
    "4.2",
    "4.3",
    "4.4",
    "4.5",
    "425",
    "309A",
}

PROGRAMS: dict[str, list[dict[str, Any]]] = {
    "bachelor": [
        {
            "id": "bachelor_1_en",
            "name": "BS - Year 1 (EN)",
            "language": "en",
            "year": 1,
            "tracks": [
                {
                    "name": "Computer Science and Engineering",
                    "groups": [
                        {"id": "B25-CSE-01", "size": 27},
                        {"id": "B25-CSE-02", "size": 27},
                        {"id": "B25-CSE-03", "size": 26},
                        {"id": "B25-CSE-04", "size": 26},
                        {"id": "B25-CSE-05", "size": 26},
                    ],
                },
                {
                    "name": "Data Science and Artificial Intelligence",
                    "groups": [
                        {"id": "B25-DSAI-01", "size": 26},
                        {"id": "B25-DSAI-02", "size": 25},
                        {"id": "B25-DSAI-03", "size": 25},
                        {"id": "B25-DSAI-04", "size": 25},
                        {"id": "B25-DSAI-05", "size": 25},
                    ],
                },
            ],
        },
        {
            "id": "bachelor_1_ru",
            "name": "BS - Year 1 (RU)",
            "language": "ru",
            "year": 1,
            "tracks": [
                {"name": "AI360", "groups": [{"id": "B25-AI360-01", "size": 18}]},
                {
                    "name": "MFAI",
                    "groups": [
                        {"id": "B25-MFAI-01", "size": 18},
                        {"id": "B25-MFAI-02", "size": 18},
                        {"id": "B25-MFAI-03", "size": 18},
                        {"id": "B25-MFAI-04", "size": 18},
                        {"id": "B25-MFAI-05", "size": 18},
                        {"id": "B25-MFAI-06", "size": 18},
                        {"id": "B25-MFAI-07", "size": 30},
                    ],
                },
                {"name": "Robotics", "groups": [{"id": "B25-RO-01", "size": 2}]},
            ],
        },
        {
            "id": "bachelor_2_en",
            "name": "BS - Year 2 (EN)",
            "language": "en",
            "year": 2,
            "tracks": [
                {
                    "name": "Software Development",
                    "groups": [
                        {"id": "B24-SD-01", "size": 30},
                        {"id": "B24-SD-02", "size": 30},
                        {"id": "B24-SD-03", "size": 30},
                    ],
                },
                {
                    "name": "Cybersecurity",
                    "groups": [
                        {"id": "B24-CBS-01", "size": 30},
                        {"id": "B24-CBS-02", "size": 30},
                        {"id": "B24-CBS-03", "size": 30},
                    ],
                },
                {"name": "Data Science", "groups": [{"id": "B24-DS-01", "size": 28}]},
                {
                    "name": "Artificial Intelligence",
                    "groups": [
                        {"id": "B24-AI-01", "size": 30},
                        {"id": "B24-AI-02", "size": 30},
                        {"id": "B24-AI-03", "size": 30},
                    ],
                },
                {"name": "Game Development", "groups": [{"id": "B24-GD-01", "size": 22}]},
                {"name": "Robotics", "groups": [{"id": "B24-RO-01", "size": 10}]},
            ],
        },
        {
            "id": "bachelor_2_ru",
            "name": "BS - Year 2 (RU)",
            "language": "ru",
            "year": 2,
            "tracks": [
                {
                    "name": "MFAI",
                    "groups": [
                        {"id": "B24-MFAI-01", "size": 20},
                        {"id": "B24-MFAI-02", "size": 24},
                        {"id": "B24-MFAI-03", "size": 22},
                        {"id": "B24-MFAI-04", "size": 14},
                    ],
                },
                {"name": "Robotics", "groups": [{"id": "B24-RO15-01", "size": 1}]},
                {"name": "AI360", "groups": [{"id": "B24-AI360-01", "size": 10}]},
            ],
        },
        {
            "id": "bachelor_3_en",
            "name": "BS - Year 3 (EN)",
            "language": "en",
            "year": 3,
            "tracks": [
                {
                    "name": "Software Development",
                    "groups": [
                        {"id": "B23-SD-01", "size": 30},
                        {"id": "B23-SD-02", "size": 27},
                        {"id": "B23-SD-03", "size": 25},
                    ],
                },
                {
                    "name": "Cybersecurity",
                    "groups": [
                        {"id": "B23-CBS-01", "size": 27},
                        {"id": "B23-CBS-02", "size": 26},
                    ],
                },
                {
                    "name": "Artificial Intelligence",
                    "groups": [
                        {"id": "B23-AI-01", "size": 27},
                        {"id": "B23-AI-02", "size": 24},
                    ],
                },
                {
                    "name": "Data Science",
                    "groups": [
                        {"id": "B23-DS-01", "size": 24},
                        {"id": "B23-DS-02", "size": 25},
                    ],
                },
                {"name": "Game Development", "groups": [{"id": "B23-GD-01", "size": 16}]},
                {"name": "Robotics", "groups": [{"id": "B23-RO-01", "size": 14}]},
            ],
        },
    ],
    "master": [
        {
            "id": "master_1",
            "name": "MS - Year 1",
            "year": 1,
            "tracks": [
                {
                    "name": "Software Engineering",
                    "groups": [
                        {"id": "M25-SE-01", "size": 15},
                        {"id": "M25-SE-02", "size": 15},
                    ],
                },
                {"name": "AIDE", "groups": [{"id": "M25-AIDE-01", "size": 27}]},
                {"name": "Robotics", "groups": [{"id": "M25-RO-01", "size": 14}]},
                {"name": "Technological Entrepreneurship", "groups": [{"id": "M25-TE-01", "size": 17}]},
                {"name": "SNE", "groups": [{"id": "M25-SNE-01", "size": 21}]},
            ],
        }
    ],
    "phd": [
        {
            "id": "phd",
            "name": "PhD - 1 year",
            "year": 1,
            "tracks": [
                {
                    "name": "PhD",
                    "groups": [{"id": "PhD", "size": 25}],
                },
            ],
        },
    ],
}


WEEKDAY_TO_SHORT = {
    "MONDAY": "Mon",
    "TUESDAY": "Tue",
    "WEDNESDAY": "Wed",
    "THURSDAY": "Thu",
    "FRIDAY": "Fri",
    "SATURDAY": "Sat",
    "SUNDAY": "Sun",
}

CLASS_TAG_MAP = {
    "лаб": "lab",
    "лаба": "lab",
    "lab": "lab",
    "тут": "tut",
    "tut": "tut",
    "tutorial": "tut",
    "лек": "lec",
    "лекция": "lec",
    "lec": "lec",
    "практ": "practice",
    "практика": "practice",
    "seminar": "sem",
}

@dataclass(frozen=True)
class PatternKey:
    course: str
    class_tag: str
    room: str
    start_date: str
    end_date: str


def normalize_class_tag(value: str | None) -> str:
    if value is None:
        return "class"
    cleaned = value.strip().lower()
    return CLASS_TAG_MAP.get(cleaned, cleaned.replace(" ", "_"))


def normalize_time(value: str) -> str:
    return value[:5]


def maybe_online(room: str | None) -> bool:
    if not room:
        return False
    cleaned = room.strip().lower()
    return cleaned in {"онлайн", "online"}


def to_instructor_id(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    if not slug:
        slug = "unknown_instructor"
    return slug


def split_teacher_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    names = [p for p in parts if p]
    return names


def is_english_lesson(lesson_name: str) -> bool:
    name = lesson_name.strip().lower()
    return ("english" in name) or ("англий" in name) or ("иностран" in name) or ("foreign language" in name)


def infer_course_tags(course_name: str) -> list[str]:
    if is_english_lesson(course_name):
        return ["english"]
    return ["core_course"]


def normalize_lesson_name(lesson_name: str) -> str:
    name = lesson_name.strip()
    lowered = name.lower()
    if lowered in {"foreign language", "иностранный язык"}:
        return "Foreign Language"
    return name


def should_exclude_lesson(lesson_name: str) -> bool:
    return lesson_name.strip().lower() in {
        "group meeting with administration",
    }


def normalize_weekday_label(value: str) -> str:
    token = value.strip().upper()
    mapping = {
        "M": "Mon",
        "T": "Tue",
        "W": "Wed",
        "TH": "Thu",
        "F": "Fri",
        "S": "Sat",
    }
    return mapping.get(token, token.title())


class FlowStyleList(list):
    pass


class ConfigDumper(yaml.SafeDumper):
    pass


def _represent_flow_list(dumper: yaml.SafeDumper, data: FlowStyleList) -> yaml.nodes.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


ConfigDumper.add_representer(FlowStyleList, _represent_flow_list)


def apply_yaml_style_overrides(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"instructor_pool", "student_groups"} and isinstance(value, list):
                out[key] = FlowStyleList(value)
            else:
                out[key] = apply_yaml_style_overrides(value)
        return out
    if isinstance(node, list):
        return [apply_yaml_style_overrides(item) for item in node]
    return node


def excel_time_to_hhmm(raw: str) -> str:
    value = raw.strip()
    try:
        fraction = float(value)
        minutes = int(round(fraction * 24 * 60))
        hh = (minutes // 60) % 24
        mm = minutes % 60
        return f"{hh:02d}:{mm:02d}"
    except ValueError:
        return value[:5]


def group_id_from_english_label(label: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    while "__" in base:
        base = base.replace("__", "_")
    return f"ENG-{base.strip('_')}"


def english_group_sort_key_from_id(group_id: str) -> tuple[str, int, str]:
    gid = str(group_id or "").strip()
    match = re.match(r"^(ENG-[A-Za-z_]+?)(\d+)$", gid)
    if match:
        return (match.group(1).lower(), int(match.group(2)), gid)
    return (gid.lower(), 10**9, gid)


def load_xlsx_rows(xlsx_path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(xlsx_path) as z:
        sst: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                sst.append("".join((t.text or "") for t in si.findall(".//a:t", ns)))

        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        out: list[list[str]] = []
        for row in sheet.findall("a:sheetData/a:row", ns):
            vals: list[str] = []
            for cell in row.findall("a:c", ns):
                c_type = cell.attrib.get("t")
                v = cell.find("a:v", ns)
                if v is None:
                    vals.append("")
                    continue
                raw = v.text or ""
                if c_type == "s" and raw.isdigit():
                    idx = int(raw)
                    vals.append(sst[idx] if idx < len(sst) else "")
                else:
                    vals.append(raw)
            out.append(vals)
        return out


def load_rooms(rooms_json_path: Path) -> list[dict[str, Any]]:
    if not rooms_json_path.exists():
        return []
    rows = json.loads(rooms_json_path.read_text(encoding="utf-8"))
    rooms: list[dict[str, Any]] = []
    for row in rows:
        room_id = str(row.get("id", "")).strip()
        if not room_id:
            continue
        if room_id in EXCLUDED_ROOM_IDS:
            continue
        capacity = row.get("capacity")
        if not isinstance(capacity, int):
            continue
        rooms.append(
            {
                "id": room_id,
                "name": str(row.get("title") or row.get("short_name") or room_id),
                "capacity": capacity,
            }
        )
    return sorted(
        rooms,
        key=lambda room: (
            str(room.get("id", ""))[:1],
            -int(room.get("capacity", 0)),
            str(room.get("id", "")),
        ),
    )


def load_english_distribution(
    xlsx_path: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, str, str], set[str]],
    dict[tuple[str, str], set[str]],
    dict[str, set[str]],
    dict[str, int],
]:
    if not xlsx_path.exists():
        return [], {}, {}, {}, {}

    rows = load_xlsx_rows(xlsx_path)
    if not rows:
        return [], {}, {}, {}, {}

    header = [h.strip().lower() for h in rows[0]]
    col = {name: i for i, name in enumerate(header)}
    required = ["e group", "instructor", "days", "time", "e-mail"]
    if any(key not in col for key in required):
        return [], {}, {}, {}, {}

    by_id: dict[str, dict[str, Any]] = {}
    by_slot_and_instr: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    by_slot_only: dict[tuple[str, str], set[str]] = defaultdict(set)
    group_instructors: dict[str, set[str]] = defaultdict(set)
    group_slots: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for row in rows[1:]:
        max_idx = max(col[k] for k in required)
        if len(row) <= max_idx:
            continue

        label = row[col["e group"]].strip()
        if not label:
            continue
        gid = group_id_from_english_label(label)
        instr_name = row[col["instructor"]].strip() or "Unknown Instructor"
        instr_id = to_instructor_id(instr_name)
        group_instructors[gid].add(instr_id)
        time_hhmm = excel_time_to_hhmm(row[col["time"]])
        days_raw = row[col["days"]].strip()
        day_tokens = [d for d in (part.strip() for part in days_raw.split("/")) if d]
        day_names = [normalize_weekday_label(d) for d in day_tokens]
        email = row[col["e-mail"]].strip().lower()
        if "@" not in email:
            email = ""

        group = by_id.setdefault(
            gid,
            {
                "id": gid,
                "kind": "english",
                "name": label,
                "students": [],
                "size": 0,
            },
        )
        if email and email not in group["students"]:
            group["students"].append(email)

        for day in day_names:
            by_slot_and_instr[(day, time_hhmm, instr_id)].add(gid)
            by_slot_only[(day, time_hhmm)].add(gid)
            group_slots[gid].add((day, time_hhmm))

    shared_groups: list[dict[str, Any]] = []
    for group in sorted(by_id.values(), key=lambda g: english_group_sort_key_from_id(str(g.get("id", "")))):
        group["size"] = len(group["students"])
        shared_groups.append(group)

    group_per_week = {gid: len(slots) for gid, slots in group_slots.items() if slots}
    return shared_groups, by_slot_and_instr, by_slot_only, group_instructors, group_per_week


def _track_group_ids(tracks: list[dict[str, Any]]) -> set[str]:
    return {g["id"] for track in tracks for g in track.get("groups", []) if g.get("id")}


def _program_group_ids(program: dict[str, Any]) -> set[str]:
    groups: set[str] = set()
    if "tracks" in program:
        groups.update(_track_group_ids(program.get("tracks", [])))
    if "extra_hierarchy" in program:
        groups.update(_track_group_ids(program.get("extra_hierarchy", [])))
    if "groups" in program:
        groups.update({g["id"] for g in program.get("groups", []) if g.get("id")})
    return groups


def build_group_selectors(programs: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    selectors: dict[str, set[str]] = {}
    for level_programs in programs.values():
        for program in level_programs:
            program_id = program.get("id")
            if not program_id:
                continue
            program_groups = _program_group_ids(program)
            if program_groups:
                selectors[f"@{program_id}"] = program_groups
            for track in program.get("tracks", []):
                track_name = track.get("name")
                if not track_name:
                    continue
                track_groups = {g["id"] for g in track.get("groups", []) if g.get("id")}
                if track_groups:
                    selectors[f"@{program_id}/{track_name}"] = track_groups
            for track in program.get("extra_hierarchy", []):
                track_name = track.get("name")
                if not track_name:
                    continue
                track_groups = {g["id"] for g in track.get("groups", []) if g.get("id")}
                if track_groups:
                    selectors[f"@{program_id}/{track_name}"] = track_groups
    return selectors


def build_group_order(programs: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    order: dict[str, int] = {}
    idx = 0
    for level_programs in programs.values():
        for program in level_programs:
            if "tracks" in program:
                for track in program.get("tracks", []):
                    for group in track.get("groups", []):
                        gid = group.get("id")
                        if gid and gid not in order:
                            order[gid] = idx
                            idx += 1
            else:
                for group in program.get("groups", []):
                    gid = group.get("id")
                    if gid and gid not in order:
                        order[gid] = idx
                        idx += 1
            for track in program.get("extra_hierarchy", []):
                for group in track.get("groups", []):
                    gid = group.get("id")
                    if gid and gid not in order:
                        order[gid] = idx
                        idx += 1
    return order


def collect_academic_groups(programs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _level_name, level_programs in programs.items():
        for program in level_programs:
            if "tracks" in program:
                for track in program.get("tracks", []):
                    for g in track.get("groups", []):
                        gid = g.get("id")
                        if not gid or gid in seen:
                            continue
                        seen.add(gid)
                        out.append(
                            {
                                "id": gid,
                                "name": gid,
                                "estimated_size": g.get("size"),
                            }
                        )
            else:
                for g in program.get("groups", []):
                    gid = g.get("id")
                    if not gid or gid in seen:
                        continue
                    seen.add(gid)
                    out.append(
                        {
                            "id": gid,
                            "name": gid,
                            "estimated_size": g.get("size"),
                        }
                    )
    return out


def enrich_academic_groups_from_predefined(
    academic_groups: list[dict[str, Any]],
    predefined_json_path: Path,
) -> list[dict[str, Any]]:
    ordered_ids = [g["id"] for g in academic_groups if g.get("id")]
    if not predefined_json_path.exists():
        return [g for g in academic_groups if g.get("id")]

    try:
        payload = json.loads(predefined_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [g for g in academic_groups if g.get("id")]

    predefined = payload.get("academic_groups")
    if not isinstance(predefined, list):
        return [g for g in academic_groups if g.get("id")]

    by_id: dict[str, dict[str, Any]] = {g["id"]: dict(g) for g in academic_groups if g.get("id")}
    for item in predefined:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("name") or "").strip()
        if not gid:
            continue
        if gid not in by_id:
            # Keep academic groups bounded to groups present in programs.
            continue
        students_raw = item.get("user_emails")
        students = []
        if isinstance(students_raw, list):
            students = [str(email).strip().lower() for email in students_raw if str(email).strip()]

        existing = by_id[gid]
        merged = dict(existing)
        merged["id"] = gid
        merged["name"] = existing.get("name") or gid
        merged["students"] = students
        merged["estimated_size"] = len(students) if students else existing.get("estimated_size")
        by_id[gid] = merged

    return [by_id[gid] for gid in ordered_ids if gid in by_id]


def simplify_program_group_refs(programs: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    simplified: dict[str, list[dict[str, Any]]] = {}
    for level, level_programs in programs.items():
        out_programs: list[dict[str, Any]] = []
        for program in level_programs:
            p = dict(program)
            if "tracks" in p:
                tracks_out: list[dict[str, Any]] = []
                for track in p.get("tracks", []):
                    t = dict(track)
                    t["groups"] = [g["id"] for g in track.get("groups", []) if g.get("id")]
                    tracks_out.append(t)
                p["tracks"] = tracks_out
            if "extra_hierarchy" in p:
                extra_out: list[dict[str, Any]] = []
                for track in p.get("extra_hierarchy", []):
                    t = dict(track)
                    t["groups"] = [g["id"] for g in track.get("groups", []) if g.get("id")]
                    extra_out.append(t)
                p["extra_hierarchy"] = extra_out
            elif "groups" in p:
                p["groups"] = [g["id"] for g in p.get("groups", []) if g.get("id")]
            out_programs.append(p)
        simplified[level] = out_programs
    return simplified


def attach_english_to_programs(
    programs: dict[str, list[dict[str, Any]]],
    english_groups: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    enriched = deepcopy(programs)
    grouped: dict[str, list[dict[str, Any]]] = {"AWA-I": [], "EAP": [], "FL": [], "Other": []}

    for group in english_groups:
        gid = group.get("id")
        if not gid:
            continue
        item = {"id": gid, "size": group.get("size")}
        gid_lower = str(gid).lower()
        if gid_lower.startswith("eng-awa_i_"):
            grouped["AWA-I"].append(item)
        elif gid_lower.startswith("eng-eap"):
            grouped["EAP"].append(item)
        elif gid_lower.startswith("eng-fl"):
            grouped["FL"].append(item)
        else:
            grouped["Other"].append(item)

    tracks: list[dict[str, Any]] = []
    for track_name in ("AWA-I", "EAP", "FL", "Other"):
        groups = sorted(grouped[track_name], key=lambda item: english_group_sort_key_from_id(str(item.get("id", ""))))
        if groups:
            tracks.append({"name": track_name, "groups": groups})

    if tracks:
        enriched["english"] = [
            {
                "id": "english",
                "name": "English Program (Year 1)",
                "language": "en",
                "tracks": tracks,
            }
        ]

    return enriched


def build_group_buckets(
    academic_groups: list[dict[str, Any]],
    english_groups: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    english_clean = [
        {
            "id": group["id"],
            "name": group.get("name", group["id"]),
            "estimated_size": group.get("size"),
            "students": group.get("students", []),
        }
        for group in english_groups
    ]
    return {
        "academic": academic_groups,
        "english": sorted(english_clean, key=lambda g: english_group_sort_key_from_id(str(g.get("id", "")))),
        # Filled when elective groups are introduced. Keep schema explicit now.
        "elective": [],
    }


def compress_groups_to_selectors(
    groups: list[str],
    selector_map: dict[str, set[str]],
    group_order: dict[str, int],
) -> list[str]:
    group_set = set(groups)
    if not group_set:
        return []

    selected: list[str] = []
    covered: set[str] = set()
    candidates = sorted(
        ((selector, members) for selector, members in selector_map.items() if members.issubset(group_set)),
        key=lambda item: (-len(item[1]), item[0].count("/"), item[0]),
    )
    for selector, members in candidates:
        if members.issubset(covered):
            continue
        selected.append(selector)
        covered.update(members)

    leftovers = list(group_set - covered)

    def member_rank(gid: str) -> int:
        return group_order.get(gid, 10**9)

    def token_rank(token: str) -> int:
        if token in selector_map:
            return min((member_rank(gid) for gid in selector_map[token]), default=10**9)
        return member_rank(token)

    combined = selected + leftovers
    return sorted(combined, key=lambda token: (token_rank(token), token))


def class_group_rank(
    cls: dict[str, Any],
    selector_map: dict[str, set[str]],
    group_order: dict[str, int],
) -> int:
    groups = cls.get("student_groups", [])
    if not groups:
        return 10**9

    def member_rank(gid: str) -> int:
        return group_order.get(gid, 10**9)

    def token_rank(token: str) -> int:
        if token in selector_map:
            return min((member_rank(gid) for gid in selector_map[token]), default=10**9)
        return member_rank(token)

    return min((token_rank(token) for token in groups), default=10**9)


def infer_per_group(
    class_tag: str,
    student_groups: list[str],
    *,
    is_english_course: bool,
    source_group_count: int | None = None,
) -> bool:
    if is_english_course and class_tag == "class":
        return True
    effective_group_count = source_group_count if source_group_count is not None else len(student_groups)
    if class_tag == "class" and effective_group_count > 1:
        return True
    if class_tag == "lab":
        return True
    return False


def detect_block_key(google_sheet_name: str | None) -> str | None:
    if not google_sheet_name:
        return None
    lowered = google_sheet_name.strip().lower()
    if "1st block" in lowered:
        return "block1"
    if "2nd block" in lowered:
        return "block2"
    return None


def output_path_for_block(base_output: Path, block_key: str) -> Path:
    return base_output.with_name(f"{base_output.stem}-{block_key}{base_output.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert core-courses-lessons JSON into config-candidate.yaml")
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_yaml", type=Path, nargs="?", default=Path("config-candidate.yaml"))
    parser.add_argument(
        "--english-distribution-xlsx",
        type=Path,
        default=Path("Foreign language.xlsx"),
        help="Path to Foreign language distribution XLSX",
    )
    parser.add_argument(
        "--rooms-json",
        type=Path,
        default=Path("rooms.json"),
        help="Path to rooms JSON export",
    )
    parser.add_argument(
        "--predefined-json",
        type=Path,
        default=Path("predefined.json"),
        help="Path to predefined.json with academic_groups students",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError("Input JSON is empty")

    block_rows: dict[str, list[dict[str, Any]]] = {"block1": [], "block2": []}
    unclassified_rows: list[dict[str, Any]] = []
    for row in rows:
        block_key = detect_block_key(row.get("google_sheet_name"))
        if block_key in block_rows:
            block_rows[block_key].append(row)
        else:
            unclassified_rows.append(row)

    distribution_path = args.english_distribution_xlsx
    if not distribution_path.exists():
        candidate = args.input_json.parent / distribution_path
        if candidate.exists():
            distribution_path = candidate
    rooms_json_path = args.rooms_json
    if not rooms_json_path.exists():
        candidate = args.input_json.parent / rooms_json_path
        if candidate.exists():
            rooms_json_path = candidate
    predefined_json_path = args.predefined_json
    if not predefined_json_path.exists():
        candidate = args.input_json.parent / predefined_json_path
        if candidate.exists():
            predefined_json_path = candidate
    rooms = load_rooms(rooms_json_path)

    (
        shared_groups,
        english_slot_instr_map,
        english_slot_map,
        english_group_instructors,
        english_group_per_week,
    ) = load_english_distribution(distribution_path)
    programs = attach_english_to_programs(PROGRAMS, shared_groups)
    simplified_programs = simplify_program_group_refs(programs)
    academic_groups = collect_academic_groups(PROGRAMS)
    academic_groups = enrich_academic_groups_from_predefined(academic_groups, predefined_json_path)
    groups_bucket = build_group_buckets(academic_groups, shared_groups)

    instructors_map: dict[str, str] = {}
    aggregated: dict[PatternKey, dict[str, Any]] = {}

    for r in rows:
        course = normalize_lesson_name(r["lesson_name"])
        if should_exclude_lesson(course):
            continue
        class_tag = normalize_class_tag(r.get("lesson_class_type"))
        teacher_names = split_teacher_names(r.get("teacher"))
        teacher_ids = tuple(sorted(to_instructor_id(name) for name in teacher_names))
        key = PatternKey(
            course=course,
            class_tag=class_tag,
            room=(r.get("room_name") or ""),
            # Source sometimes splits one logical stream into multiple date ranges
            # (e.g., around block boundaries). Keep stream identity stable here.
            start_date="",
            end_date="",
        )
        if key not in aggregated:
            aggregated[key] = {
                "groups": set(),
                "raw_groups": set(),
                "teacher_signatures": set(),
                "groups_by_signature": defaultdict(set),
                "slots_by_signature": defaultdict(lambda: defaultdict(set)),
                "duration_slots": 1,
                "slots_by_group": defaultdict(set),
            }

        for tid, tname in zip((to_instructor_id(name) for name in teacher_names), teacher_names):
            instructors_map[tid] = tname
        aggregated[key]["teacher_signatures"].add(teacher_ids)

        group_field = r["group_name"]
        groups = group_field if isinstance(group_field, list) else [group_field]
        groups = [g for g in groups if g]
        aggregated[key]["raw_groups"].update(groups)
        if is_english_lesson(course):
            day = WEEKDAY_TO_SHORT[r["weekday"]]
            start = normalize_time(r["start_time"])
            matched_groups: set[str] = set()
            for tid in teacher_ids:
                matched_groups.update(english_slot_instr_map.get((day, start, tid), set()))
            if not matched_groups:
                matched_groups.update(english_slot_map.get((day, start), set()))
            if matched_groups:
                groups = sorted(matched_groups)
        aggregated[key]["groups_by_signature"][teacher_ids].update(groups)
        aggregated[key]["groups"].update(groups)
        # Per-week frequency should come from recurring weekly rows only.
        # Rows with date_on are typically one-off replacements and should not
        # increase regular weekly frequency.
        if not r.get("date_on"):
            slot_sig = (r["weekday"], normalize_time(r["start_time"]))
            for g in groups:
                aggregated[key]["slots_by_group"][g].add(slot_sig)
                aggregated[key]["slots_by_signature"][teacher_ids][g].add(slot_sig)

        duration_minutes = (
            datetime.strptime(normalize_time(r["end_time"]), "%H:%M")
            - datetime.strptime(normalize_time(r["start_time"]), "%H:%M")
        ).seconds // 60
        aggregated[key]["duration_slots"] = max(aggregated[key]["duration_slots"], 1, round(duration_minutes / 90))
    selector_map = build_group_selectors(programs)
    group_order = build_group_order(programs)

    def render_config(selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
        global_start = min(r["start_date"] for r in selected_rows)
        global_end = max(r["end_date"] for r in selected_rows)
        selected_keys = {
            PatternKey(
                course=normalize_lesson_name(row["lesson_name"]),
                class_tag=normalize_class_tag(row.get("lesson_class_type")),
                room=(row.get("room_name") or ""),
                start_date="",
                end_date="",
            )
            for row in selected_rows
            if not should_exclude_lesson(normalize_lesson_name(row["lesson_name"]))
        }

        courses_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        tag_order = {"lec": 0, "tut": 1, "lab": 2, "class": 3}
        for pattern, data in sorted(
            aggregated.items(),
            key=lambda x: (
                x[0].course,
                tag_order.get(x[0].class_tag, 99),
                x[0].class_tag,
            ),
        ):
            if pattern not in selected_keys:
                continue
            teacher_signatures = sorted(data["teacher_signatures"])

            # Build emission variants:
            # 1) split lec/tut by teacher signatures when they differ;
            # 2) additionally split lec/tut by audience slot clusters when one teacher
            #    teaches different audiences in different recurring slots.
            if pattern.class_tag in {"lec", "tut"} and len(teacher_signatures) > 1:
                base_variants = []
                for signature in teacher_signatures:
                    base_variants.append(
                        (
                            sorted(data["groups_by_signature"][signature]),
                            data["slots_by_signature"][signature],
                            [signature],
                        )
                    )
            else:
                base_variants = [
                    (
                        sorted(data["groups"]),
                        data["slots_by_group"],
                        teacher_signatures,
                    )
                ]

            emission_variants: list[tuple[list[str], Any, list[tuple[str, ...]]]] = []
            for groups_for_cls, slots_source, signatures_for_pool in base_variants:
                if pattern.class_tag in {"lec", "tut"}:
                    groups_by_slot_fingerprint: dict[tuple[tuple[str, str], ...], list[str]] = defaultdict(list)
                    for gid in groups_for_cls:
                        slot_fingerprint = tuple(sorted(slots_source.get(gid, set())))
                        groups_by_slot_fingerprint[slot_fingerprint].append(gid)
                    if len(groups_by_slot_fingerprint) > 1:
                        for cluster_groups in groups_by_slot_fingerprint.values():
                            filtered_slots = {gid: slots_source[gid] for gid in cluster_groups if gid in slots_source}
                            emission_variants.append((sorted(cluster_groups), filtered_slots, signatures_for_pool))
                        continue
                emission_variants.append((groups_for_cls, slots_source, signatures_for_pool))

            for groups_for_cls, slots_source, signatures_for_pool in emission_variants:

                cls = {
                    "tag": pattern.class_tag,
                    "student_groups": compress_groups_to_selectors(groups_for_cls, selector_map, group_order),
                }
                per_week = max((len(slots) for slots in slots_source.values()), default=1)
                if per_week != 1:
                    cls["per_week"] = per_week
                if data["duration_slots"] != 1:
                    cls["duration_slots"] = data["duration_slots"]
                if is_english_lesson(pattern.course):
                    cls["instructor_pool"] = sorted({tid for sig in signatures_for_pool for tid in sig})
                elif len(signatures_for_pool) == 1:
                    sig = signatures_for_pool[0]
                    if len(sig) == 0:
                        cls["instructor_pool"] = []
                    elif len(sig) == 1:
                        cls["instructor_pool"] = [sig[0]]
                    else:
                        cls["instructor_pool"] = [list(sig)]
                elif all(len(sig) == 1 for sig in signatures_for_pool):
                    cls["instructor_pool"] = sorted({sig[0] for sig in signatures_for_pool})
                else:
                    cls["instructor_pool"] = [list(sig) for sig in signatures_for_pool]
                if infer_per_group(
                    pattern.class_tag,
                    cls["student_groups"],
                    is_english_course=is_english_lesson(pattern.course),
                    source_group_count=len(groups_for_cls),
                ):
                    cls["per_group"] = True
                if is_english_lesson(pattern.course) and cls.get("tag") == "class":
                    # Emit one class per concrete student group (ENG-* from XLSX
                    # when slot+teacher matches were found).
                    direct_groups = sorted(data["groups"])
                    if direct_groups:
                        for token in direct_groups:
                            split_cls = deepcopy(cls)
                            split_cls["student_groups"] = [token]
                            xlsx_per_week = english_group_per_week.get(token)
                            if xlsx_per_week and xlsx_per_week > 0:
                                if xlsx_per_week == 1:
                                    split_cls.pop("per_week", None)
                                else:
                                    split_cls["per_week"] = xlsx_per_week
                            xlsx_instructors = english_group_instructors.get(token, set())
                            if xlsx_instructors:
                                split_cls["instructor_pool"] = sorted(xlsx_instructors)
                                courses_map[pattern.course].append(split_cls)
                                continue
                            # Narrow teacher pool to instructors that actually teach
                            # this specific foreign-language group.
                            group_signatures = [
                                sig for sig in teacher_signatures if token in data["groups_by_signature"].get(sig, set())
                            ]
                            if group_signatures:
                                split_cls["instructor_pool"] = sorted({tid for sig in group_signatures for tid in sig})
                            courses_map[pattern.course].append(split_cls)
                        continue
                courses_map[pattern.course].append(cls)

        for course_name, components in courses_map.items():
            courses_map[course_name] = sorted(
                components,
                key=lambda cls: (
                    tag_order.get(cls.get("tag", ""), 99),
                    class_group_rank(cls, selector_map, group_order),
                ),
            )

        # Generic relation mapping: connect each tutorial to the best matching lecture.
        for components in courses_map.values():
            lecture_indices = [idx for idx, cls in enumerate(components) if cls.get("tag") == "lec"]
            tutorial_indices = [idx for idx, cls in enumerate(components) if cls.get("tag") == "tut"]
            if not lecture_indices or not tutorial_indices:
                continue
            # Keep config compact: only emit relates_to for ambiguous multi-stream cases.
            if len(lecture_indices) <= 1 and len(tutorial_indices) <= 1:
                continue

            def _as_set(value: Any) -> set[str]:
                if not isinstance(value, list):
                    return set()
                return {str(item) for item in value if isinstance(item, str)}

            def _flatten_instructors(value: Any) -> set[str]:
                if not isinstance(value, list):
                    return set()
                flat: set[str] = set()
                for item in value:
                    if isinstance(item, str):
                        flat.add(item)
                    elif isinstance(item, list):
                        flat.update(str(v) for v in item if isinstance(v, str))
                return flat

            for tut_idx, tut_cls in enumerate(components):
                if tut_cls.get("tag") != "tut" or tut_cls.get("relates_to") is not None:
                    continue

                tut_groups = _as_set(tut_cls.get("student_groups"))
                tut_instructors = _flatten_instructors(tut_cls.get("instructor_pool"))
                best: tuple[tuple[int, int, int, int], int] | None = None

                for lec_idx in lecture_indices:
                    lec_cls = components[lec_idx]
                    lec_groups = _as_set(lec_cls.get("student_groups"))
                    lec_instructors = _flatten_instructors(lec_cls.get("instructor_pool"))
                    group_overlap = len(tut_groups & lec_groups)
                    instructor_overlap = len(tut_instructors & lec_instructors)
                    if group_overlap == 0 and instructor_overlap == 0:
                        continue

                    score = (
                        group_overlap,
                        instructor_overlap,
                        1 if lec_idx < tut_idx else 0,
                        -abs(tut_idx - lec_idx),
                    )
                    if best is None or score > best[0]:
                        best = (score, lec_idx)

                if best is not None:
                    tut_cls["relates_to"] = best[1]

        instructors = [
            {
                "id": iid,
                "name": name,
            }
            for iid, name in sorted(instructors_map.items())
        ]

        return {
            "term": {
                "name": "Spring 2026",
                "semester": {"start_date": global_start, "end_date": global_end},
            },
            "rooms": rooms,
            "instructors": instructors,
            "programs": simplified_programs,
            "student_groups": groups_bucket,
            "courses": [
                {
                    "name": course_name,
                    "course_tags": infer_course_tags(course_name),
                    "components": components,
                }
                for course_name, components in sorted(courses_map.items())
            ],
        }

    if block_rows["block1"] and block_rows["block2"]:
        selected_by_block = {
            "block1": block_rows["block1"] + unclassified_rows,
            "block2": block_rows["block2"] + unclassified_rows,
        }
        for block_key, selected_rows in selected_by_block.items():
            if not selected_rows:
                continue
            output_path = output_path_for_block(args.output_yaml, block_key)
            styled_config = apply_yaml_style_overrides(render_config(selected_rows))
            output_path.write_text(
                yaml.dump(styled_config, Dumper=ConfigDumper, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            print(f"Wrote {output_path}")
    else:
        styled_config = apply_yaml_style_overrides(render_config(rows))
        args.output_yaml.write_text(
            yaml.dump(styled_config, Dumper=ConfigDumper, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"Wrote {args.output_yaml}")


if __name__ == "__main__":
    main()
