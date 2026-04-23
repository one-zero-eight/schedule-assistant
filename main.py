from __future__ import annotations

import argparse
import datetime
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from ortools.sat.python import cp_model
from tqdm import tqdm

from config import ScheduleConfig, SettingBaseModel, expand_groups, resolve_selector_map

DAY_TO_WEEKDAY = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


class _YamlDumper(yaml.SafeDumper):
    pass


def _yaml_str_presenter(dumper: yaml.SafeDumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_YamlDumper.add_representer(str, _yaml_str_presenter)


def _safe_term_segment(cfg: ScheduleConfig) -> str:
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in cfg.term.name.strip() or "term")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.replace(" ", "_").strip("_") or "term"


def _results_dir_for_run(cfg: ScheduleConfig) -> Path:
    """``results/{timestamp}_{slug}/`` — one folder per invocation (slug from term name)."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug = _safe_term_segment(cfg)
    out = Path("results") / f"{ts}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _with_available_postfix(path: Path) -> Path:
    """Return path, or ``path_#`` if it already exists."""
    if not path.exists():
        return path
    for idx in range(1, 10_000):
        candidate = path.parent / f"{path.name}_{idx}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find free artifacts directory postfix for {path}")


def save_output(artifacts_dir: Path | None, result: SolveResult) -> None:
    if artifacts_dir is None:
        return
    saved_at = datetime.datetime.now(datetime.timezone.utc)
    if result.stats.started_at is None:
        result.stats.started_at = saved_at
    result.stats.saved_at = saved_at
    result.stats.elapsed_seconds = max(0.0, (saved_at - result.stats.started_at).total_seconds())
    (artifacts_dir / "output.yaml").write_text(
        yaml.dump(
            result.model_dump(mode="json"),
            Dumper=_YamlDumper,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
        ),
        encoding="utf-8",
    )


@dataclass
class Meeting:
    course: str
    tag: str
    groups: list[str]
    instructor_options: list[list[str]]
    duration: int = 1
    course_idx: int = -1
    class_idx: int = -1
    week_index: int = 0
    audience_key: tuple[str, ...] = ()
    expected_students: int = 0


@dataclass
class PreparedModel:
    model: cp_model.CpModel
    day_vars: list[cp_model.IntVar]
    local_start_vars: list[cp_model.IntVar]
    absolute_start_vars: list[cp_model.IntVar]
    absolute_end_vars: list[cp_model.IntVar]
    room_vars: list[cp_model.IntVar]
    inst_choice_vars: list[cp_model.IntVar]


@dataclass
class SolveExecution:
    solver: cp_model.CpSolver
    status: Any
    phase_stats: SolveStats.PhaseStats


class CourseSchedule(SettingBaseModel):
    """One course and its scheduled components."""

    class ComponentOutput(SettingBaseModel):
        """Config-level component (lec/tut/lab) with placed sessions."""

        class SessionSeries(SettingBaseModel):
            """Parallel lists for one audience (group set) across sessions."""

            audience: list[str] = []
            days: list[str] = []
            day_indices: list[int] = []
            start_times: list[datetime.time] = []
            rooms: list[str] = []
            instructors: list[list[str]] = []

            def sorted_by_time(self) -> Self:
                combined = list(
                    zip(
                        self.day_indices,
                        self.days,
                        self.start_times,
                        self.rooms,
                        self.instructors,
                    )
                )
                combined.sort(key=lambda x: (x[0], x[2]))
                self.day_indices = [x[0] for x in combined]
                self.days = [x[1] for x in combined]
                self.start_times = [x[2] for x in combined]
                self.rooms = [x[3] for x in combined]
                self.instructors = [x[4] for x in combined]
                return self

        tag: str
        student_groups: list[str]
        instructor_pool: list[str | list[str]]
        sessions: list[SessionSeries]

    name: str
    course_tags: list[str]
    components: list[ComponentOutput]


class Schedule(SettingBaseModel):
    """Root of the solved schedule payload."""

    courses: list[CourseSchedule] = []


