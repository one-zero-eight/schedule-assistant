"""Negative-path tests for main.solve_schedule."""

from __future__ import annotations

from main import Schedule, solve_schedule
from metrics import assert_all_satisfied
from test_solver import _case_config


def test_empty_no_teaching_days(tmp_path) -> None:
    cfg = _case_config("empty_no_teaching_days")
    out = solve_schedule(cfg, time_limit=5, show_progress=False, artifacts_dir=tmp_path)
    assert out.status in ("EMPTY", "OPTIMAL", "FEASIBLE")


def test_empty_no_rooms(tmp_path) -> None:
    cfg = _case_config("empty_no_rooms")
    out = solve_schedule(cfg, time_limit=5, show_progress=False, artifacts_dir=tmp_path)
    assert out.status == "EMPTY"
    assert out.schedule == Schedule()
    assert out.stats.error == "no rooms in config"


def test_empty_input_yields_empty_schedule(tmp_path) -> None:
    """No courses, course without components, or component with no audience → no meetings."""
    cfgs = [
        _case_config("empty_no_courses"),
        _case_config("empty_course_no_components"),
        _case_config("empty_component_no_audience"),
    ]
    for cfg in cfgs:
        out = solve_schedule(cfg, time_limit=5, show_progress=False, artifacts_dir=tmp_path)
        assert out.status == "EMPTY"
        assert_all_satisfied(out, cfg)
        assert out.schedule == Schedule()
        assert not out.schedule.courses
        assert out.stats.meetings == 0
        assert out.stats.slots == 3
        assert out.stats.error == "no meetings to schedule"
        assert out.stats.slots_per_day is None
        assert out.stats.teaching_days is None


def test_infeasible_output_solver(tmp_path) -> None:
    """When CP-SAT finds no solution, schedule remains empty (no courses list)."""
    for case_name in ("infeasible_single_slot", "infeasible_example"):
        cfg = _case_config(case_name)
        out = solve_schedule(cfg, time_limit=30, show_progress=False, artifacts_dir=tmp_path)
        assert out.status in ("INFEASIBLE", "UNKNOWN")
        assert out.schedule == Schedule()
        assert not out.schedule.courses
        assert out.stats.meetings == 2
