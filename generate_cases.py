from __future__ import annotations
from pathlib import Path

import yaml
from config import TermConfig

ROOT = Path(__file__).resolve().parent


def _default_time_slots_from_config_class() -> list[str]:
    default_slots = TermConfig.model_fields["time_slots"].default
    return [slot.strftime("%H:%M") for slot in default_slots]


def _group_codes_from_entries(entries: list | None) -> set[str]:
    out: set[str] = set()
    for item in entries or []:
        if isinstance(item, dict):
            code = item.get("code")
            if code:
                out.add(str(code))
        elif item is not None and str(item).strip() != "":
            out.add(str(item))
    return out


def _build_selector_map(raw_cfg: dict) -> dict[str, set[str]]:
    selector_map: dict[str, set[str]] = {}
    for section in raw_cfg.get("sections", []):
        for program in section.get("programs", []):
            program_code = program.get("code")
            if not program_code:
                continue
            program_groups: set[str] = set()
            program_groups.update(_group_codes_from_entries(program.get("groups")))
            for track in program.get("tracks", []):
                track_name = track.get("name")
                groups = _group_codes_from_entries(track.get("groups"))
                if track_name and groups:
                    selector_map[f"@{program_code}/{track_name}"] = groups
                    program_groups.update(groups)
            if program_groups:
                selector_map[f"@{program_code}"] = program_groups
    return selector_map


def _expand_groups(tokens: list[str], selector_map: dict[str, set[str]]) -> list[str]:
    out: set[str] = set()
    for token in tokens:
        out.update(selector_map.get(token, {token}))
    return sorted(out)


def _index_distribution_by_kind(cfg: dict, kind: str) -> dict[str, dict]:
    return {
        item["code"]: item
        for item in cfg.get("students_groups", [])
        if item.get("kind") == kind and item.get("code")
    }


def _base_case_cfg(cfg: dict) -> dict:
    case_cfg = dict(cfg)
    case_cfg["term"] = dict(cfg["term"])
    case_cfg["term"]["semester"] = dict(cfg["term"]["semester"])
    if "days" in cfg["term"]:
        case_cfg["term"]["days"] = list(cfg["term"]["days"])
    if "starting_day" in cfg["term"] and cfg["term"]["starting_day"]:
        case_cfg["term"]["starting_day"] = cfg["term"]["starting_day"]
    elif case_cfg["term"].get("days"):
        case_cfg["term"]["starting_day"] = case_cfg["term"]["days"][0]
    else:
        case_cfg["term"]["starting_day"] = "Mon"
    case_cfg["term"]["time_slots"] = _default_time_slots_from_config_class()
    return case_cfg


def _filter_courses(cfg: dict, selector_map: dict[str, set[str]], target_groups: set[str]) -> list[dict]:
    filtered_courses: list[dict] = []
    for course_item in cfg.get("courses", []):
        filtered_components = []
        components = course_item.get("components") or []
        for comp_item in components:
            expanded = set(_expand_groups(comp_item.get("student_groups", []), selector_map))
            selected_groups = sorted(expanded.intersection(target_groups))
            if not selected_groups:
                continue
            component = dict(comp_item)
            component["student_groups"] = selected_groups
            filtered_components.append(component)
        if filtered_components:
            course = dict(course_item)
            course["components"] = filtered_components
            filtered_courses.append(course)
    return filtered_courses