class SolveStats(SettingBaseModel):
    class PhaseStats(SettingBaseModel):
        phase: str
        decision: str
        solver_status: str | None = None
        objective_value: float | None = None
        best_objective_bound: float | None = None
        max_time_in_seconds: float | None = None
        solver_parameters: str | None = None
        variable_count: int | None = None
        constraint_count: int | None = None
        response_stats: str | None = None
        solution_info: str | None = None

    meetings: int
    slots: int
    error: str | None = None
    started_at: datetime.datetime | None = None
    saved_at: datetime.datetime | None = None
    elapsed_seconds: float | None = None
    slots_per_day: int | None = None
    teaching_days: int | None = None
    phase_stats: list[PhaseStats] = []


class SolveResult(SettingBaseModel):
    status: Literal["OPTIMAL", "INFEASIBLE", "FEASIBLE", "MODEL_INVALID", "UNKNOWN", "EMPTY"]
    """The possible status codes are (from OR-Tools documentation):

    OPTIMAL (4): An optimal solution has been found.
    FEASIBLE (2): A feasible solution has been found, and a bound may be available to assess its quality via solver.best_objective_bound.
    INFEASIBLE (3): No solution can satisfy all constraints.
    MODEL_INVALID (1): The CP-SAT model is incorrectly specified.
    UNKNOWN (0): No solution was found, and no infeasibility proof is available. A bound may still be available.

    + EMPTY when the solver did not run: nothing to schedule, or a pre-solver
      validation failure (see stats.error). Not a CP-SAT status.
    """
    stats: SolveStats
    artifacts_dir: Path | None = None
    "Directory containing solver artifacts (e.g. output.yaml, solver_log_phase_*.txt); None when no artifact directory is used."
    schedule: Schedule


def teaching_days(cfg: ScheduleConfig) -> list[str]:
    """Build one teaching week ordered from `term.starting_day`."""
    configured_days: list[str] = []
    seen_days: set[str] = set()
    for raw_day in cfg.term.days:
        day = str(raw_day).strip()
        if day not in DAY_TO_WEEKDAY or day in seen_days:
            continue
        seen_days.add(day)
        configured_days.append(day)
    if not configured_days:
        return []
    start_day = str(cfg.term.starting_day).strip()
    if start_day not in configured_days:
        return configured_days
    start_idx = configured_days.index(start_day)
    return configured_days[start_idx:] + configured_days[:start_idx]


