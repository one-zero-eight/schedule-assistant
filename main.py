from __future__ import annotations

import argparse
import datetime
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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


def save_output(artifacts_dir: Path | None, result: SolveResult) -> None:
    if artifacts_dir is None:
        return
    (artifacts_dir / "output.yaml").write_text(
        yaml.safe_dump(result.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
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


class CourseSchedule(SettingBaseModel):
    """One course and its scheduled components."""

    class ComponentOutput(SettingBaseModel):
        """Config-level component (lec/tut/lab) with placed sessions."""

        class SessionSeries(SettingBaseModel):
            """Parallel lists for one audience (group set) across sessions."""

            audience: list[str]
            instructors: list[list[str]]
            dates: list[datetime.date]
            start_times: list[datetime.time]
            rooms: list[str]

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
    meetings: int
    slots: int
    error: str | None = None
    slots_per_day: int | None = None
    teaching_days: int | None = None


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
    schedule: Schedule
    stats: SolveStats
    artifacts_dir: Path | None = None
    "Directory containing solver artifacts (e.g. output.yaml, solver_log_phase_*.txt); None when no artifact directory is used."


def teaching_dates(cfg: ScheduleConfig) -> list[datetime.date]:
    allowed_days = {DAY_TO_WEEKDAY[d] for d in cfg.term.days}
    out: list[datetime.date] = []
    cur = cfg.term.semester.start_date
    end = cfg.term.semester.end_date
    while cur <= end:
        if cur.weekday() in allowed_days:
            out.append(cur)
        cur += datetime.timedelta(days=1)
    return out


def solve_schedule(
    cfg: ScheduleConfig,
    time_limit: int,
    *,
    show_progress: bool,
    artifacts_dir: Path | None = None,
) -> SolveResult:
    # Precompute static scheduling inputs from config.
    selector_map = resolve_selector_map(cfg)
    dates = teaching_dates(cfg)
    num_dates = len(dates)
    if num_dates == 0:
        result = SolveResult(
            status="EMPTY",
            schedule=Schedule(),
            stats=SolveStats(meetings=0, slots=0, error="no teaching days in term range"),
            artifacts_dir=artifacts_dir,
        )
        save_output(artifacts_dir, result)
        return result

    slots_per_day = len(cfg.term.time_slots)
    room_ids = [r.id for r in cfg.rooms]
    if not room_ids:
        result = SolveResult(
            status="EMPTY",
            schedule=Schedule(),
            stats=SolveStats(meetings=0, slots=0, error="no rooms in config"),
            artifacts_dir=artifacts_dir,
        )
        save_output(artifacts_dir, result)
        return result

    delta = cfg.term.semester.end_date - cfg.term.semester.start_date
    num_weeks = max(1, delta.days // 7)
    room_capacities = [r.capacity for r in cfg.rooms]
    group_size_map: dict[str, int] = {}
    group_students_map: dict[str, set[str]] = defaultdict(set)
    student_groups_membership: dict[str, set[str]] = defaultdict(set)
    for bucket in (cfg.student_groups.academic, cfg.student_groups.english, cfg.student_groups.elective):
        for group in bucket:
            group_size_map[group.id] = max(0, int(group.estimated_size or 0))
            for student_email in group.students:
                student = student_email.strip().lower()
                if not student:
                    continue
                group_students_map[group.id].add(student)
                student_groups_membership[student].add(group.id)
    shared_students = {student for student, groups in student_groups_membership.items() if len(groups) > 1}

    # Expand course components into concrete meeting instances (one per week/audience).
    meetings: list[Meeting] = []
    for c_idx, course in enumerate(tqdm(cfg.courses, desc="Building meetings", disable=not show_progress)):
        for cls_idx, cls in enumerate(course.components):
            groups = expand_groups(cls.student_groups, selector_map)
            if not groups:
                continue

            audiences = [[g] for g in groups] if cls.per_group else [groups]

            count = cls.per_week * num_weeks

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
        result = SolveResult(
            status="EMPTY",
            schedule=Schedule(),
            stats=SolveStats(meetings=0, slots=num_dates * slots_per_day),
            artifacts_dir=artifacts_dir,
        )
        save_output(artifacts_dir, result)
        return result

    # CP-SAT model: each meeting gets day/time/room/instructor decision variables.
    model = cp_model.CpModel()

    day_vars: list[cp_model.IntVar] = []
    local_start_vars: list[cp_model.IntVar] = []
    absolute_start_vars: list[cp_model.IntVar] = []
    absolute_end_vars: list[cp_model.IntVar] = []
    room_vars: list[cp_model.IntVar] = []
    room_pick_vars: list[list[cp_model.BoolVarT]] = []
    inst_choice_vars: list[cp_model.IntVar] = []
    weekday_vars: list[cp_model.IntVar] = []
    group_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    student_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    room_intervals: dict[int, list[cp_model.IntervalVar]] = defaultdict(list)
    inst_to_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)

    max_abs = num_dates * slots_per_day
    weekday_markers = [d.weekday() for d in dates]

    for i, m in enumerate(meetings):
        # Time placement vars + absolute-slot interval for overlap constraints.
        dur = m.duration
        day_v = model.new_int_var(0, num_dates - 1, f"day_{i}")
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
        weekday_v = model.new_int_var(0, 6, f"weekday_{i}")
        model.add_element(day_v, weekday_markers, weekday_v)
        weekday_vars.append(weekday_v)

        core = model.new_interval_var(abs_s, dur, abs_e, f"group_core_{i}")
        for g in m.groups:
            group_intervals[g].append(core)
        if shared_students:
            meeting_students: set[str] = set()
            for g in m.groups:
                meeting_students.update(group_students_map.get(g, set()))
            for student in meeting_students:
                if student in shared_students:
                    student_intervals[student].append(core)

        room_v = model.new_int_var(0, len(room_ids) - 1, f"room_{i}")
        room_vars.append(room_v)

        n_opts = len(m.instructor_options)
        if n_opts > 1:
            inst_choice = model.new_int_var(0, n_opts - 1, f"inst_choice_{i}")
        else:
            inst_choice = model.new_constant(0)
        inst_choice_vars.append(inst_choice)

    # Hard constraint: a student group cannot attend overlapping meetings.
    for ivals in tqdm(group_intervals.values(), desc="Group constraints", disable=not show_progress):
        model.add_no_overlap(ivals)
    # Hard constraint: a single student present in multiple groups (e.g. academic + English)
    # cannot have overlapping meetings across those groups.
    for ivals in tqdm(student_intervals.values(), desc="Student constraints", disable=not show_progress):
        if len(ivals) > 1:
            model.add_no_overlap(ivals)

    for i, m in enumerate(meetings):
        # Room assignment: exactly one room per meeting, represented as optional intervals.
        dur = m.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        room_bools: list[cp_model.BoolVarT] = []
        for r in range(len(room_ids)):
            b = model.new_bool_var(f"room_pick_{i}_{r}")
            room_bools.append(b)
            model.add(room_vars[i] == r).OnlyEnforceIf(b)
            model.add(room_vars[i] != r).OnlyEnforceIf(b.Not())
            room_intervals[r].append(model.new_optional_interval_var(abs_s, dur, abs_e, b, f"room_iv_{i}_{r}"))
        model.add_exactly_one(room_bools)
        room_pick_vars.append(room_bools)

    # Hard constraint: a room cannot host more than one meeting at the same time.
    for r in tqdm(range(len(room_ids)), desc="Room constraints", disable=not show_progress):
        if room_intervals[r]:
            model.add_no_overlap(room_intervals[r])

    # Hard constraint: an instructor (or co-teaching set) cannot overlap in time.
    for i, m in enumerate(tqdm(meetings, desc="Instructor constraints", disable=not show_progress)):
        dur = m.duration
        abs_s, abs_e = absolute_start_vars[i], absolute_end_vars[i]
        n_opts = len(m.instructor_options)
        if n_opts == 1:
            for inst in m.instructor_options[0]:
                inst_to_intervals[inst].append(model.new_interval_var(abs_s, dur, abs_e, f"inst_iv_{inst}_{i}"))
            continue
        opts_b = [model.new_bool_var(f"inst_opt_{i}_{k}") for k in range(n_opts)]
        model.add_exactly_one(opts_b)
        for opt_idx, b in enumerate(opts_b):
            model.add(inst_choice_vars[i] == opt_idx).OnlyEnforceIf(b)
        for opt_idx, insts in enumerate(m.instructor_options):
            for inst in insts:
                inst_to_intervals[inst].append(
                    model.new_optional_interval_var(abs_s, dur, abs_e, opts_b[opt_idx], f"inst_iv_{inst}_{i}_{opt_idx}")
                )

    for inst, ivals in inst_to_intervals.items():
        if ivals:
            model.add_no_overlap(ivals)

    # Tiered soft-objective design:
    # Tier 1: pedagogical coherence
    # Tier 2: room suitability + moderate pedagogy
    # Tier 3: calendar comfort + distribution quality
    tier1_terms: list[cp_model.LinearExprT] = []
    tier2_terms: list[cp_model.LinearExprT] = []
    tier3_terms: list[cp_model.LinearExprT] = []

    def _meeting_impact(groups: list[str]) -> int:
        # Events affecting more groups should carry proportionally more objective impact.
        return max(1, len(groups))

    # Objective weights (within room-family buckets).
    FALLBACK_ATTENDANCE_RATIO = 0.9
    """When no room fits full enrollment, require capacity for ceil(90%) attendance."""
    ROOM_OVERSIZE_BUCKET_PENALTIES = (
        (30, 0),     # <=30% oversize is effectively free
        (70, 120),   # <=70% oversize
        (100, 300),  # >70% oversize
    )
    room_oversize_coeffs: dict[tuple[int, int], int] = {}
    required_capacity_by_meeting: dict[int, int] = {}

    # Room sizing constraints/objective terms:
    # - room under-capacity is hard-forbidden
    # - if enrollment is >100, use ceil(90%) required attendance for capping
    # - otherwise, if no room can fit full enrollment, fallback to ceil(90%) required attendance
    # - if a chosen room is much larger than required capacity, add a soft objective penalty
    for i, meeting in enumerate(meetings):
        students = meeting.expected_students
        if students <= 0:
            continue

        feasible_for_full = any(capacity >= students for capacity in room_capacities)
        required_capacity = students
        if students > 100 or not feasible_for_full:
            required_capacity = math.ceil(students * FALLBACK_ATTENDANCE_RATIO)
        required_capacity_by_meeting[i] = max(1, required_capacity)

        for room_idx, capacity in enumerate(room_capacities):
            if capacity < required_capacity_by_meeting[i]:
                continue

            oversize_penalty = 0
            if capacity > required_capacity_by_meeting[i]:
                oversize_pct = ((capacity - required_capacity_by_meeting[i]) * 100) // max(1, required_capacity_by_meeting[i])
                for threshold, bucket_penalty in ROOM_OVERSIZE_BUCKET_PENALTIES:
                    if oversize_pct <= threshold:
                        oversize_penalty += bucket_penalty
                        break

            impact = _meeting_impact(meeting.groups)
            weighted_oversize_penalty = oversize_penalty * impact

            room_oversize_coeffs[(i, room_idx)] = weighted_oversize_penalty

    for i, meeting in enumerate(meetings):
        students = meeting.expected_students
        if students <= 0:
            continue
        for room_idx, capacity in enumerate(room_capacities):
            selected_room = room_pick_vars[i][room_idx]

            if capacity < required_capacity_by_meeting.get(i, students):
                model.add(room_vars[i] != room_idx)
                continue

            oversize_coeff = room_oversize_coeffs.get((i, room_idx), 0)
            if oversize_coeff > 0:
                tier2_terms.append(selected_room * oversize_coeff)

    # Soft relation between related components of the same audience/week.
    ORDER_VIOLATION_WEIGHT = 500
    CROSS_DAY_VIOLATION_WEIGHT = 200
    CROSS_DAY_LAB_VIOLATION_WEIGHT = 40
    BACK_TO_BACK_MISS_WEIGHT = 600
    ORDERED_TAG_TRANSITIONS = {("lec", "tut"), ("tut", "lab"), ("lec", "lab")}
    for c_idx, course in enumerate(cfg.courses):
        ncomp = len(course.components)
        explicit_relations: set[tuple[int, int]] = set()
        for dst_idx, comp in enumerate(course.components):
            raw_rel = comp.relates_to
            if raw_rel is None:
                continue
            rel_indices = raw_rel if isinstance(raw_rel, list) else [raw_rel]
            for src_idx in rel_indices:
                if 0 <= src_idx < ncomp and src_idx != dst_idx:
                    explicit_relations.add((src_idx, dst_idx))

        if explicit_relations:
            # Keep explicit relates_to wiring, but still add sensible fallbacks for components
            # without relates_to so mixed topologies (e.g. lec1/tut1 + lec2/tut2 + lab) are constrained.
            component_relations_set: set[tuple[int, int]] = set(explicit_relations)
            for dst_idx, comp in enumerate(course.components):
                if comp.relates_to is not None:
                    continue
                dst_tag = str(comp.tag).lower()
                if dst_tag == "lab":
                    for src_idx in range(dst_idx):
                        src_tag = str(course.components[src_idx].tag).lower()
                        if src_tag in {"lec", "tut", "class"}:
                            component_relations_set.add((src_idx, dst_idx))
                elif dst_idx > 0:
                    component_relations_set.add((dst_idx - 1, dst_idx))
            component_relations = sorted(component_relations_set)
        else:
            component_relations = [(cls_idx, cls_idx + 1) for cls_idx in range(ncomp - 1)]

        # Group meetings by (component, teaching-week bucket). Order checks are then
        # applied only when source/destination meetings share at least one group.
        meetings_by_component_week: dict[tuple[int, int], list[int]] = defaultdict(list)
        teaching_weeks: set[int] = set()
        for i, m in enumerate(meetings):
            if m.course_idx != c_idx:
                continue
            comp = course.components[m.class_idx]
            per_week = max(1, int(comp.per_week))
            teaching_week = m.week_index // per_week
            meetings_by_component_week[(m.class_idx, teaching_week)].append(i)
            teaching_weeks.add(teaching_week)

        for teaching_week in teaching_weeks:
            for src_idx, dst_idx in component_relations:
                src_meetings = meetings_by_component_week.get((src_idx, teaching_week), [])
                dst_meetings = meetings_by_component_week.get((dst_idx, teaching_week), [])
                if not src_meetings or not dst_meetings:
                    continue

                tag_a = str(course.components[src_idx].tag).lower()
                tag_b = str(course.components[dst_idx].tag).lower()
                for src_pos, ia in enumerate(src_meetings):
                    for dst_pos, ib in enumerate(dst_meetings):
                        shared_groups = set(meetings[ia].groups) & set(meetings[ib].groups)
                        if not shared_groups:
                            continue
                        relation_impact = max(1, len(shared_groups))
                        pair_suffix = (
                            f"{c_idx}_{src_idx}_{dst_idx}_{teaching_week}_{src_pos}_{dst_pos}"
                        )
                        if (tag_a, tag_b) in ORDERED_TAG_TRANSITIONS:
                            order_violated = model.new_bool_var(f"order_violated_{pair_suffix}")
                            model.add(absolute_start_vars[ia] >= absolute_start_vars[ib]).OnlyEnforceIf(order_violated)
                            model.add(absolute_start_vars[ia] < absolute_start_vars[ib]).OnlyEnforceIf(order_violated.Not())
                            tier1_terms.append(order_violated * ORDER_VIOLATION_WEIGHT * relation_impact)

                        same_audience = meetings[ia].audience_key == meetings[ib].audience_key
                        if same_audience:
                            cross_day_violated = model.new_bool_var(f"cross_day_violated_{pair_suffix}")
                            model.add(day_vars[ia] != day_vars[ib]).OnlyEnforceIf(cross_day_violated)
                            model.add(day_vars[ia] == day_vars[ib]).OnlyEnforceIf(cross_day_violated.Not())
                            cross_day_weight = (
                                CROSS_DAY_LAB_VIOLATION_WEIGHT
                                if ("lab" in tag_a or "lab" in tag_b)
                                else CROSS_DAY_VIOLATION_WEIGHT
                            )
                            tier2_terms.append(cross_day_violated * cross_day_weight * relation_impact)

                        # Additional soft preference for lec -> tut: tutorial starts right after lecture ends.
                        if tag_a == "lec" and tag_b == "tut" and same_audience:
                            time_not_back_to_back = model.new_bool_var(f"time_not_back_to_back_{pair_suffix}")
                            room_changed = model.new_bool_var(f"room_changed_{pair_suffix}")
                            back_to_back_missed = model.new_bool_var(f"back_to_back_missed_{pair_suffix}")
                            model.add(absolute_end_vars[ia] != absolute_start_vars[ib]).OnlyEnforceIf(time_not_back_to_back)
                            model.add(absolute_end_vars[ia] == absolute_start_vars[ib]).OnlyEnforceIf(
                                time_not_back_to_back.Not()
                            )
                            model.add(room_vars[ia] != room_vars[ib]).OnlyEnforceIf(room_changed)
                            model.add(room_vars[ia] == room_vars[ib]).OnlyEnforceIf(room_changed.Not())
                            model.add(back_to_back_missed >= time_not_back_to_back)
                            model.add(back_to_back_missed >= room_changed)
                            model.add(back_to_back_missed <= time_not_back_to_back + room_changed)
                            tier1_terms.append(back_to_back_missed * BACK_TO_BACK_MISS_WEIGHT * relation_impact)

    # Tier 3 mild preferences: Saturday classes and later starts.
    SATURDAY_EVENT_WEIGHT = 90
    saturday_markers = [1 if d.weekday() == 5 else 0 for d in dates]
    if SATURDAY_EVENT_WEIGHT > 0 and any(saturday_markers):
        for i, m in enumerate(meetings):
            meeting_on_saturday = model.new_int_var(0, 1, f"on_saturday_{i}")
            model.add_element(day_vars[i], saturday_markers, meeting_on_saturday)
            impact = _meeting_impact(m.groups)
            tier3_terms.append(meeting_on_saturday * SATURDAY_EVENT_WEIGHT * impact)

    LATE_START_THRESHOLD = datetime.time(18, 0)
    LATE_START_WEIGHT = 50
    if LATE_START_WEIGHT > 0:
        late_slot_markers = [1 if t >= LATE_START_THRESHOLD else 0 for t in cfg.term.time_slots]
        if any(late_slot_markers):
            for i in range(len(meetings)):
                starts_late = model.new_int_var(0, 1, f"starts_late_{i}")
                model.add_element(local_start_vars[i], late_slot_markers, starts_late)
                tier3_terms.append(starts_late * LATE_START_WEIGHT)

    # Mild soft bias: place ordered-teaching anchors (lec/tut) earlier in the day
    # using only local start slot (no weekday/date preference).
    LECTURE_EARLY_START_WEIGHT = 18
    TUTORIAL_EARLY_START_WEIGHT = 10
    for i, m in enumerate(meetings):
        tag = str(m.tag).lower()
        impact = _meeting_impact(m.groups)
        if tag == "lec":
            if LECTURE_EARLY_START_WEIGHT > 0:
                tier2_terms.append(local_start_vars[i] * LECTURE_EARLY_START_WEIGHT * impact)
        elif tag == "tut":
            if TUTORIAL_EARLY_START_WEIGHT > 0:
                tier2_terms.append(local_start_vars[i] * TUTORIAL_EARLY_START_WEIGHT * impact)

    meetings_by_group: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(meetings):
        for g in m.groups:
            meetings_by_group[g].append(i)

    # Tier 3 optional: student distribution quality.
    STUDENT_SPREAD_WEIGHT = 10
    if STUDENT_SPREAD_WEIGHT > 0:
        for group_id, group_meeting_indices in meetings_by_group.items():
            if len(group_meeting_indices) < 2 or num_dates < 2:
                continue
            group_day_vars = [day_vars[idx] for idx in group_meeting_indices]
            max_day = model.new_int_var(0, num_dates - 1, f"group_{group_id}_max_day")
            min_day = model.new_int_var(0, num_dates - 1, f"group_{group_id}_min_day")
            span = model.new_int_var(0, num_dates - 1, f"group_{group_id}_span")
            concentration = model.new_int_var(0, num_dates - 1, f"group_{group_id}_concentration")
            model.add_max_equality(max_day, group_day_vars)
            model.add_min_equality(min_day, group_day_vars)
            model.add(span == max_day - min_day)
            model.add(concentration == (num_dates - 1) - span)
            tier3_terms.append(concentration * STUDENT_SPREAD_WEIGHT)

    # Tier 3 optional: weekday balancing for student groups.
    # Focus on bachelor_1_en first (where overload was observed), fallback to all groups if selector missing.
    # This fights weekday clumping and discourages moving excessive load to Saturday.
    GROUP_WEEKDAY_BALANCE_WEIGHT = 24
    GROUP_WEEKDAY_OVERLOAD_WEIGHT = 22
    GROUP_SATURDAY_LOAD_WEIGHT = 10
    active_weekdays = sorted({DAY_TO_WEEKDAY[d] for d in cfg.term.days})
    weekday_occurrences = {wd: sum(1 for d in dates if d.weekday() == wd) for wd in active_weekdays}
    meeting_on_weekday: dict[tuple[int, int], cp_model.BoolVarT] = {}
    target_groups = resolve_selector_map(cfg).get("@bachelor_1_en", set())
    balanced_group_ids = (
        [g for g in meetings_by_group if g in target_groups]
        if target_groups
        else list(meetings_by_group.keys())
    )
    relevant_meeting_indices = sorted({idx for g in balanced_group_ids for idx in meetings_by_group[g]})
    if active_weekdays and balanced_group_ids and (
        GROUP_WEEKDAY_BALANCE_WEIGHT > 0
        or GROUP_WEEKDAY_OVERLOAD_WEIGHT > 0
        or GROUP_SATURDAY_LOAD_WEIGHT > 0
    ):
        for i in relevant_meeting_indices:
            for wd in active_weekdays:
                b = model.new_bool_var(f"m_{i}_on_wd_{wd}")
                model.add(weekday_vars[i] == wd).OnlyEnforceIf(b)
                model.add(weekday_vars[i] != wd).OnlyEnforceIf(b.Not())
                meeting_on_weekday[(i, wd)] = b

        for group_id in balanced_group_ids:
            group_meeting_indices = meetings_by_group[group_id]
            if len(group_meeting_indices) < 2:
                continue
            weekday_load_vars: list[cp_model.IntVar] = []
            for wd in active_weekdays:
                hits = [meeting_on_weekday[(idx, wd)] for idx in group_meeting_indices]
                load = model.new_int_var(0, len(group_meeting_indices), f"group_{group_id}_wd_{wd}_load")
                model.add(load == sum(hits))
                weekday_load_vars.append(load)

                if GROUP_WEEKDAY_OVERLOAD_WEIGHT > 0:
                    # Target: <=5 events/day for weekdays, <=4 events/day on Saturday.
                    per_day_target = 4 if wd == 5 else 5
                    max_reasonable = per_day_target * max(1, weekday_occurrences.get(wd, 0))
                    overload = model.new_int_var(0, len(group_meeting_indices), f"group_{group_id}_wd_{wd}_over")
                    model.add(overload >= load - max_reasonable)
                    tier3_terms.append(overload * GROUP_WEEKDAY_OVERLOAD_WEIGHT)
                if GROUP_SATURDAY_LOAD_WEIGHT > 0 and wd == 5:
                    tier3_terms.append(load * GROUP_SATURDAY_LOAD_WEIGHT)

            if GROUP_WEEKDAY_BALANCE_WEIGHT > 0 and len(weekday_load_vars) >= 2:
                max_wd_load = model.new_int_var(0, len(group_meeting_indices), f"group_{group_id}_wd_max")
                min_wd_load = model.new_int_var(0, len(group_meeting_indices), f"group_{group_id}_wd_min")
                wd_spread = model.new_int_var(0, len(group_meeting_indices), f"group_{group_id}_wd_spread")
                model.add_max_equality(max_wd_load, weekday_load_vars)
                model.add_min_equality(min_wd_load, weekday_load_vars)
                model.add(wd_spread == max_wd_load - min_wd_load)
                tier3_terms.append(wd_spread * GROUP_WEEKDAY_BALANCE_WEIGHT)

    tier1_expr = cp_model.LinearExpr.Sum(tier1_terms) if tier1_terms else 0
    tier2_expr = cp_model.LinearExpr.Sum(tier2_terms) if tier2_terms else 0
    tier3_expr = cp_model.LinearExpr.Sum(tier3_terms) if tier3_terms else 0

    objective_phases: list[tuple[str, cp_model.LinearExprT]] = []
    if tier1_terms:
        objective_phases.append(("tier1_pedagogical", tier1_expr))
    if tier2_terms:
        objective_phases.append(("tier2_room", tier2_expr))
    if tier3_terms:
        objective_phases.append(("tier3_calendar", tier3_expr))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = max(1, os.cpu_count() or 1)
    solver.parameters.log_search_progress = True
    solver.parameters.log_to_stdout = True

    raw_hint_int_vars: list[cp_model.IntVar] = []
    raw_hint_int_vars.extend(day_vars)
    raw_hint_int_vars.extend(local_start_vars)
    raw_hint_int_vars.extend(room_vars)
    raw_hint_int_vars.extend(inst_choice_vars)

    raw_hint_bool_vars: list[cp_model.BoolVarT] = []
    for room_bools in room_pick_vars:
        raw_hint_bool_vars.extend(room_bools)

    hint_int_vars: list[cp_model.IntVar] = []
    seen_hint_int_indices: set[int] = set()
    for v in raw_hint_int_vars:
        idx = v.Index()
        if idx in seen_hint_int_indices:
            continue
        seen_hint_int_indices.add(idx)
        hint_int_vars.append(v)

    hint_bool_vars: list[cp_model.BoolVarT] = []
    seen_hint_bool_indices: set[int] = set()
    for v in raw_hint_bool_vars:
        idx = v.Index()
        if idx in seen_hint_bool_indices:
            continue
        seen_hint_bool_indices.add(idx)
        hint_bool_vars.append(v)

    def _run_solver_once(active_log_paths: list[Path] | None = None):
        if active_log_paths:
            log_files = [open(path, "a", encoding="utf-8") for path in active_log_paths]

            def _log_callback(msg: str) -> None:
                for log_f in log_files:
                    log_f.write(msg)
                    log_f.write("\n")

            solver.log_callback = _log_callback
            try:
                return solver.Solve(model)
            finally:
                for log_f in log_files:
                    log_f.close()
                solver.log_callback = None
        return solver.Solve(model)

    log_dir: Path | None = artifacts_dir

    deadline = time.monotonic() + float(time_limit)
    status = cp_model.UNKNOWN
    best_feasible_status = None
    best_day_values: list[int] | None = None
    best_local_start_values: list[int] | None = None
    best_room_values: list[int] | None = None
    best_inst_choice_values: list[int] | None = None
    if not objective_phases:
        remaining = max(0.1, deadline - time.monotonic())
        solver.parameters.max_time_in_seconds = remaining
        if log_dir is not None:
            phase_log_path = log_dir / "solver_log_phase_1.txt"
            phase_log_path.write_text("", encoding="utf-8")
            status = _run_solver_once([phase_log_path])
        else:
            status = _run_solver_once()
    else:
        total_phases = len(objective_phases)
        if total_phases == 1:
            phase_shares = [1.0]
        elif total_phases == 2:
            phase_shares = [0.75, 0.25]
        else:
            tail_share = 0.15
            middle_share = 0.25
            first_share = 1.0 - middle_share - tail_share
            phase_shares = [first_share, middle_share]
            phase_shares.extend([tail_share / (total_phases - 2)] * (total_phases - 2))
        for phase_idx, (phase_name, phase_expr) in enumerate(objective_phases):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            phase_budget = float(time_limit) * phase_shares[phase_idx]
            solver.parameters.max_time_in_seconds = max(0.1, min(phase_budget, remaining))
            model.minimize(phase_expr)
            if log_dir is not None:
                phase_log_path = log_dir / f"solver_log_phase_{phase_idx + 1}.txt"
                phase_log_path.write_text("", encoding="utf-8")
                status = _run_solver_once([phase_log_path])
            else:
                status = _run_solver_once()
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break

            phase_value = int(round(solver.ObjectiveValue()))
            model.add(phase_expr == phase_value)
            best_feasible_status = status
            best_day_values = [solver.Value(v) for v in day_vars]
            best_local_start_values = [solver.Value(v) for v in local_start_vars]
            best_room_values = [solver.Value(v) for v in room_vars]
            best_inst_choice_values = [solver.Value(v) for v in inst_choice_vars]

            # Hint the found solution for next objective phase.
            if phase_idx < len(objective_phases) - 1:
                model.clear_hints()
                for v in hint_int_vars:
                    model.add_hint(v, solver.Value(v))
                for v in hint_bool_vars:
                    model.add_hint(v, bool(solver.Value(v)))

    stats = SolveStats(
        meetings=len(meetings),
        slots=num_dates * slots_per_day,
        slots_per_day=slots_per_day,
        teaching_days=num_dates,
    )
    schedule_empty = Schedule()

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if (
            best_feasible_status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            and best_day_values is not None
            and best_local_start_values is not None
            and best_room_values is not None
            and best_inst_choice_values is not None
        ):
            status = best_feasible_status
        else:
            if status == cp_model.INFEASIBLE:
                assumptions = solver.sufficient_assumptions_for_infeasibility()

                if assumptions:
                    print("Minimal unsatisfiable core:")
                    for var_index in assumptions:
                        print(f"{var_index}: '{model.proto.variables[var_index].name}'")

            result = SolveResult(
                status=solver.StatusName(status),  # pyright: ignore[reportArgumentType]
                schedule=schedule_empty,
                stats=stats,
                artifacts_dir=artifacts_dir,
            )
            save_output(artifacts_dir, result)
            return result

    def _value_or_snapshot(
        idx: int,
        vars_list: list[cp_model.IntVar],
        snapshot: list[int] | None,
    ) -> int:
        if snapshot is not None:
            return snapshot[idx]
        return solver.Value(vars_list[idx])

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

            instances_map: dict[tuple[str, ...], dict[str, list]] = defaultdict(
                lambda: {"dates": [], "start_times": [], "rooms": [], "instructors": []}
            )

            for m_idx in meeting_indices:
                m = meetings[m_idx]

                di = _value_or_snapshot(m_idx, day_vars, best_day_values)
                t_idx = _value_or_snapshot(m_idx, local_start_vars, best_local_start_values)
                r_val = _value_or_snapshot(m_idx, room_vars, best_room_values)
                inst_opt_idx = _value_or_snapshot(m_idx, inst_choice_vars, best_inst_choice_values)

                d = dates[di]
                slot_time = cfg.term.time_slots[t_idx]
                room_id = room_ids[r_val]
                chosen_insts = m.instructor_options[inst_opt_idx] if m.instructor_options else []

                g_key = tuple(m.groups)

                instances_map[g_key]["dates"].append(d)
                instances_map[g_key]["start_times"].append(slot_time)
                instances_map[g_key]["rooms"].append(room_id)
                instances_map[g_key]["instructors"].append(chosen_insts)

            sessions_output: list[CourseSchedule.ComponentOutput.SessionSeries] = []
            for g_key, data in instances_map.items():
                combined = list(zip(data["dates"], data["start_times"], data["rooms"], data["instructors"]))
                combined.sort(key=lambda x: x[0])

                sessions_output.append(
                    CourseSchedule.ComponentOutput.SessionSeries(
                        audience=list(g_key),
                        instructors=[x[3] for x in combined],
                        dates=[x[0] for x in combined],
                        start_times=[x[1] for x in combined],
                        rooms=[x[2] for x in combined],
                    )
                )

            class_output = CourseSchedule.ComponentOutput(
                tag=cls_cfg.tag,
                student_groups=cls_cfg.student_groups,
                instructor_pool=cls_cfg.instructor_pool,
                sessions=sessions_output,
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
    save_output(artifacts_dir, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--time-limit", type=int, default=60)
    parser.add_argument("--no-progress", action="store_true")

    args = parser.parse_args()

    cfg = ScheduleConfig.from_yaml(args.config)

    results_dir = _results_dir_for_run(cfg)
    result = solve_schedule(
        cfg,
        args.time_limit,
        show_progress=not args.no_progress,
        artifacts_dir=results_dir,
    )

    print(
        yaml.safe_dump(
            {"status": result.status, "stats": result.stats.model_dump(mode="json")},
            sort_keys=False,
            allow_unicode=True,
        ).rstrip()
    )
    schedule_path = results_dir / "output.yaml"
    print(f"Schedule written to {schedule_path.resolve()}", flush=True)
    if result.artifacts_dir is not None:
        phase_log_paths = sorted(result.artifacts_dir.glob("solver_log_phase_*.txt"))
        if phase_log_paths:
            print("Solver logs:", flush=True)
            for phase_log_path in phase_log_paths:
                print(f"- {phase_log_path}", flush=True)


if __name__ == "__main__":
    main()