def _generate_program_year_cases(candidate_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with candidate_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    selector_map = _build_selector_map(cfg)
    core_by_code = _index_distribution_by_kind(cfg, "core")
    english_by_code = _index_distribution_by_kind(cfg, "english")

    for old_case in output_dir.glob("*.yaml"):
        old_case.unlink()

    sections = cfg.get("sections", [])
    core_section = next((s for s in sections if s.get("code") == "core"), {"programs": []})
    grouped_by_year: dict[int, list[dict]] = {}
    for program in core_section.get("programs", []):
        year = program.get("year")
        if year is None:
            continue
        grouped_by_year.setdefault(int(year), []).append(program)

    for year, year_programs in grouped_by_year.items():
        target_group_candidates: set[str] = set()
        for program in year_programs:
            target_group_candidates.update(_group_codes_from_entries(program.get("groups")))
            for track in program.get("tracks", []):
                target_group_candidates.update(_group_codes_from_entries(track.get("groups")))
        target_group_candidates.intersection_update(core_by_code.keys())
        target_groups = set(target_group_candidates)
        if year == 1 and english_by_code:
            english_target_groups: set[str] = set()
            for course_item in cfg.get("courses", []):
                course_name = str(course_item.get("name", "")).strip().lower()
                if "english" not in course_name and "foreign language" not in course_name:
                    continue
                for comp_item in course_item.get("components", []):
                    expanded = set(_expand_groups(comp_item.get("student_groups", []), selector_map))
                    english_target_groups.update(group for group in expanded if group in english_by_code)
            target_groups.update(english_target_groups)
        if not target_groups:
            continue

        case_cfg = _base_case_cfg(cfg)
        case_cfg["courses"] = _filter_courses(cfg, selector_map, target_groups)
        case_cfg["students_groups"] = list(cfg.get("students_groups", []))
        case_cfg["rooms"] = list(cfg.get("rooms", []))
        case_cfg["instructors"] = list(cfg.get("instructors", []))
        case_cfg["sections"] = list(cfg.get("sections", []))

        slug = f"core_year_{year}"
        output_path = output_dir / f"{slug}.yaml"
        output_path.write_text(
            yaml.safe_dump(case_cfg, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _generate_program_level_full_cases(candidate_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with candidate_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    selector_map = _build_selector_map(cfg)
    core_by_code = _index_distribution_by_kind(cfg, "core")

    for old_case in output_dir.glob("*.yaml"):
        old_case.unlink()

    sections = cfg.get("sections", [])
    core_section = next((s for s in sections if s.get("code") == "core"), None)
    if not core_section:
        return

    target_group_candidates: set[str] = set()
    for program in core_section.get("programs", []):
        target_group_candidates.update(_group_codes_from_entries(program.get("groups")))
        for track in program.get("tracks", []):
            target_group_candidates.update(_group_codes_from_entries(track.get("groups")))
    target_group_candidates.intersection_update(core_by_code.keys())
    target_groups = set(target_group_candidates)
    if not target_groups:
        return

    case_cfg = _base_case_cfg(cfg)
    case_cfg["courses"] = _filter_courses(cfg, selector_map, target_groups)
    case_cfg["students_groups"] = list(cfg.get("students_groups", []))
    case_cfg["rooms"] = list(cfg.get("rooms", []))
    case_cfg["instructors"] = list(cfg.get("instructors", []))
    case_cfg["sections"] = list(cfg.get("sections", []))

    output_path = output_dir / "all_bachelors_masters_phd.yaml"
    output_path.write_text(
        yaml.safe_dump(case_cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _generate_program_level_full_with_english_cases(candidate_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with candidate_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    selector_map = _build_selector_map(cfg)
    core_by_code = _index_distribution_by_kind(cfg, "core")
    english_by_code = _index_distribution_by_kind(cfg, "english")

    for old_case in output_dir.glob("*.yaml"):
        old_case.unlink()

    sections = cfg.get("sections", [])
    core_section = next((s for s in sections if s.get("code") == "core"), None)
    if not core_section:
        return

    academic_target_group_candidates: set[str] = set()
    for program in core_section.get("programs", []):
        academic_target_group_candidates.update(_group_codes_from_entries(program.get("groups")))
        for track in program.get("tracks", []):
            academic_target_group_candidates.update(_group_codes_from_entries(track.get("groups")))
    academic_target_group_candidates.intersection_update(core_by_code.keys())
    academic_target_groups = set(academic_target_group_candidates)

    english_target_groups: set[str] = set()
    for course_item in cfg.get("courses", []):
        course_name = str(course_item.get("name", "")).strip().lower()
        if "english" not in course_name and "foreign language" not in course_name:
            continue
        for comp_item in course_item.get("components", []):
            expanded = set(_expand_groups(comp_item.get("student_groups", []), selector_map))
            english_target_groups.update(group for group in expanded if group in english_by_code)

    target_groups = set(academic_target_groups)
    target_groups.update(english_target_groups)
    if not target_groups:
        return

    case_cfg = _base_case_cfg(cfg)
    case_cfg["courses"] = _filter_courses(cfg, selector_map, target_groups)
    case_cfg["students_groups"] = list(cfg.get("students_groups", []))
    case_cfg["rooms"] = list(cfg.get("rooms", []))
    case_cfg["instructors"] = list(cfg.get("instructors", []))
    case_cfg["sections"] = list(cfg.get("sections", []))

    output_path = output_dir / "all_bachelors_masters_phd_with_english.yaml"
    output_path.write_text(
        yaml.safe_dump(case_cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    candidate_by_block = {
        "block1": ROOT / "config-candidate-block1.yaml",
        "block2": ROOT / "config-candidate-block2.yaml",
    }
    for block, candidate_path in candidate_by_block.items():
        if not candidate_path.exists():
            continue
        tests_dir = base_dir / "tests" / "cases"
        tests_dir.mkdir(parents=True, exist_ok=True)
        program_year_output_dir = tests_dir / f"feasible_by_program_year_{block}"
        _generate_program_year_cases(candidate_path, program_year_output_dir)

        program_level_output_dir = tests_dir / f"feasible_full_config_{block}"
        _generate_program_level_full_cases(candidate_path, program_level_output_dir)

        if block == "block1":
            with_english_output_dir = tests_dir / f"feasible_full_with_english_{block}"
            _generate_program_level_full_with_english_cases(candidate_path, with_english_output_dir)


if __name__ == "__main__":
    main()