def _compact_multiline(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    return "\n".join(lines)


def prepare_model(
    meetings: list[Meeting],
    *,
    num_days: int,
    slots_per_day: int,
    room_ids: list[str],
    room_capacities: list[int],
    group_students_map: dict[str, set[str]],
    shared_students: set[str],
    show_progress: bool,
) -> tuple[PreparedModel | None, str | None]:
    """Build the CP-SAT model.

    Layout (top to bottom):
      1. Per-meeting decision variables (day, local slot, absolute start/end).
      2. No-overlap resources: groups, shared-student profiles, rooms, instructors.
      3. Room assignment: `room_v` + reified bools channelled to optional intervals.
      4. Instructor assignment: option bools channelled to `inst_choice` + optional intervals.

    Returns `(None, reason)` if the instance is infeasible at build time
    (e.g. no room fits a meeting's expected enrollment).
    """
    model = cp_model.CpModel()

    # --- Output var arrays (indexed by meeting position) ---------------------
    day_vars: list[cp_model.IntVar] = []
    local_start_vars: list[cp_model.IntVar] = []
    absolute_start_vars: list[cp_model.IntVar] = []
    absolute_end_vars: list[cp_model.IntVar] = []
    room_vars: list[cp_model.IntVar] = []
    inst_choice_vars: list[cp_model.IntVar] = []

    # --- Resource → intervals for later no_overlap posting -------------------
    # group_id      -> intervals of meetings attended by that group (mandatory).
    # profile       -> intervals of meetings touching a unique shared-student profile.
    # room_idx      -> intervals of meetings placed in that room (optional if >1 choice).
    # instructor_id -> intervals of meetings assigned to that instructor (optional if >1 option).
    group_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    profile_intervals: dict[frozenset[str], list[cp_model.IntervalVar]] = defaultdict(list)
    room_intervals: dict[int, list[cp_model.IntervalVar]] = defaultdict(list)
    inst_to_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)

    max_abs = num_days * slots_per_day
    fallback_attendance_ratio = 0.9

    # ---------------------------------------------------------------
    # Shared-student profiles.
    #
    # Any student belonging to >1 group creates a cross-group conflict: two
    # meetings whose audiences share this student must not overlap. Students
    # with identical group-membership sets yield identical no_overlap
    # constraints, so we dedupe to one constraint per unique profile.
    # `profiles_by_group[g]` lists every profile that contains group `g`;
    # a meeting's intervals are added once per distinct profile it touches.
    # ---------------------------------------------------------------
    profiles_by_group: dict[str, list[frozenset[str]]] = defaultdict(list)
    if shared_students:
        student_to_groups: dict[str, set[str]] = defaultdict(set)
        for group_id, members in group_students_map.items():
            for s in members & shared_students:
                student_to_groups[s].add(group_id)
        unique_profiles: set[frozenset[str]] = {frozenset(gs) for gs in student_to_groups.values() if len(gs) > 1}
        for profile in unique_profiles:
            for group_id in profile:
                profiles_by_group[group_id].append(profile)

    # ---------------------------------------------------------------
    # Feasible rooms per meeting.
    #
    # Rooms are eligible if capacity >= required_capacity. For very large
    # meetings (>100) or when no room fits the full enrollment, we relax the
    # required capacity to `fallback_attendance_ratio * students` to account
    # for realistic attendance. Early-exit with a reason string if any meeting
    # has no eligible room.
    # ---------------------------------------------------------------
    feasible_room_indices_by_meeting: dict[int, list[int]] = {}
    for i, meeting in enumerate(meetings):
        students = meeting.expected_students
        if students <= 0:
            feasible_rooms = list(range(len(room_ids)))
            if not feasible_rooms:
                return None, "no feasible room"
            feasible_room_indices_by_meeting[i] = feasible_rooms
            continue
        feasible_for_full = any(capacity >= students for capacity in room_capacities)
        required_capacity = students
        if students > 100 or not feasible_for_full:
            required_capacity = math.ceil(students * fallback_attendance_ratio)
        required_capacity = max(1, required_capacity)
        feasible_rooms = [room_idx for room_idx, cap in enumerate(room_capacities) if cap >= required_capacity]
        if not feasible_rooms:
            return None, f"no feasible room for meeting {i}"
        feasible_room_indices_by_meeting[i] = feasible_rooms

    # ===============================================================
    # Meeting timing variables + group / profile intervals.
    # ===============================================================
    for i, meeting in enumerate(meetings):
        dur = meeting.duration

        # Timing decision vars. `abs_s = day * slots_per_day + local_s` links
        # day/local-slot to a single absolute-start coordinate used by every
        # no-overlap resource.
        day_v = model.new_int_var(0, num_days - 1, f"day_{i}")
        loc_lo = max(0, slots_per_day - dur)
        local_s = model.new_int_var(0, loc_lo, f"local_start_{i}")
        local_e = model.new_int_var(dur, slots_per_day, f"local_end_{i}")
        model.add(local_e == local_s + dur)

        abs_s = model.new_int_var(0, max_abs - dur, f"abs_start_{i}")
        abs_e = model.new_int_var(dur, max_abs, f"abs_end_{i}")
        model.add(abs_s == day_v * slots_per_day + local_s)
        model.add(abs_e == abs_s + dur)

        day_vars.append(day_v)
        local_start_vars.append(local_s)
        absolute_start_vars.append(abs_s)
        absolute_end_vars.append(abs_e)

        # Mandatory "core" interval shared by group- and profile-level conflicts.
        core = model.new_interval_var(abs_s, dur, abs_e, f"group_core_{i}")
        for group_id in meeting.groups:
            group_intervals[group_id].append(core)
        if profiles_by_group:
            seen_profiles: set[frozenset[str]] = set()
            for group_id in meeting.groups:
                for profile in profiles_by_group.get(group_id, ()):
                    if profile in seen_profiles:
                        continue
                    seen_profiles.add(profile)
                    profile_intervals[profile].append(core)

        # Room decision var: a constant when there's only one feasible room,
        # otherwise a sparse-domain int var (the actual room is channelled to
        # pick-bools in pass 2).
        feasible_room_indices = feasible_room_indices_by_meeting[i]
        if len(feasible_room_indices) == 1:
            room_v = model.new_constant(feasible_room_indices[0])
        else:
            room_v = model.new_int_var_from_domain(cp_model.Domain.FromValues(feasible_room_indices), f"room_{i}")
        room_vars.append(room_v)

        # Instructor-option index: constant when there's only one option,
        # otherwise linked to option bools in pass 3.
        n_opts = len(meeting.instructor_options)
        if n_opts > 1:
            inst_choice = model.new_int_var(0, n_opts - 1, f"inst_choice_{i}")
        else:
            inst_choice = model.new_constant(0)
        inst_choice_vars.append(inst_choice)

    # ===============================================================
    # Group & shared-student-profile no-overlap constraints.
    # (Mandatory intervals -> CP-SAT's disjunctive propagator.)
    # ===============================================================
    for ivals in tqdm(group_intervals.values(), desc="Group constraints", disable=not show_progress):
        if len(ivals) > 1:
            model.add_no_overlap(ivals)
    for ivals in tqdm(profile_intervals.values(), desc="Profile constraints", disable=not show_progress):
        if len(ivals) > 1:
            model.add_no_overlap(ivals)

    # ===============================================================
    # Room assignment.
    #
    # Single-room case: attach a mandatory interval to the sole room.
    # Multi-room case: create one pick-bool per feasible room, attach an
    # optional interval gated by the bool, and channel room_v to the bools
    # via a single linear sum (`room_v == Σ idx · b`) instead of per-room
    # OnlyEnforceIf reifications.
    # ===============================================================
    for i, meeting in enumerate(meetings):
        dur = meeting.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        feasible_room_indices = feasible_room_indices_by_meeting[i]
        if len(feasible_room_indices) == 1:
            only_idx = feasible_room_indices[0]
            room_intervals[only_idx].append(
                model.new_interval_var(abs_s, dur, abs_e, f"room_iv_{i}")
            )
        else:
            room_bools = {
                room_idx: model.new_bool_var(f"room_pick_{i}_{room_idx}") for room_idx in feasible_room_indices
            }
            for room_idx, b in room_bools.items():
                room_intervals[room_idx].append(
                    model.new_optional_interval_var(abs_s, dur, abs_e, b, f"room_iv_{i}_{room_idx}")
                )
            model.add_exactly_one(room_bools.values())
            model.add(room_vars[i] == sum(idx * b for idx, b in room_bools.items()))

    for room_idx in tqdm(range(len(room_ids)), desc="Room constraints", disable=not show_progress):
        if len(room_intervals[room_idx]) > 1:
            model.add_no_overlap(room_intervals[room_idx])

    # ===============================================================
    # Instructor assignment.
    #
    # Single-option case: every listed instructor attends unconditionally,
    # so we attach a mandatory interval per (meeting, instructor).
    # Multi-option case: create one bool per option, link `inst_choice` to
    # those bools via `inst_choice == Σ k · opt_b[k]`, and attach one
    # optional interval per (option, instructor) gated by the option bool.
    # ===============================================================
    for i, meeting in enumerate(tqdm(meetings, desc="Instructor constraints", disable=not show_progress)):
        dur = meeting.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        n_opts = len(meeting.instructor_options)
        if n_opts == 1:
            for inst in meeting.instructor_options[0]:
                inst_to_intervals[inst].append(model.new_interval_var(abs_s, dur, abs_e, f"inst_iv_{inst}_{i}"))
            continue
        opts_b = [model.new_bool_var(f"inst_opt_{i}_{k}") for k in range(n_opts)]
        model.add_exactly_one(opts_b)
        model.add(inst_choice_vars[i] == sum(k * b for k, b in enumerate(opts_b)))
        for opt_idx, insts in enumerate(meeting.instructor_options):
            for inst in insts:
                inst_to_intervals[inst].append(
                    model.new_optional_interval_var(abs_s, dur, abs_e, opts_b[opt_idx], f"inst_iv_{inst}_{i}_{opt_idx}")
                )

    for ivals in inst_to_intervals.values():
        if len(ivals) > 1:
            model.add_no_overlap(ivals)

    return (
        PreparedModel(
            model=model,
            day_vars=day_vars,
            local_start_vars=local_start_vars,
            absolute_start_vars=absolute_start_vars,
            absolute_end_vars=absolute_end_vars,
            room_vars=room_vars,
            inst_choice_vars=inst_choice_vars,
        ),
        None,
    )


