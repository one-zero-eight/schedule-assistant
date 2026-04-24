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
    is_core_course: bool = False
    component_relates_to: tuple[int, ...] | None = None


@dataclass
class PreparedModel:
    model: cp_model.CpModel
    day_vars: list[cp_model.IntVar]
    local_start_vars: list[cp_model.IntVar]
    absolute_start_vars: list[cp_model.IntVar]
    absolute_end_vars: list[cp_model.IntVar]
    room_vars: list[cp_model.IntVar]
    inst_choice_vars: list[cp_model.IntVar]
    day_bool_by_meeting: list[list[cp_model.IntVar]]


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
    weekend_day_indices: frozenset[int] = frozenset(),
    late_slot_indices: frozenset[int] = frozenset(),
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
    # One-hot day bools: every objective that references "meeting m on day d"
    # gets a direct boolean instead of a lazy `==`/`!=` reification pair.
    day_bool_by_meeting: list[list[cp_model.IntVar]] = []
    for i, meeting in enumerate(meetings):
        dur = meeting.duration

        day_bools = [model.new_bool_var(f"day_is_{i}_{d}") for d in range(num_days)]
        day_bool_by_meeting.append(day_bools)
        model.add_exactly_one(day_bools)

        day_v = model.new_int_var(0, num_days - 1, f"day_{i}")
        model.add(day_v == sum(d * b for d, b in enumerate(day_bools)))

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
    #
    # While iterating, we also collect `oversize_bools`: pick-bools for
    # rooms whose capacity exceeds the meeting's required capacity by
    # more than the metric's threshold (matches `metrics.py` definition
    # of `rooms_events_much_larger_than_students_count`). We only track
    # meetings that actually have a choice between oversize and non-oversize
    # rooms; meetings forced into oversize (or never oversize) add nothing
    # to the decision space and are excluded from both the penalty sum
    # and the opportunity denominator.
    # ===============================================================
    oversize_bools: list[cp_model.IntVar] = []
    oversize_opportunities = 0
    for i, meeting in enumerate(meetings):
        dur = meeting.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        feasible_room_indices = feasible_room_indices_by_meeting[i]

        students = meeting.expected_students
        if students <= 0:
            required_for_metric = 1
        else:
            feasible_for_full = any(cap >= students for cap in room_capacities)
            required_for_metric = max(
                1, students if feasible_for_full else math.ceil(students * fallback_attendance_ratio)
            )

        def _is_oversize(room_idx: int, req: int = required_for_metric) -> bool:
            cap = room_capacities[room_idx]
            return cap > req and (cap - req) * 100 > 30 * req

        if len(feasible_room_indices) == 1:
            only_idx = feasible_room_indices[0]
            room_intervals[only_idx].append(
                model.new_interval_var(abs_s, dur, abs_e, f"room_iv_{i}")
            )
        else:
            meeting_oversize_bools: list[cp_model.IntVar] = []
            has_non_oversize = False
            room_bools = {
                room_idx: model.new_bool_var(f"room_pick_{i}_{room_idx}") for room_idx in feasible_room_indices
            }
            for room_idx, b in room_bools.items():
                room_intervals[room_idx].append(
                    model.new_optional_interval_var(abs_s, dur, abs_e, b, f"room_iv_{i}_{room_idx}")
                )
                if _is_oversize(room_idx):
                    meeting_oversize_bools.append(b)
                else:
                    has_non_oversize = True
            model.add_exactly_one(room_bools.values())
            model.add(room_vars[i] == sum(idx * b for idx, b in room_bools.items()))

            if meeting_oversize_bools and has_non_oversize:
                oversize_bools.extend(meeting_oversize_bools)
                oversize_opportunities += 1

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
    # Captured so the objective block can reify "inst i teaches meeting m".
    meeting_option_bools: list[list[cp_model.IntVar]] = [[] for _ in meetings]
    for i, meeting in enumerate(tqdm(meetings, desc="Instructor constraints", disable=not show_progress)):
        dur = meeting.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        n_opts = len(meeting.instructor_options)
        if n_opts == 1:
            for inst in meeting.instructor_options[0]:
                inst_to_intervals[inst].append(model.new_interval_var(abs_s, dur, abs_e, f"inst_iv_{inst}_{i}"))
            continue
        opts_b = [model.new_bool_var(f"inst_opt_{i}_{k}") for k in range(n_opts)]
        meeting_option_bools[i] = opts_b
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


    # ===============================================================
    # Optimization objective.
    #
    # Soft goals (all counts normalized by their config-fixed upper bound
    # so their contributions are scale-invariant across problem sizes):
    #   + Maximize same-day lec+tut+lab coverage
    #     (quality_same_day_lec_tut_lab_ratio).
    #   - Minimize assignments to rooms much larger than audience
    #     (rooms_events_much_larger_than_students_ratio).
    #   - Minimize per-(group, day) bad days where a core-course group is
    #     scheduled more than 5 meetings on the same day
    #     (hatred_student_bad_days_events_total).
    #   - Minimize per-(group, day) bad days where a core-course group
    #     has more than 3 distinct subjects on the same day
    #     (hatred_student_bad_days_distinct_total).
    #   - Minimize wrong component orderings within a course for
    #     group-sharing, relates_to-allowed meeting pairs
    #     (quality_labs_before_lecture_count,
    #      quality_labs_before_tutorial_count,
    #      quality_tutorials_before_lecture_count).
    #   - Minimize weekend (Sat/Sun) events
    #     (hatred_global_saturday_event_count,
    #      hatred_global_sunday_event_count).
    #   - Minimize events ending after 18:00
    #     (hatred_global_late_events_count).
    #   - Minimize per-group excess active days
    #     (hatred_student_total_active_days_excess_groups).
    #   - Minimize per-instructor excess active days
    #     (hatred_instructor_total_active_days_excess).
    # ===============================================================

    def _meeting_on_day(m_idx: int, d: int) -> cp_model.IntVar:
        return day_bool_by_meeting[m_idx][d]

    # --- Same-day lec+tut+lab coverage ---
    class_tag: dict[tuple[int, int], str] = {}
    class_groups: dict[tuple[int, int], set[str]] = defaultdict(set)
    for m in meetings:
        class_tag[(m.course_idx, m.class_idx)] = str(m.tag).lower()
        class_groups[(m.course_idx, m.class_idx)].update(m.groups)

    course_components: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for (course_idx, cls_idx), tag in class_tag.items():
        course_components[course_idx][tag].append(cls_idx)

    triple_rows: list[tuple[int, int, int, int, str]] = []
    for course_idx, comps in course_components.items():
        lecs = comps.get("lec", [])
        tuts = comps.get("tut", [])
        labs = comps.get("lab", [])
        if not tuts or not labs or len(lecs) != 1:
            continue
        lec_i = lecs[0]
        lec_gs = class_groups[(course_idx, lec_i)]
        for tut_i in tuts:
            tut_gs = class_groups[(course_idx, tut_i)]
            for lab_i in labs:
                lab_gs = class_groups[(course_idx, lab_i)]
                for g in sorted(lec_gs & tut_gs & lab_gs):
                    triple_rows.append((course_idx, lec_i, tut_i, lab_i, g))

    satisfied_bools: list[cp_model.IntVar] = []
    if triple_rows:
        meetings_by_cls_group: dict[tuple[int, int, str], list[int]] = defaultdict(list)
        for m_idx, m in enumerate(meetings):
            for g in m.groups:
                meetings_by_cls_group[(m.course_idx, m.class_idx, g)].append(m_idx)

        for row_idx, (c_idx, lec_i, tut_i, lab_i, g) in enumerate(triple_rows):
            lec_ms = meetings_by_cls_group.get((c_idx, lec_i, g), [])
            tut_ms = meetings_by_cls_group.get((c_idx, tut_i, g), [])
            lab_ms = meetings_by_cls_group.get((c_idx, lab_i, g), [])
            if not (lec_ms and tut_ms and lab_ms):
                continue
            day_triples: list[cp_model.IntVar] = []
            for d in range(num_days):
                triple_d = model.new_bool_var(f"ltl_day_{row_idx}_{d}")
                model.add(triple_d <= sum(_meeting_on_day(m, d) for m in lec_ms))
                model.add(triple_d <= sum(_meeting_on_day(m, d) for m in tut_ms))
                model.add(triple_d <= sum(_meeting_on_day(m, d) for m in lab_ms))
                day_triples.append(triple_d)
            sat = model.new_bool_var(f"ltl_sat_{row_idx}")
            model.add(sat <= sum(day_triples))
            satisfied_bools.append(sat)

    # --- Bad days: core-course group scheduled with > 5 meetings in a day ---
    # Per-group opportunity cap = max bad days achievable given its weekly
    # load. A group with N core events in the week can realize at most
    # `(N - 1) // threshold` bad days (0 if N <= threshold), bounded by the
    # number of teaching days. Groups with 0 possible bad days are skipped
    # to avoid creating redundant decision bools.
    bad_day_event_threshold = 5
    core_group_to_meetings: dict[str, list[int]] = defaultdict(list)
    for m_idx, m in enumerate(meetings):
        if not m.is_core_course:
            continue
        for g in m.groups:
            core_group_to_meetings[g].append(m_idx)

    bad_day_bools: list[cp_model.IntVar] = []
    bad_day_opportunities = 0
    for g, m_indices in core_group_to_meetings.items():
        weekly_load = len(m_indices)
        per_group_cap = min(num_days, max(0, (weekly_load - 1) // bad_day_event_threshold))
        if per_group_cap == 0:
            continue
        bad_day_opportunities += per_group_cap
        for d in range(num_days):
            count_expr = sum(_meeting_on_day(m, d) for m in m_indices)
            bad_day = model.new_bool_var(f"bad_day_{g}_{d}")
            model.add(count_expr <= bad_day_event_threshold).only_enforce_if(bad_day.Not())
            bad_day_bools.append(bad_day)

    # --- Bad days: > 3 distinct core subjects for a group ---
    # A bad distinct-subjects day requires >=4 distinct courses on the
    # same day, so per-group cap = 0 when the group is enrolled in <4
    # core courses, and min(num_days, total_meetings // 4) otherwise.
    # subject_present[g, d, course] is the strict OR of the group's
    # meetings of that course on day d (needs full reification — upper
    # bound alone lets the solver under-count to cheat the penalty).
    bad_day_distinct_threshold = 3
    distinct_required_for_bad = bad_day_distinct_threshold + 1
    group_course_meetings: dict[tuple[str, int], list[int]] = defaultdict(list)
    group_core_courses: dict[str, set[int]] = defaultdict(set)
    for m_idx, m in enumerate(meetings):
        if not m.is_core_course:
            continue
        for g in m.groups:
            group_core_courses[g].add(m.course_idx)
            group_course_meetings[(g, m.course_idx)].append(m_idx)

    bad_day_distinct_bools: list[cp_model.IntVar] = []
    bad_day_distinct_opportunities = 0
    for g, courses in group_core_courses.items():
        if len(courses) < distinct_required_for_bad:
            continue
        weekly_core_meetings = sum(len(group_course_meetings[(g, c)]) for c in courses)
        per_group_cap = min(num_days, weekly_core_meetings // distinct_required_for_bad)
        if per_group_cap == 0:
            continue
        bad_day_distinct_opportunities += per_group_cap
        for d in range(num_days):
            subject_present_bools: list[cp_model.IntVar] = []
            for c in courses:
                course_meetings_on_d = [_meeting_on_day(m, d) for m in group_course_meetings[(g, c)]]
                subj_present = model.new_bool_var(f"subj_{g}_d{d}_c{c}")
                model.add_max_equality(subj_present, course_meetings_on_d)
                subject_present_bools.append(subj_present)
            bad_day_distinct = model.new_bool_var(f"bad_distinct_{g}_d{d}")
            model.add(
                sum(subject_present_bools) <= bad_day_distinct_threshold
            ).only_enforce_if(bad_day_distinct.Not())
            bad_day_distinct_bools.append(bad_day_distinct)

    # --- Wrong component order (lec → tut → lab) ---
    # For each ordered tag pair (earlier_tag -> later_tag) we penalize
    # every meeting pair (m_e, m_l) where:
    #   * both meetings belong to the same course,
    #   * their audiences share at least one group,
    #   * the metric's `relates_to` filter allows the component pairing.
    # The violation bool is 1 iff the "earlier" meeting actually starts
    # strictly AFTER the "later" one (tag order reversed in time). Ties
    # on absolute start don't count, matching the metric semantics.
    def _pair_allowed(early_meeting: Meeting, late_meeting: Meeting) -> bool:
        early_rel = early_meeting.component_relates_to
        late_rel = late_meeting.component_relates_to
        if early_rel is None and late_rel is None:
            return True
        early_set = set(early_rel) if early_rel else set()
        late_set = set(late_rel) if late_rel else set()
        return late_meeting.class_idx in early_set or early_meeting.class_idx in late_set

    order_violation_bools: list[cp_model.IntVar] = []
    meetings_by_course_tag: dict[tuple[int, str], list[int]] = defaultdict(list)
    for m_idx, m in enumerate(meetings):
        meetings_by_course_tag[(m.course_idx, str(m.tag).lower())].append(m_idx)

    wrong_order_tag_pairs = (("lec", "tut"), ("lec", "lab"), ("tut", "lab"))
    seen_courses = {m.course_idx for m in meetings}
    for course_idx in seen_courses:
        for early_tag, late_tag in wrong_order_tag_pairs:
            early_ms = meetings_by_course_tag.get((course_idx, early_tag), [])
            late_ms = meetings_by_course_tag.get((course_idx, late_tag), [])
            for e_idx in early_ms:
                e_groups = set(meetings[e_idx].groups)
                for l_idx in late_ms:
                    l_meeting = meetings[l_idx]
                    if not (e_groups & set(l_meeting.groups)):
                        continue
                    if not _pair_allowed(meetings[e_idx], l_meeting):
                        continue
                    viol = model.new_bool_var(f"order_viol_{e_idx}_{l_idx}")
                    model.add(
                        absolute_start_vars[l_idx] >= absolute_start_vars[e_idx]
                    ).only_enforce_if(viol.Not())
                    order_violation_bools.append(viol)

    # --- Weekend events (Sat/Sun) ---
    # A meeting contributes at most once per weekend day (it lives on a
    # single day), so summing the per-(meeting, weekend-day) indicators
    # yields exactly the total weekend event count.
    weekend_event_bools: list[cp_model.IntVar] = []
    if weekend_day_indices:
        for m_idx in range(len(meetings)):
            for d in weekend_day_indices:
                weekend_event_bools.append(_meeting_on_day(m_idx, d))

    # --- Late events (end > 18:00) ---
    # For each meeting we reify a bool for every late slot index and let
    # the solver pick at most one (since local_start is a single value).
    # The sum over all meetings equals the late-event count used by the
    # metric, and the denominator is just the number of meetings.
    late_event_bools: list[cp_model.IntVar] = []
    if late_slot_indices:
        for m_idx in range(len(meetings)):
            for s in late_slot_indices:
                slot_eq = model.new_bool_var(f"slot_eq_{m_idx}_{s}")
                model.add(local_start_vars[m_idx] == s).only_enforce_if(slot_eq)
                model.add(local_start_vars[m_idx] != s).only_enforce_if(slot_eq.Not())
                late_event_bools.append(slot_eq)

    # --- Per-group excess active days ---
    # weekly_events_g and min_active_g are fixed by config, so we can
    # pre-compute min_active and use a tight equality-style bound:
    #   excess_g + min_active_g >= sum_d active_g_d,  excess_g >= 0.
    # Denominator = sum_g (num_days - min_active_g): the maximum total
    # excess reachable across all groups.
    group_to_meetings: dict[str, list[int]] = defaultdict(list)
    for m_idx, m in enumerate(meetings):
        for g in m.groups:
            group_to_meetings[g].append(m_idx)

    group_excess_vars: list[cp_model.IntVar] = []
    group_excess_opportunities = 0
    for g, m_idxs in group_to_meetings.items():
        weekly = len(m_idxs)
        if weekly <= 0:
            continue
        min_active = max(1, math.ceil(weekly / 5))
        max_excess = num_days - min_active
        if max_excess <= 0:
            continue
        active_day_bools = []
        for d in range(num_days):
            any_on_d = model.new_bool_var(f"grp_{g}_on_{d}")
            model.add_max_equality(any_on_d, [_meeting_on_day(mi, d) for mi in m_idxs])
            active_day_bools.append(any_on_d)
        excess = model.new_int_var(0, max_excess, f"grp_{g}_excess")
        model.add(excess + min_active >= sum(active_day_bools))
        group_excess_vars.append(excess)
        group_excess_opportunities += max_excess

    # --- Per-instructor excess active days ---
    # The instructor assigned to a meeting is a decision variable, so we
    # reify "inst i teaches meeting m" from the option bools. For each
    # (i, d) pair we OR the per-meeting teach bools gated by the meeting
    # being on day d. weekly_i is also variable; we use the integer
    # linear relaxation
    #     5 * excess_i >= 5 * active_days_i - weekly_i - 4
    # which is equivalent to excess_i >= max(0, active_days_i - ceil(w/5))
    # for integer variables (exact lower bound the solver drives to).
    inst_meeting_lits: dict[str, dict[int, list[cp_model.IntVar]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for m_idx, m in enumerate(meetings):
        opts = m.instructor_options
        if len(opts) == 1:
            # Single option: every listed instructor always teaches it.
            for inst in opts[0]:
                inst_meeting_lits[inst][m_idx] = []  # sentinel: unconditional
        else:
            opt_bools = meeting_option_bools[m_idx]
            for opt_idx, insts in enumerate(opts):
                for inst in insts:
                    inst_meeting_lits[inst][m_idx].append(opt_bools[opt_idx])

    inst_excess_vars: list[cp_model.IntVar] = []
    inst_excess_opportunities = 0
    for inst, per_meeting in inst_meeting_lits.items():
        if not per_meeting:
            continue
        teaches_m: dict[int, cp_model.IntVar | int] = {}
        for m_idx, lits in per_meeting.items():
            if not lits:
                teaches_m[m_idx] = 1  # unconditional
            elif len(lits) == 1:
                teaches_m[m_idx] = lits[0]
            else:
                b = model.new_bool_var(f"inst_{inst}_teaches_{m_idx}")
                model.add_max_equality(b, lits)
                teaches_m[m_idx] = b
        weekly_terms = [v for v in teaches_m.values()]
        # Linear channeling of "instructor active on day d" avoids O(meetings*days)
        # bool_and/bool_or reifications. `any_on_d` is a bool; LP minimization
        # (excess has negative weight) drives it to max(0, max_m(tb+md-1)).
        active_day_bools = []
        for d in range(num_days):
            any_on_d = model.new_bool_var(f"inst_{inst}_any_{d}")
            for m_idx, tb in teaches_m.items():
                md_bool = _meeting_on_day(m_idx, d)
                if isinstance(tb, int):
                    model.add(any_on_d >= md_bool)
                else:
                    model.add(any_on_d >= tb + md_bool - 1)
            active_day_bools.append(any_on_d)
        excess = model.new_int_var(0, num_days, f"inst_{inst}_excess")
        model.add(
            5 * excess >= 5 * sum(active_day_bools) - sum(weekly_terms) - 4
        )
        inst_excess_vars.append(excess)
        inst_excess_opportunities += num_days - 1

    # --- Combine all scaled objective terms ---
    weight_same_day = 1
    weight_oversize_room = 1
    weight_bad_day = 1
    weight_bad_day_distinct = 1
    weight_wrong_order = 1
    weight_weekend = 1
    weight_late = 1
    weight_group_excess = 1
    weight_inst_excess = 1

    scaled_terms: list[tuple[int, int, list[cp_model.IntVar]]] = []
    if satisfied_bools:
        scaled_terms.append((weight_same_day, len(satisfied_bools), satisfied_bools))
    if oversize_bools and oversize_opportunities > 0:
        scaled_terms.append((-weight_oversize_room, oversize_opportunities, oversize_bools))
    if bad_day_bools and bad_day_opportunities > 0:
        scaled_terms.append((-weight_bad_day, bad_day_opportunities, bad_day_bools))
    if bad_day_distinct_bools and bad_day_distinct_opportunities > 0:
        scaled_terms.append(
            (-weight_bad_day_distinct, bad_day_distinct_opportunities, bad_day_distinct_bools)
        )
    if order_violation_bools:
        scaled_terms.append((-weight_wrong_order, len(order_violation_bools), order_violation_bools))
    # Denominators reflect the upper bound on how many events could land
    # on the penalized slots: (#rooms) x (#days in category) x (#slots in
    # category). This is config-fixed and matches the semantics of the
    # weekend/late count metrics (one event per room per timeslot).
    num_rooms = len(room_ids)
    if weekend_event_bools:
        weekend_slot_capacity = len(weekend_day_indices) * slots_per_day * num_rooms
        if weekend_slot_capacity > 0:
            scaled_terms.append((-weight_weekend, weekend_slot_capacity, weekend_event_bools))
    if late_event_bools:
        late_slot_capacity = len(late_slot_indices) * num_days * num_rooms
        if late_slot_capacity > 0:
            scaled_terms.append((-weight_late, late_slot_capacity, late_event_bools))
    if group_excess_vars and group_excess_opportunities > 0:
        scaled_terms.append((-weight_group_excess, group_excess_opportunities, group_excess_vars))
    if inst_excess_vars and inst_excess_opportunities > 0:
        scaled_terms.append((-weight_inst_excess, inst_excess_opportunities, inst_excess_vars))

    if scaled_terms:
        # Fixed-K rational scaling instead of LCM. Every term contributes
        # at most |w| * K in magnitude (K = 10_000), which keeps
        # coefficients bounded regardless of how many objectives we
        # stack on. Rounding error per term is <= 1/K, well below any
        # meaningful weight difference.
        K = 10_000
        obj_terms: list[cp_model.LinearExpr | int] = []
        for w, denom, vars_ in scaled_terms:
            if denom <= 0:
                continue
            coef = int(round(w * K / denom))
            if coef == 0:
                continue
            obj_terms.append(coef * sum(vars_))
        if obj_terms:
            model.maximize(sum(obj_terms))

    return (
        PreparedModel(
            model=model,
            day_vars=day_vars,
            local_start_vars=local_start_vars,
            absolute_start_vars=absolute_start_vars,
            absolute_end_vars=absolute_end_vars,
            room_vars=room_vars,
            inst_choice_vars=inst_choice_vars,
            day_bool_by_meeting=day_bool_by_meeting,
        ),
        None,
    )


def apply_warm_start_hints(
    prepared: "PreparedModel",
    meetings: list[Meeting],
    hint_yaml_path: Path,
    cfg: ScheduleConfig,
    days: list[str],
    room_ids: list[str],
) -> int:
    """Populate `model.add_hint` from a previously produced output.yaml.

    Returns the number of meetings that received hints.
    """
    data = yaml.safe_load(hint_yaml_path.read_text(encoding="utf-8"))
    schedule = data.get("schedule") if isinstance(data, dict) else None
    if not schedule:
        return 0

    day_to_idx = {d: i for i, d in enumerate(days)}
    room_to_idx = {r: i for i, r in enumerate(room_ids)}
    slot_to_idx: dict[str, int] = {}
    for i, t in enumerate(cfg.term.time_slots):
        slot_to_idx[t.strftime("%H:%M:%S")] = i

    def normalize_time(raw: object) -> str | None:
        if isinstance(raw, datetime.time):
            return raw.strftime("%H:%M:%S")
        if isinstance(raw, str):
            s = raw.strip().strip("'").strip('"')
            if len(s) == 5:
                s = s + ":00"
            return s
        return None

    def _is_constant(var: cp_model.IntVar) -> bool:
        dom = var.proto.domain
        return len(dom) == 2 and dom[0] == dom[1]

    bucket: dict[tuple[str, str, tuple[str, ...]], list[int]] = defaultdict(list)
    for m_idx, m in enumerate(meetings):
        key = (m.course, str(m.tag), tuple(m.groups))
        bucket[key].append(m_idx)
    for key_ in bucket:
        bucket[key_].sort(key=lambda i: meetings[i].week_index)

    hints = 0
    hinted: set[int] = set()
    for course_data in schedule.get("courses", []):
        course_name = course_data.get("name")
        for component_data in course_data.get("components", []):
            tag = component_data.get("tag")
            for session in component_data.get("sessions", []):
                audience = tuple(session.get("audience", []))
                m_idxs = bucket.get((course_name, tag, audience), [])
                if not m_idxs:
                    continue
                days_list = session.get("days", [])
                start_times = session.get("start_times", [])
                rooms = session.get("rooms", [])
                instructors_list = session.get("instructors", [])
                n = min(len(m_idxs), len(days_list), len(start_times), len(rooms))
                for i in range(n):
                    m_idx = m_idxs[i]
                    if m_idx in hinted:
                        continue
                    d_name = days_list[i]
                    t_norm = normalize_time(start_times[i])
                    r_id = str(rooms[i])
                    if d_name not in day_to_idx or t_norm is None or t_norm not in slot_to_idx:
                        continue
                    if r_id not in room_to_idx:
                        continue
                    hinted.add(m_idx)
                    d_idx = day_to_idx[d_name]
                    prepared.model.add_hint(prepared.day_vars[m_idx], d_idx)
                    for dd, b in enumerate(prepared.day_bool_by_meeting[m_idx]):
                        prepared.model.add_hint(b, 1 if dd == d_idx else 0)
                    prepared.model.add_hint(prepared.local_start_vars[m_idx], slot_to_idx[t_norm])
                    if not _is_constant(prepared.room_vars[m_idx]):
                        prepared.model.add_hint(prepared.room_vars[m_idx], room_to_idx[r_id])
                    chosen = set(instructors_list[i]) if i < len(instructors_list) else set()
                    m = meetings[m_idx]
                    if not _is_constant(prepared.inst_choice_vars[m_idx]):
                        for opt_idx, opts in enumerate(m.instructor_options):
                            if set(opts) == chosen:
                                prepared.model.add_hint(prepared.inst_choice_vars[m_idx], opt_idx)
                                break
                    hints += 1
    return hints


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
    solver.parameters.linearization_level = 1
 
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
    hint_path: Path | None = None,
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
        is_core_course = "core_course" in {str(t).lower() for t in course.course_tags}
        for cls_idx, cls in enumerate(course.components):
            relates_to_value = cls.relates_to
            if relates_to_value is None:
                relates_to_tuple: tuple[int, ...] | None = None
            elif isinstance(relates_to_value, list):
                relates_to_tuple = tuple(int(v) for v in relates_to_value)
            else:
                relates_to_tuple = (int(relates_to_value),)
            groups = expand_groups(cls.student_groups, selector_map)
            if not groups:
                continue

            audiences = [[g] for g in groups] if cls.per_group else [groups]

            count = cls.per_week

            instructor_options: list[list[str]] = []
            seen_options: set[tuple[str, ...]] = set()
            if cls.instructor_pool:
                for p in cls.instructor_pool:
                    opt = p if isinstance(p, list) else [p]
                    key = tuple(sorted(opt))
                    if key in seen_options:
                        continue
                    seen_options.add(key)
                    instructor_options.append(opt)
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
                            is_core_course=is_core_course,
                            component_relates_to=relates_to_tuple,
                        )
                    )

    if not meetings:
        return _empty_result("no meetings to schedule", slots_count=num_days * slots_per_day)

    weekend_day_indices = frozenset(
        i for i, d in enumerate(days) if d in ("Sat", "Sun")
    )
    late_cutoff = datetime.time(18, 0)
    late_slot_indices = frozenset(
        i
        for i, slot in enumerate(cfg.term.time_slots)
        if (datetime.datetime.combine(datetime.date.min, slot) + datetime.timedelta(minutes=90)).time()
        > late_cutoff
    )

    prepared, prepare_error = prepare_model(
        meetings,
        num_days=num_days,
        slots_per_day=slots_per_day,
        room_ids=room_ids,
        room_capacities=room_capacities,
        group_students_map=group_students_map,
        shared_students=shared_students,
        show_progress=show_progress,
        weekend_day_indices=weekend_day_indices,
        late_slot_indices=late_slot_indices,
    )
    if prepared is None:
        return _empty_result(
            prepare_error or "model preparation failed",
            meetings_count=len(meetings),
            slots_count=num_days * slots_per_day,
        )

    if hint_path is not None and hint_path.is_file():
        hints_applied = apply_warm_start_hints(
            prepared, meetings, hint_path, cfg, days, room_ids
        )
        if show_progress:
            print(f"Applied {hints_applied}/{len(meetings)} warm-start hints")

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
    parser.add_argument(
        "--hint",
        type=Path,
        default=None,
        help="Path to a previous output.yaml to use as a warm-start hint.",
    )

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
        hint_path=args.hint,
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
