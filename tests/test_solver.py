"""Basic tests for main.solve_schedule."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from main import solve_schedule
from metrics import assert_all_satisfied, assert_no_conflicts

from config import ScheduleConfig

CASES_DIR = Path(__file__).parent / "cases"
PROGRAM_YEAR_CASES_DIR_BLOCK1 = CASES_DIR / "feasible_one_week_by_program_year_block1"
PROGRAM_YEAR_CASES_DIR_BLOCK2 = CASES_DIR / "feasible_one_week_by_program_year_block2"
FULL_CONFIG_CASES_DIR_BLOCK1 = CASES_DIR / "feasible_one_week_full_config_block1"
FULL_CONFIG_CASES_DIR_BLOCK2 = CASES_DIR / "feasible_one_week_full_config_block2"
TARGET_FULL_CONFIG_CASE = "all_bachelors_masters_phd.yaml"
PROGRAM_YEAR_CASE_PATHS_BLOCK1 = tuple(sorted(PROGRAM_YEAR_CASES_DIR_BLOCK1.glob("*.yaml")))
PROGRAM_YEAR_CASE_PATHS_BLOCK2 = tuple(sorted(PROGRAM_YEAR_CASES_DIR_BLOCK2.glob("*.yaml")))


def test_all_cases_define_working_days_and_timeslots() -> None:
    missing_days: list[str] = []
    missing_slots: list[str] = []
    for case_path in sorted(CASES_DIR.rglob("*.yaml")):
        with case_path.open(encoding="utf-8") as f:
            case_cfg = yaml.safe_load(f) or {}
        term = case_cfg.get("term") or {}
        if not term.get("days"):
            missing_days.append(str(case_path.relative_to(CASES_DIR)))
        if not term.get("time_slots"):
            missing_slots.append(str(case_path.relative_to(CASES_DIR)))

    assert not missing_days, f"cases missing term.days: {missing_days}"
    assert not missing_slots, f"cases missing term.time_slots: {missing_slots}"


def _case_config(name: str) -> ScheduleConfig:
    return ScheduleConfig.from_yaml(CASES_DIR / f"{name}.yaml")


def _target_full_config_case_config(dir_path: Path, *, block_name: str) -> ScheduleConfig:
    case_path = dir_path / TARGET_FULL_CONFIG_CASE
    if case_path.exists():
        return ScheduleConfig.from_yaml(case_path)
    raise AssertionError(
        f"Missing generated case {TARGET_FULL_CONFIG_CASE!r} in {block_name}. "
        "Run tests/cases/generate_cases.py first."
    )


def _assert_program_year_feasible_case(
    cfg: ScheduleConfig,
    tmp_path: Path,
    *,
    time_limit: int = 120,
) -> None:
    expected_courses = {course.name for course in cfg.courses if course.components}
    target_groups = {group.id for group in cfg.student_groups.academic}

    out = solve_schedule(cfg, time_limit=time_limit, show_progress=False, artifacts_dir=tmp_path)
    assert out.status in ("OPTIMAL", "FEASIBLE")

    actual_courses = {course.name for course in out.schedule.courses if course.components}
    assert actual_courses == expected_courses

    audiences = {
        tuple(series.audience)
        for course in out.schedule.courses
        for comp in course.components
        for series in comp.sessions
    }
    assert audiences
    assert all(set(audience).issubset(target_groups) for audience in audiences)

    assert_no_conflicts(out)
    assert_all_satisfied(out, cfg)


@pytest.fixture(scope="session")
def program_year_cases_artifacts_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("feasible_one_week_by_program_year_cases")


@pytest.fixture(scope="session")
def full_config_cases_artifacts_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("feasible_one_week_full_config_cases")


def test_feasible_single_meeting(tmp_path) -> None:
    cfg = _case_config("feasible_single_meeting")
    out = solve_schedule(cfg, time_limit=30, show_progress=False, artifacts_dir=tmp_path)
    assert out.status in ("OPTIMAL", "FEASIBLE")
    courses = out.schedule.courses
    assert len(courses) == 1
    assert courses[0].name == "Intro"
    assert len(courses[0].components) == 1
    inst = courses[0].components[0].sessions
    assert len(inst) == 1
    assert inst[0].audience == ["B26-TEST-01"]
    assert inst[0].dates
    assert inst[0].start_times
    assert inst[0].rooms
    assert_no_conflicts(out)
    assert_all_satisfied(out, cfg)
    st = out.stats
    assert st.meetings == 1
    assert st.slots_per_day == 3
    assert st.teaching_days == 1



@pytest.mark.parametrize("case_path", PROGRAM_YEAR_CASE_PATHS_BLOCK1, ids=lambda p: p.stem)
def test_feasible_one_week_by_program_year_cases_block1(
    case_path: Path,
    program_year_cases_artifacts_root: Path,
) -> None:
    cfg = ScheduleConfig.from_yaml(case_path)
    artifacts_dir = program_year_cases_artifacts_root / "block1" / case_path.stem
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _assert_program_year_feasible_case(cfg, artifacts_dir, time_limit=10)


@pytest.mark.parametrize("case_path", PROGRAM_YEAR_CASE_PATHS_BLOCK2, ids=lambda p: p.stem)
def test_feasible_one_week_by_program_year_cases_block2(
    case_path: Path,
    program_year_cases_artifacts_root: Path,
) -> None:
    cfg = ScheduleConfig.from_yaml(case_path)
    artifacts_dir = program_year_cases_artifacts_root / "block2" / case_path.stem
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _assert_program_year_feasible_case(cfg, artifacts_dir, time_limit=10)


def test_feasible_one_week_full_config_cases_block1(full_config_cases_artifacts_root: Path) -> None:
    cfg = _target_full_config_case_config(FULL_CONFIG_CASES_DIR_BLOCK1, block_name="block1")
    artifacts_dir = full_config_cases_artifacts_root / "block1" / "all_bachelors_masters_phd"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _assert_program_year_feasible_case(cfg, artifacts_dir, time_limit=600)


def test_feasible_one_week_full_config_cases_block2(full_config_cases_artifacts_root: Path) -> None:
    cfg = _target_full_config_case_config(FULL_CONFIG_CASES_DIR_BLOCK2, block_name="block2")
    artifacts_dir = full_config_cases_artifacts_root / "block2" / "all_bachelors_masters_phd"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _assert_program_year_feasible_case(cfg, artifacts_dir, time_limit=600)


def test_feasible_ordered_same_day(tmp_path) -> None:
    """lec → tut → lab same audience/week: solver finds a same-day ordering."""
    cfg = _case_config("feasible_ordered_same_day")
    out = solve_schedule(cfg, time_limit=30, show_progress=False, artifacts_dir=tmp_path)
    assert out.status in ("OPTIMAL", "FEASIBLE")
    comp = out.schedule.courses[0].components
    assert len(comp) == 3
    times = [comp[i].sessions[0].start_times[0] for i in range(3)]
    assert times[0] < times[1] < times[2]
    dates = [comp[i].sessions[0].dates[0] for i in range(3)]
    assert dates[0] == dates[1] == dates[2]
    assert_no_conflicts(out)
    assert_all_satisfied(out, cfg)