def solve(
    prepared: PreparedModel,
    *,
    time_limit: int,
    show_progress: bool,
    artifacts_dir: Path | None,
    num_search_workers: int | None,
) -> SolveExecution:
    phase_name = "hard_constraints"
    solver = cp_model.CpSolver()
    available_cores = max(1, os.cpu_count() or 1)
    default_workers = min(16, available_cores)
    workers = default_workers if num_search_workers is None else max(1, int(num_search_workers))
    solver.parameters.num_search_workers = workers
    solver.parameters.log_search_progress = True
    solver.parameters.log_to_stdout = show_progress

    def _run_solver_once(active_log_path: Path | None = None):
        if active_log_path is not None:
            log_file = open(active_log_path, "a", encoding="utf-8")

            def _log_callback(msg: str) -> None:
                log_file.write(msg)
                log_file.write("\n")

            solver.log_callback = _log_callback
            try:
                return solver.Solve(prepared.model)
            finally:
                log_file.close()
                solver.log_callback = None
        return solver.Solve(prepared.model)

    deadline = time.monotonic() + float(time_limit)
    phase_stats = SolveStats.PhaseStats(phase=phase_name, decision="pending")
    status = cp_model.UNKNOWN
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        phase_stats = SolveStats.PhaseStats(phase=phase_name, decision="skipped_no_time")
    else:
        solver.parameters.max_time_in_seconds = max(0.1, remaining)
        phase_max_time = float(solver.parameters.max_time_in_seconds)
        if artifacts_dir is not None:
            phase_log_path = artifacts_dir / "solver_log_phase_1.txt"
            phase_log_path.write_text("", encoding="utf-8")
            status = _run_solver_once(phase_log_path)
        else:
            status = _run_solver_once()
        objective_value = float(solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
        best_objective_bound = (
            float(solver.BestObjectiveBound())
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.UNKNOWN)
            else None
        )
        phase_stats = SolveStats.PhaseStats(
            phase=phase_name,
            decision="ran",
            solver_status=solver.StatusName(status),
            objective_value=objective_value,
            best_objective_bound=best_objective_bound,
            max_time_in_seconds=phase_max_time,
            solver_parameters=_compact_multiline(str(solver.parameters)),
            variable_count=len(prepared.model.proto.variables),
            constraint_count=len(prepared.model.proto.constraints),
            response_stats=_compact_multiline(solver.ResponseStats()),
            solution_info=_compact_multiline(solver.SolutionInfo()),
        )
    return SolveExecution(solver=solver, status=status, phase_stats=phase_stats)


def solve_schedule(
    cfg: ScheduleConfig,
    time_limit: int,
    *,
    show_progress: bool = False,
    artifacts_dir: Path | None = None,
    num_search_workers: int | None = None,
) -> SolveResult:
    run_started_at = datetime.datetime.now(datetime.timezone.utc)

    def _finalize_result(result: SolveResult) -> SolveResult:
        if result.stats.started_at is None:
            result.stats.started_at = run_started_at
        save_output(artifacts_dir, result)
        return result

    def _empty_result(error: str, *, meetings_count: int = 0, slots_count: int = 0) -> SolveResult:
        return _finalize_result(
            SolveResult(
                status="EMPTY",
                schedule=Schedule(),
                stats=SolveStats(meetings=meetings_count, slots=slots_count, error=error),
                artifacts_dir=artifacts_dir,
            )
        )

    # Precompute static scheduling inputs from config.
    selector_map = resolve_selector_map(cfg)
    days = teaching_days(cfg)
    num_days = len(days)
    if num_days == 0:
        return _empty_result("no teaching days in term")

    slots_per_day = len(cfg.term.time_slots)
    room_ids = [r.id for r in cfg.rooms]
    if not room_ids:
        return _empty_result("no rooms in config")

    room_capacities = [r.capacity for r in cfg.rooms]
    group_size_map: dict[str, int] = {}
    group_students_map: dict[str, set[str]] = defaultdict(set)
    student_groups_membership: dict[str, set[str]] = defaultdict(set)
    for group in cfg.students_groups:
        group_size_map[group.code] = max(0, int(group.estimated_size or 0))
        for student_email in group.students:
            student = student_email.strip().lower()
            if not student:
                continue
            group_students_map[group.code].add(student)
            student_groups_membership[student].add(group.code)
    shared_students = {student for student, groups in student_groups_membership.items() if len(groups) > 1}

    # Expand course components into concrete meeting instances (one per week/audience).
    meetings: list[Meeting] = []
    for c_idx, course in enumerate(tqdm(cfg.courses, desc="Building meetings", disable=not show_progress)):
        for cls_idx, cls in enumerate(course.components):
            groups = expand_groups(cls.student_groups, selector_map)
            if not groups:
                continue

            audiences = [[g] for g in groups] if cls.per_group else [groups]

            count = cls.per_week

            instructor_options: list[list[str]] = []
            if cls.instructor_pool:
                for p in cls.instructor_pool:
                    instructor_options.append(p if isinstance(p, list) else [p])
            if not instructor_options:
                instructor_options = [[]]

            for audience in audiences:
                aud_key = tuple(audience)
                expected_students = (
                    cls.expected_enrollment
                    if cls.expected_enrollment is not None
                    else sum(group_size_map.get(group_id, 0) for group_id in audience)
                )
                for w in range(count):
                    meetings.append(
                        Meeting(
                            course=course.name,
                            tag=cls.tag,
                            groups=list(audience),
                            instructor_options=instructor_options,
                            course_idx=c_idx,
                            class_idx=cls_idx,
                            week_index=w,
                            audience_key=aud_key,
                            expected_students=max(0, int(expected_students)),
                        )
                    )

    if not meetings:
        return _empty_result("no meetings to schedule", slots_count=num_days * slots_per_day)

    prepared, prepare_error = prepare_model(
        meetings,
        num_days=num_days,
        slots_per_day=slots_per_day,
        room_ids=room_ids,
        room_capacities=room_capacities,
        group_students_map=group_students_map,
        shared_students=shared_students,
        show_progress=show_progress,
    )
    if prepared is None:
        return _empty_result(
            prepare_error or "model preparation failed",
            meetings_count=len(meetings),
            slots_count=num_days * slots_per_day,
        )

    execution = solve(
        prepared,
        time_limit=time_limit,
        show_progress=show_progress,
        artifacts_dir=artifacts_dir,
        num_search_workers=num_search_workers,
    )
    solver = execution.solver
    status = execution.status
    stats = SolveStats(
        meetings=len(meetings),
        slots=num_days * slots_per_day,
        slots_per_day=slots_per_day,
        teaching_days=num_days,
        phase_stats=[execution.phase_stats],
    )
    schedule_empty = Schedule()

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            assumptions = solver.sufficient_assumptions_for_infeasibility()

            if assumptions:
                print("Minimal unsatisfiable core:")
                for var_index in assumptions:
                    print(f"{var_index}: '{prepared.model.proto.variables[var_index].name}'")

        result = SolveResult(
            status=solver.StatusName(status),  # pyright: ignore[reportArgumentType]
            schedule=schedule_empty,
            stats=stats,
            artifacts_dir=artifacts_dir,
        )
        return _finalize_result(result)

    output_courses: list[CourseSchedule] = []

    for c_idx, course_cfg in enumerate(cfg.courses):
        course_output = CourseSchedule(
            name=course_cfg.name,
            course_tags=list(course_cfg.course_tags),
            components=[],
        )

        for cls_idx, cls_cfg in enumerate(course_cfg.components):
            meeting_indices = [i for i, m in enumerate(meetings) if m.course_idx == c_idx and m.class_idx == cls_idx]

            if not meeting_indices:
                continue

            instances_map: dict[tuple[str, ...], CourseSchedule.ComponentOutput.SessionSeries] = defaultdict(
                CourseSchedule.ComponentOutput.SessionSeries
            )

            for m_idx in meeting_indices:
                m = meetings[m_idx]

                di = solver.Value(prepared.day_vars[m_idx])
                t_idx = solver.Value(prepared.local_start_vars[m_idx])
                r_val = solver.Value(prepared.room_vars[m_idx])
                inst_opt_idx = solver.Value(prepared.inst_choice_vars[m_idx])

                day_name = days[di]
                slot_time = cfg.term.time_slots[t_idx]
                room_id = room_ids[r_val]
                chosen_insts = m.instructor_options[inst_opt_idx] if m.instructor_options else []

                g_key = tuple(m.groups)
                if not instances_map[g_key].audience:
                    instances_map[g_key].audience = list(g_key)

                instances_map[g_key].days.append(day_name)
                instances_map[g_key].day_indices.append(di)
                instances_map[g_key].start_times.append(slot_time)
                instances_map[g_key].rooms.append(room_id)
                instances_map[g_key].instructors.append(chosen_insts)

            class_output = CourseSchedule.ComponentOutput(
                tag=cls_cfg.tag,
                student_groups=cls_cfg.student_groups,
                instructor_pool=cls_cfg.instructor_pool,
                sessions=[data.sorted_by_time() for data in instances_map.values()],
            )
            course_output.components.append(class_output)

        output_courses.append(course_output)

    schedule = Schedule(courses=output_courses)

    result = SolveResult(
        status=solver.StatusName(status),  # pyright: ignore[reportArgumentType]
        schedule=schedule,
        stats=stats,
        artifacts_dir=artifacts_dir,
    )
    return _finalize_result(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--time-limit", type=int, default=60)
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--no-progress", action="store_true")

    args = parser.parse_args()
    if args.num_workers is not None and args.num_workers < 1:
        parser.error("--num-workers must be >= 1")

    cfg = ScheduleConfig.from_yaml(args.config)

    artifacts_dir = args.artifacts_dir
    if artifacts_dir is None:
        artifacts_dir = _results_dir_for_run(cfg)
    else:
        artifacts_dir = _with_available_postfix(artifacts_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=False)

    result = solve_schedule(
        cfg,
        args.time_limit,
        show_progress=not args.no_progress,
        artifacts_dir=artifacts_dir,
        num_search_workers=args.num_workers,
    )

    print(
        yaml.safe_dump(
            {"status": result.status, "stats": result.stats.model_dump(mode="json")},
            sort_keys=False,
            allow_unicode=True,
        ).rstrip()
    )
    schedule_path = artifacts_dir / "output.yaml"
    print(f"Schedule written to {schedule_path.resolve()}", flush=True)
    if result.artifacts_dir is not None:
        phase_log_paths = sorted(result.artifacts_dir.glob("solver_log_phase_*.txt"))
        if phase_log_paths:
            print("Solver logs:", flush=True)
            for phase_log_path in phase_log_paths:
                print(f"- {phase_log_path}", flush=True)


if __name__ == "__main__":
    main()
