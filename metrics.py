from __future__ import annotations

import argparse
import datetime
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml
from main import Schedule, SolveResult, teaching_dates

from config import CourseConfig, ScheduleConfig, expand_groups, resolve_selector_map

# Matches current solver (single slot index per meeting); replace when model exposes per-class duration.
TIME_SLOT_DURATION = datetime.timedelta(minutes=90)
WEEKDAY_ORDER = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_RANK = {day: idx for idx, day in enumerate(WEEKDAY_ORDER)}
# Python weekday: Mon=0 .. Sun=6.
WEEKDAYS_MON_FRI = frozenset(range(5))
WEEKDAYS_MON_SAT = frozenset(range(6))


@dataclass(frozen=True, slots=True)
class FlatMeeting:
    course_idx: int
    component_idx: int
    course: str
    component_tag: str
    groups: tuple[str, ...]
    date: datetime.date
    start_time: datetime.time
    room: str
    instructors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduleMetrics:
    conflicts: tuple[str, ...]
    unsatisfied: tuple[str, ...]
    total_events: int
    total_timeslots_count: int
    back_to_back_lecture_tutorial_opportunities: int
    back_to_back_lecture_tutorial_scheduled: int
    same_day_lec_tut_lab_opportunities: int
    same_day_lec_tut_lab_satisfied: int
    labs_before_tutorial_count: int
    labs_before_lecture_count: int
    tutorials_before_lecture_count: int
    events_exceeding_room_capacity_count: int
    events_exceeding_room_capacity: tuple[RoomCapacityViolation, ...]
    events_with_room_much_larger_than_students_count: int
    events_with_room_much_larger_than_students: tuple[RoomOversizeViolation, ...]
    events: tuple[FlatMeeting, ...]
    labs_before_tutorial_events: tuple[TagOrderPair, ...]
    labs_before_lecture_events: tuple[TagOrderPair, ...]
    tutorials_before_lecture_events: tuple[TagOrderPair, ...]
    events_per_weekday: dict[str, int]
    per_group_weekday_load: dict[str, dict[str, int]]
    per_group_per_day_distinct_subjects: dict[str, dict[str, int]]
    distinct_subjects_per_group_day_sum: int
    max_distinct_subjects_any_group_day: int
    saturday_event_count: int
    per_group_saturday_events: dict[str, int]
    timeslot_histogram_by_weekday: dict[str, dict[str, int]]
    meeting_hours_weighted_by_group_count_per_weekday: dict[str, float]
    room_capacity_utilization_by_room: dict[str, float]
    room_capacity_utilization_overall: float
    room_time_utilization_by_room: dict[str, float]
    room_time_utilization_overall: float
    instructor_weekday_load: dict[str, dict[str, int]]
    events_per_course_component: dict[str, int]
    events_per_course_components_grouped: dict[str, dict[str, int]]
    room_swaps_instructor_consecutive_slots: int
    room_swaps_group_consecutive_slots: int
    room_swaps_same_instructor_and_audience_consecutive_slots: int
    room_swaps_same_course_and_audience_consecutive_slots: int

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    @property
    def all_satisfied(self) -> bool:
        return not self.unsatisfied


def _slot_end(start: datetime.time) -> datetime.time:
    return (datetime.datetime.combine(datetime.date.min, start) + TIME_SLOT_DURATION).time()


def _slot_index(start: datetime.time, time_slots: list[datetime.time]) -> int | None:
    for i, t in enumerate(time_slots):
        if t == start:
            return i
    return None


def _is_consecutive_time_slots(
    m1: FlatMeeting,
    m2: FlatMeeting,
    time_slots: list[datetime.time],
) -> bool:
    if m1.date != m2.date:
        return False
    i1 = _slot_index(m1.start_time, time_slots)
    i2 = _slot_index(m2.start_time, time_slots)
    if i1 is None or i2 is None:
        return False
    return i2 == i1 + 1


def _room_swaps_consecutive_slots_instructor(events: list[FlatMeeting], time_slots: list[datetime.time]) -> int:
    """Count transitions where an instructor is in room A, then in the next slot in room B (B != A)."""
    by_inst_day: dict[tuple[str, datetime.date], list[FlatMeeting]] = defaultdict(list)
    for e in events:
        for inst in e.instructors:
            by_inst_day[(inst, e.date)].append(e)
    total = 0
    for _key, evs in by_inst_day.items():
        evs.sort(key=lambda x: x.start_time)
        for a, b in zip(evs, evs[1:]):
            if _is_consecutive_time_slots(a, b, time_slots) and a.room != b.room:
                total += 1
    return total


def _room_swaps_consecutive_slots_group(events: list[FlatMeeting], time_slots: list[datetime.time]) -> int:
    """Per student group: count transitions from room A to room B on the next time slot."""
    by_group_day: dict[tuple[str, datetime.date], list[FlatMeeting]] = defaultdict(list)
    for e in events:
        for g in e.groups:
            by_group_day[(g, e.date)].append(e)
    total = 0
    for _key, evs in by_group_day.items():
        evs.sort(key=lambda x: x.start_time)
        for a, b in zip(evs, evs[1:]):
            if _is_consecutive_time_slots(a, b, time_slots) and a.room != b.room:
                total += 1
    return total


def _room_swaps_consecutive_slots_same_instructor_and_audience(
    events: list[FlatMeeting], time_slots: list[datetime.time]
) -> int:
    """
    Same instructor set and same audience (groups) on consecutive slots but different rooms —
    cohort moves together but must relocate.
    """
    by_key: dict[tuple[datetime.date, tuple[str, ...], tuple[str, ...]], list[FlatMeeting]] = defaultdict(list)
    for e in events:
        by_key[(e.date, e.instructors, e.groups)].append(e)
    total = 0
    for _key, evs in by_key.items():
        evs.sort(key=lambda x: x.start_time)
        for a, b in zip(evs, evs[1:]):
            if _is_consecutive_time_slots(a, b, time_slots) and a.room != b.room:
                total += 1
    return total


def _room_swaps_consecutive_slots_same_course_and_audience(
    events: list[FlatMeeting], time_slots: list[datetime.time]
) -> int:
    """Same course and same audience (groups) on consecutive slots but different rooms (e.g. lec then tut)."""
    by_key: dict[tuple[datetime.date, str, tuple[str, ...]], list[FlatMeeting]] = defaultdict(list)
    for e in events:
        by_key[(e.date, e.course, e.groups)].append(e)
    total = 0
    for _key, evs in by_key.items():
        evs.sort(key=lambda x: x.start_time)
        for a, b in zip(evs, evs[1:]):
            if _is_consecutive_time_slots(a, b, time_slots) and a.room != b.room:
                total += 1
    return total


@dataclass(frozen=True, slots=True)
class TagOrderPair:
    course_idx: int
    course: str
    groups: tuple[str, ...]
    shared_groups: tuple[str, ...]
    date: datetime.date
    early_datetime: datetime.datetime
    late_datetime: datetime.datetime
    early_tag: str
    late_tag: str
    early_event: FlatMeeting
    late_event: FlatMeeting


@dataclass(frozen=True, slots=True)
class RoomCapacityViolation:
    event: FlatMeeting
    expected_students: int
    room_capacity: int


@dataclass(frozen=True, slots=True)
class RoomOversizeViolation:
    event: FlatMeeting
    expected_students: int
    required_capacity: int
    room_capacity: int
    oversize_pct: int


def _iter_flat_meetings(schedule: Schedule) -> Iterator[FlatMeeting]:
    for c_idx, course in enumerate(schedule.courses):
        cname = course.name
        for cls_idx, comp in enumerate(course.components):
            tag = comp.tag
            for series in comp.sessions:
                audience = series.audience
                dates = series.dates
                n = len(dates)
                if not (len(series.start_times) == n and len(series.rooms) == n and len(series.instructors) == n):
                    raise AssertionError(f"parallel lists length mismatch for {cname}/{tag} audience={audience!r}")
                for i in range(n):
                    co = series.instructors[i]
                    yield FlatMeeting(
                        course_idx=c_idx,
                        component_idx=cls_idx,
                        course=cname,
                        component_tag=tag,
                        groups=tuple(audience),
                        date=dates[i],
                        start_time=series.start_times[i],
                        room=series.rooms[i],
                        instructors=tuple(co),
                    )


def _pairwise_overlap_messages(
    intervals: list[tuple[datetime.time, datetime.time, str]],
    resource_label: str,
) -> list[str]:
    messages: list[str] = []
    n = len(intervals)
    for i in range(n):
        s1, e1, lab1 = intervals[i]
        for j in range(i + 1, n):
            s2, e2, lab2 = intervals[j]
            if s1 < e2 and s2 < e1:
                messages.append(
                    f"{resource_label}: overlapping meetings {lab1} "
                    f"[{s1.isoformat(timespec='minutes')},{e1.isoformat(timespec='minutes')}) vs "
                    f"{lab2} [{s2.isoformat(timespec='minutes')},{e2.isoformat(timespec='minutes')}) "
                    f"(duration {TIME_SLOT_DURATION})"
                )
    return messages


def _weekday_name(d: datetime.date) -> str:
    return d.strftime("%a")


def _sort_weekday_items[T](items: list[tuple[str, T]]) -> list[tuple[str, T]]:
    return sorted(items, key=lambda kv: (WEEKDAY_RANK.get(kv[0], 999), kv[0]))


def _group_pairs_by_course(pairs: tuple[TagOrderPair, ...]) -> dict[str, list[TagOrderPair]]:
    grouped: dict[str, list[TagOrderPair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.course].append(pair)
    return dict(sorted(grouped.items()))


def _group_pairs_by_late_tag(pairs: list[TagOrderPair]) -> dict[str, list[TagOrderPair]]:
    grouped: dict[str, list[TagOrderPair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.late_tag].append(pair)
    return dict(sorted(grouped.items()))


def _group_pairs_by_late_datetime(pairs: list[TagOrderPair]) -> dict[datetime.datetime, list[TagOrderPair]]:
    grouped: dict[datetime.datetime, list[TagOrderPair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.late_datetime].append(pair)
    return dict(sorted(grouped.items()))


def _group_size_map(cfg: ScheduleConfig) -> dict[str, int]:
    out: dict[str, int] = {}
    for bucket in (cfg.student_groups.academic, cfg.student_groups.english, cfg.student_groups.elective):
        for group in bucket:
            explicit_size = len(group.students) if group.students else 0
            estimated_size = group.estimated_size or 0
            out[group.id] = max(explicit_size, estimated_size)
    return out


def _group_students_map(cfg: ScheduleConfig) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for bucket in (cfg.student_groups.academic, cfg.student_groups.english, cfg.student_groups.elective):
        for group in bucket:
            for student_email in group.students:
                student = student_email.strip().lower()
                if student:
                    out[group.id].add(student)
    return out


def _config_group_order(cfg: ScheduleConfig) -> tuple[str, ...]:
    ids: list[str] = []
    for bucket in (cfg.student_groups.academic, cfg.student_groups.english, cfg.student_groups.elective):
        for group in bucket:
            ids.append(group.id)
    return tuple(ids)


def _programs_first_group_order(cfg: ScheduleConfig) -> tuple[str, ...]:
    """Group ids in programs YAML order (level dict order, then programs, tracks, groups), then remaining student_groups."""
    seen: set[str] = set()
    ordered: list[str] = []
    for _level_name, level_programs in cfg.programs.items():
        for program in level_programs:
            for track in program.tracks:
                for gid in track.groups:
                    if gid not in seen:
                        seen.add(gid)
                        ordered.append(gid)
    for gid in _config_group_order(cfg):
        if gid not in seen:
            seen.add(gid)
            ordered.append(gid)
    return tuple(ordered)


def _sort_group_ids(group_ids: Iterable[str], cfg: ScheduleConfig) -> list[str]:
    order = {gid: i for i, gid in enumerate(_programs_first_group_order(cfg))}
    return sorted(group_ids, key=lambda g: (order.get(g, 10_000), g))


def _ordered_group_dict[T](mapping: dict[str, T], cfg: ScheduleConfig) -> dict[str, T]:
    return {gid: mapping[gid] for gid in _sort_group_ids(mapping.keys(), cfg)}


def _meeting_expected_students(comp_cfg: CourseConfig.Component, groups: tuple[str, ...], group_sizes: dict[str, int]) -> int:
    if comp_cfg.expected_enrollment is not None:
        return max(0, int(comp_cfg.expected_enrollment))
    return max(0, sum(group_sizes.get(group_id, 0) for group_id in groups))


def _back_to_back_lecture_tutorial_opportunities(cfg: ScheduleConfig) -> int:
    selector_map = resolve_selector_map(cfg)
    num_weeks = _term_weeks(cfg)
    total = 0
    for course in cfg.courses:
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
        component_relations = sorted(explicit_relations) if explicit_relations else [
            (cls_idx, cls_idx + 1) for cls_idx in range(ncomp - 1)
        ]
        for src_idx, dst_idx in component_relations:
            src = course.components[src_idx]
            dst = course.components[dst_idx]
            if str(src.tag).lower() != "lec" or str(dst.tag).lower() != "tut":
                continue
            src_groups = expand_groups(src.student_groups, selector_map)
            dst_groups = expand_groups(dst.student_groups, selector_map)
            if not src_groups or not dst_groups:
                continue
            src_audiences = {tuple([g]) for g in src_groups} if src.per_group else {tuple(src_groups)}
            dst_audiences = {tuple([g]) for g in dst_groups} if dst.per_group else {tuple(dst_groups)}
            shared_audiences = src_audiences & dst_audiences
            if not shared_audiences:
                continue
            total += len(shared_audiences) * min(src.per_week, dst.per_week) * num_weeks
    return total


def _back_to_back_lecture_tutorial_scheduled(events: list[FlatMeeting]) -> int:
    lec_keys: dict[tuple[int, tuple[str, ...], datetime.date, str, datetime.time], int] = defaultdict(int)
    tut_keys: dict[tuple[int, tuple[str, ...], datetime.date, str, datetime.time], int] = defaultdict(int)
    for e in events:
        tag = str(e.component_tag).lower()
        key = (e.course_idx, e.groups, e.date, e.room, e.start_time)
        if tag == "lec":
            target = _slot_end(e.start_time)
            lec_keys[(e.course_idx, e.groups, e.date, e.room, target)] += 1
        elif tag == "tut":
            tut_keys[key] += 1
    paired = 0
    for key, lec_count in lec_keys.items():
        paired += min(lec_count, tut_keys.get(key, 0))
    return paired


def _shared_atomic_groups_for_components(
    lec: CourseConfig.Component,
    tut: CourseConfig.Component,
    lab: CourseConfig.Component,
    selector_map: dict[str, set[str]],
) -> frozenset[str]:
    """Groups that appear in lec, tut, and lab after expanding selectors (program-wide ∪ per-group)."""
    a = set(expand_groups(lec.student_groups, selector_map))
    b = set(expand_groups(tut.student_groups, selector_map))
    c = set(expand_groups(lab.student_groups, selector_map))
    return frozenset(a & b & c)


def _lec_tut_lab_triple_rows(
    cfg: ScheduleConfig,
) -> list[
    tuple[
        int,
        int,
        int,
        int,
        CourseConfig.Component,
        CourseConfig.Component,
        CourseConfig.Component,
        str,
    ]
]:
    """
    Each row: (course_idx, lec_idx, tut_idx, lab_idx, lec, tut, lab, group).

    Skips courses with no tutorial. Skips courses with zero or more than one lecture component.
    Includes every (lec, tut, lab) component **triple** (same course, Cartesian product of tut×lab)
    and every **atomic group** in the intersection of expanded lec, tut, and lab audiences.
    """
    selector_map = resolve_selector_map(cfg)
    rows: list[
        tuple[
            int,
            int,
            int,
            int,
            CourseConfig.Component,
            CourseConfig.Component,
            CourseConfig.Component,
            str,
        ]
    ] = []
    for c_idx, course in enumerate(cfg.courses):
        lecs = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "lec"]
        tuts = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "tut"]
        labs = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "lab"]
        if not tuts or len(lecs) != 1:
            continue
        lec_idx, lec = lecs[0]
        for tut_idx, tut in tuts:
            for lab_idx, lab in labs:
                shared = _shared_atomic_groups_for_components(lec, tut, lab, selector_map)
                for group_id in sorted(shared):
                    rows.append((c_idx, lec_idx, tut_idx, lab_idx, lec, tut, lab, group_id))
    return rows


def _same_day_lec_tut_lab_opportunities(cfg: ScheduleConfig) -> int:
    num_weeks = _term_weeks(cfg)
    total = 0
    for row in _lec_tut_lab_triple_rows(cfg):
        _c_idx, _li, _ti, _lbi, lec, tut, lab, _g = row
        total += min(lec.per_week, tut.per_week, lab.per_week) * num_weeks
    return total


def _same_day_lec_tut_lab_satisfied(events: list[FlatMeeting], cfg: ScheduleConfig) -> int:
    """Weeks per (component triple × atomic group) where those three meetings occur on one calendar day."""
    num_weeks = _term_weeks(cfg)
    start = cfg.term.semester.start_date

    # (course, group, day) -> component_idx -> tag; merged lec/tut list every group on the event
    by_course_group_day: dict[tuple[int, str, datetime.date], dict[int, str]] = defaultdict(dict)
    for e in events:
        tag = str(e.component_tag).lower()
        if tag not in ("lec", "tut", "lab"):
            continue
        for g in e.groups:
            by_course_group_day[(e.course_idx, g, e.date)][e.component_idx] = tag

    triples_by_course_group: dict[tuple[int, str], list[tuple[int, int, int]]] = defaultdict(list)
    for c_idx, li, ti, lbi, _lec, _tut, _lab, group_id in _lec_tut_lab_triple_rows(cfg):
        triples_by_course_group[(c_idx, group_id)].append((li, ti, lbi))

    satisfied_weeks: set[tuple[int, int, int, int, str, int]] = set()
    for (c_idx, g, date), comp_map in by_course_group_day.items():
        wk = (date - start).days // 7
        if wk < 0 or wk >= num_weeks:
            continue
        for li, ti, lbi in triples_by_course_group.get((c_idx, g), []):
            if comp_map.get(li) != "lec" or comp_map.get(ti) != "tut" or comp_map.get(lbi) != "lab":
                continue
            satisfied_weeks.add((c_idx, li, ti, lbi, g, wk))
    return len(satisfied_weeks)


def _collect_tag_before_tag_debug_events(
    events: list[FlatMeeting], early_tag: str, late_tag: str
) -> list[TagOrderPair]:
    """Pairs where early_tag starts strictly before late_tag in wall-clock time (same course, shared group)."""
    pairs: list[TagOrderPair] = []
    by_course: dict[int, list[FlatMeeting]] = defaultdict(list)
    for e in events:
        by_course[e.course_idx].append(e)

    for course_idx, course_events in by_course.items():
        early_events = [e for e in course_events if str(e.component_tag).lower() == early_tag]
        late_events = [e for e in course_events if str(e.component_tag).lower() == late_tag]
        if not early_events or not late_events:
            continue

        for early_event in early_events:
            early_dt = datetime.datetime.combine(early_event.date, early_event.start_time)
            for late_event in late_events:
                late_dt = datetime.datetime.combine(late_event.date, late_event.start_time)
                if early_dt >= late_dt:
                    continue
                shared_groups = tuple(sorted(set(early_event.groups) & set(late_event.groups)))
                if not shared_groups:
                    continue
                pairs.append(
                    TagOrderPair(
                        course_idx=course_idx,
                        course=early_event.course,
                        groups=late_event.groups,
                        shared_groups=shared_groups,
                        date=late_event.date,
                        early_datetime=early_dt,
                        late_datetime=late_dt,
                        early_tag=early_tag,
                        late_tag=late_tag,
                        early_event=early_event,
                        late_event=late_event,
                    )
                )
    pairs.sort(key=lambda p: (p.course, p.late_datetime, p.early_datetime, p.shared_groups))
    return pairs


def list_conflicts(result: SolveResult, cfg: ScheduleConfig | None = None) -> list[str]:
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        return []
    if not result.schedule.courses:
        return []

    events = list(_iter_flat_meetings(result.schedule))

    by_room: dict[tuple[datetime.date, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_group: dict[tuple[datetime.date, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_inst: dict[tuple[datetime.date, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_student: dict[tuple[datetime.date, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    shared_students: set[str] = set()
    group_students_map: dict[str, set[str]] = {}
    if cfg is not None:
        group_students_map = _group_students_map(cfg)
        student_groups_membership: dict[str, set[str]] = defaultdict(set)
        for group_id, students in group_students_map.items():
            for student in students:
                student_groups_membership[student].add(group_id)
        shared_students = {student for student, groups in student_groups_membership.items() if len(groups) > 1}

    for e in events:
        label = f"{e.course}/{e.component_tag}"
        d = e.date
        start = e.start_time
        end = _slot_end(start)
        by_room[(d, e.room)].append((start, end, label))
        for g in e.groups:
            by_group[(d, g)].append((start, end, label))
        for inst in e.instructors:
            by_inst[(d, inst)].append((start, end, label))
        if shared_students:
            meeting_students: set[str] = set()
            for group_id in e.groups:
                meeting_students.update(group_students_map.get(group_id, set()))
            for student in meeting_students:
                if student in shared_students:
                    by_student[(d, student)].append((start, end, label))

    conflicts: list[str] = []
    for key, ivals in by_room.items():
        conflicts.extend(_pairwise_overlap_messages(ivals, f"room {key}"))
    for key, ivals in by_group.items():
        conflicts.extend(_pairwise_overlap_messages(ivals, f"group {key}"))
    for key, ivals in by_inst.items():
        conflicts.extend(_pairwise_overlap_messages(ivals, f"instructor {key}"))
    for key, ivals in by_student.items():
        conflicts.extend(_pairwise_overlap_messages(ivals, f"student {key}"))
    return conflicts


def _term_weeks(cfg: ScheduleConfig) -> int:
    delta = cfg.term.semester.end_date - cfg.term.semester.start_date
    return max(1, delta.days // 7)


def _expected_audience_meeting_counts(cfg: ScheduleConfig) -> dict[tuple[str, str, tuple[str, ...]], int]:
    selector_map = resolve_selector_map(cfg)
    num_weeks = _term_weeks(cfg)
    out: dict[tuple[str, str, tuple[str, ...]], int] = {}
    for course in cfg.courses:
        for cls in course.components:
            groups = expand_groups(cls.student_groups, selector_map)
            if not groups:
                continue
            audiences = [[g] for g in groups] if cls.per_group else [groups]
            count = cls.per_week * num_weeks
            for audience in audiences:
                aud_key = tuple(audience)
                key = (course.name, str(cls.tag), aud_key)
                out[key] = out.get(key, 0) + count
    return out


def _actual_audience_meeting_counts(schedule: Schedule, cfg: ScheduleConfig) -> dict[tuple[str, str, tuple[str, ...]], int]:
    out: dict[tuple[str, str, tuple[str, ...]], int] = {}
    for csched in schedule.courses:
        for comp_out in csched.components:
            for series in comp_out.sessions:
                key = (csched.name, str(comp_out.tag), tuple(series.audience))
                out[key] = out.get(key, 0) + len(series.dates)
    return out


def list_unsatisfied(result: SolveResult, cfg: ScheduleConfig) -> list[str]:
    expected = _expected_audience_meeting_counts(cfg)
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        if expected:
            return [
                f"solve status is {result.status!r} but config implies meetings for "
                f"{len(expected)} (course, component, audience) keys"
            ]
        return []

    actual = _actual_audience_meeting_counts(result.schedule, cfg)
    unsatisfied: list[str] = []

    all_keys = sorted(set(expected) | set(actual))
    for key in all_keys:
        exp = expected.get(key)
        act = actual.get(key)
        if exp == act:
            continue
        if exp is None:
            unsatisfied.append(f"unexpected scheduled meetings for key={key!r}: actual={act}")
        elif act is None:
            unsatisfied.append(f"missing scheduled meetings for key={key!r}: expected={exp}, actual=0")
        else:
            unsatisfied.append(f"meeting count mismatch for key={key!r}: expected={exp}, actual={act}")
    return unsatisfied


def calculate_schedule_metrics(result: SolveResult, cfg: ScheduleConfig) -> ScheduleMetrics:
    events = list(_iter_flat_meetings(result.schedule)) if result.status in ("OPTIMAL", "FEASIBLE") else []
    group_sizes = _group_size_map(cfg)
    room_capacity_map = {room.id: room.capacity for room in cfg.rooms}
    available_slots_per_room = len(teaching_dates(cfg)) * len(cfg.term.time_slots)

    events_per_weekday: dict[str, int] = defaultdict(int)
    per_group_weekday_load: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_group_date_subjects: dict[str, dict[datetime.date, set[str]]] = defaultdict(lambda: defaultdict(set))
    per_group_saturday_events: dict[str, int] = defaultdict(int)
    saturday_event_count = 0
    timeslot_histogram_by_weekday: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    meeting_hours_weighted_by_group_count_per_weekday: dict[str, float] = defaultdict(float)
    instructor_weekday_load: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    events_per_course_component: dict[str, int] = defaultdict(int)

    room_students_total: dict[str, int] = defaultdict(int)
    room_capacity_total: dict[str, int] = defaultdict(int)
    room_used_slots: dict[str, set[tuple[datetime.date, datetime.time]]] = defaultdict(set)
    capacity_violations: list[RoomCapacityViolation] = []
    oversize_violations: list[RoomOversizeViolation] = []

    for e in events:
        weekday = _weekday_name(e.date)
        slot_key = e.start_time.isoformat(timespec="minutes")
        event_hours = TIME_SLOT_DURATION.total_seconds() / 3600.0
        events_per_weekday[weekday] += 1
        timeslot_histogram_by_weekday[weekday][slot_key] += 1
        meeting_hours_weighted_by_group_count_per_weekday[weekday] += event_hours * len(e.groups)
        events_per_course_component[f"{e.course}/{e.component_tag}"] += 1
        for g in e.groups:
            per_group_weekday_load[g][weekday] += 1
            if e.date.weekday() in WEEKDAYS_MON_SAT:
                by_group_date_subjects[g][e.date].add(e.course)
        if e.date.weekday() == 5:
            saturday_event_count += 1
            for g in e.groups:
                per_group_saturday_events[g] += 1
        for inst in e.instructors:
            instructor_weekday_load[inst][weekday] += 1

        room_used_slots[e.room].add((e.date, e.start_time))
        room_capacity = room_capacity_map.get(e.room, 0)
        room_capacity_total[e.room] += room_capacity
        comp_cfg = cfg.courses[e.course_idx].components[e.component_idx] if e.course_idx < len(cfg.courses) and e.component_idx < len(cfg.courses[e.course_idx].components) else None
        expected_students = _meeting_expected_students(comp_cfg, e.groups, group_sizes) if comp_cfg is not None else 0
        room_students_total[e.room] += expected_students
        if expected_students > room_capacity:
            capacity_violations.append(
                RoomCapacityViolation(
                    event=e,
                    expected_students=expected_students,
                    room_capacity=room_capacity,
                )
            )
        feasible_for_full = any(capacity >= expected_students for capacity in room_capacity_map.values())
        required_capacity = expected_students if feasible_for_full else math.ceil(expected_students * 0.9)
        required_capacity = max(1, required_capacity)
        if room_capacity > required_capacity:
            oversize_pct = ((room_capacity - required_capacity) * 100) // max(1, required_capacity)
            if oversize_pct > 30:
                oversize_violations.append(
                    RoomOversizeViolation(
                        event=e,
                        expected_students=expected_students,
                        required_capacity=required_capacity,
                        room_capacity=room_capacity,
                        oversize_pct=oversize_pct,
                    )
                )

    room_capacity_utilization_by_room: dict[str, float] = {}
    for room_id in room_capacity_map:
        denom = room_capacity_total.get(room_id, 0)
        room_capacity_utilization_by_room[room_id] = (room_students_total.get(room_id, 0) / denom) if denom > 0 else 0.0

    total_capacity_denom = sum(room_capacity_total.values())
    room_capacity_utilization_overall = (sum(room_students_total.values()) / total_capacity_denom) if total_capacity_denom > 0 else 0.0

    room_time_utilization_by_room: dict[str, float] = {}
    for room_id in room_capacity_map:
        used = len(room_used_slots.get(room_id, set()))
        room_time_utilization_by_room[room_id] = (used / available_slots_per_room) if available_slots_per_room > 0 else 0.0
    total_used_slots = sum(len(v) for v in room_used_slots.values())
    total_available_slots = available_slots_per_room * len(room_capacity_map)
    room_time_utilization_overall = (total_used_slots / total_available_slots) if total_available_slots > 0 else 0.0
    labs_before_tutorial_events = _collect_tag_before_tag_debug_events(events, "lab", "tut")
    labs_before_lecture_events = _collect_tag_before_tag_debug_events(events, "lab", "lec")
    tutorials_before_lecture_events = _collect_tag_before_tag_debug_events(events, "tut", "lec")
    events_per_course_components_grouped: dict[str, dict[str, int]] = {}

    for course_cfg in cfg.courses:
        grouped_components: dict[str, int] = {}
        for comp_cfg in course_cfg.components:
            tag = str(comp_cfg.tag)
            key = f"{course_cfg.name}/{tag}"
            grouped_components[tag] = grouped_components.get(tag, 0) + events_per_course_component.get(key, 0)
        events_per_course_components_grouped[course_cfg.name] = grouped_components

    cfg_course_tags: dict[str, set[str]] = {
        course_cfg.name: {str(comp_cfg.tag) for comp_cfg in course_cfg.components}
        for course_cfg in cfg.courses
    }
    for key, count in events_per_course_component.items():
        course_name, component_tag = key.split("/", 1)
        if course_name in cfg_course_tags and component_tag in cfg_course_tags[course_name]:
            continue
        if course_name not in events_per_course_components_grouped:
            events_per_course_components_grouped[course_name] = {}
        if component_tag not in events_per_course_components_grouped[course_name]:
            events_per_course_components_grouped[course_name][component_tag] = 0
        events_per_course_components_grouped[course_name][component_tag] += count

    per_group_per_day_distinct_subjects_raw: dict[str, dict[str, int]] = {}
    distinct_subjects_per_group_day_sum = 0
    max_distinct_subjects_any_group_day = 0
    for group_id, by_date in by_group_date_subjects.items():
        day_map: dict[str, int] = {}
        for d, subjects in sorted(by_date.items()):
            n = len(subjects)
            day_map[d.isoformat()] = n
            if d.weekday() in WEEKDAYS_MON_FRI:
                distinct_subjects_per_group_day_sum += n
                if n > max_distinct_subjects_any_group_day:
                    max_distinct_subjects_any_group_day = n
        per_group_per_day_distinct_subjects_raw[group_id] = day_map

    per_group_per_day_distinct_subjects = _ordered_group_dict(per_group_per_day_distinct_subjects_raw, cfg)
    per_group_saturday_nonempty = {g: c for g, c in per_group_saturday_events.items() if c > 0}

    time_slots = cfg.term.time_slots
    room_swaps_inst = (
        _room_swaps_consecutive_slots_instructor(events, time_slots) if events else 0
    )
    room_swaps_grp = _room_swaps_consecutive_slots_group(events, time_slots) if events else 0
    room_swaps_joint = (
        _room_swaps_consecutive_slots_same_instructor_and_audience(events, time_slots) if events else 0
    )
    room_swaps_course_aud = (
        _room_swaps_consecutive_slots_same_course_and_audience(events, time_slots) if events else 0
    )

    return ScheduleMetrics(
        conflicts=tuple(list_conflicts(result, cfg)),
        unsatisfied=tuple(list_unsatisfied(result, cfg)),
        total_events=len(events),
        total_timeslots_count=len(teaching_dates(cfg)) * len(cfg.term.time_slots),
        back_to_back_lecture_tutorial_opportunities=_back_to_back_lecture_tutorial_opportunities(cfg),
        back_to_back_lecture_tutorial_scheduled=_back_to_back_lecture_tutorial_scheduled(events),
        same_day_lec_tut_lab_opportunities=_same_day_lec_tut_lab_opportunities(cfg),
        same_day_lec_tut_lab_satisfied=_same_day_lec_tut_lab_satisfied(events, cfg),
        labs_before_tutorial_count=len(labs_before_tutorial_events),
        labs_before_lecture_count=len(labs_before_lecture_events),
        tutorials_before_lecture_count=len(tutorials_before_lecture_events),
        events_exceeding_room_capacity_count=len(capacity_violations),
        events_exceeding_room_capacity=tuple(capacity_violations),
        events_with_room_much_larger_than_students_count=len(oversize_violations),
        events_with_room_much_larger_than_students=tuple(oversize_violations),
        events=tuple(events),
        labs_before_tutorial_events=tuple(labs_before_tutorial_events),
        labs_before_lecture_events=tuple(labs_before_lecture_events),
        tutorials_before_lecture_events=tuple(tutorials_before_lecture_events),
        events_per_weekday=dict(sorted(events_per_weekday.items())),
        per_group_weekday_load=_ordered_group_dict(
            {group: dict(sorted(load.items())) for group, load in per_group_weekday_load.items()},
            cfg,
        ),
        per_group_per_day_distinct_subjects=per_group_per_day_distinct_subjects,
        distinct_subjects_per_group_day_sum=distinct_subjects_per_group_day_sum,
        max_distinct_subjects_any_group_day=max_distinct_subjects_any_group_day,
        saturday_event_count=saturday_event_count,
        per_group_saturday_events=_ordered_group_dict(per_group_saturday_nonempty, cfg),
        timeslot_histogram_by_weekday={
            weekday: dict(sorted(hist.items()))
            for weekday, hist in sorted(timeslot_histogram_by_weekday.items())
        },
        meeting_hours_weighted_by_group_count_per_weekday=dict(
            _sort_weekday_items(list(meeting_hours_weighted_by_group_count_per_weekday.items()))
        ),
        room_capacity_utilization_by_room=dict(sorted(room_capacity_utilization_by_room.items())),
        room_capacity_utilization_overall=room_capacity_utilization_overall,
        room_time_utilization_by_room=dict(sorted(room_time_utilization_by_room.items())),
        room_time_utilization_overall=room_time_utilization_overall,
        instructor_weekday_load={
            inst: dict(sorted(load.items()))
            for inst, load in sorted(instructor_weekday_load.items())
        },
        events_per_course_component=dict(sorted(events_per_course_component.items())),
        events_per_course_components_grouped=events_per_course_components_grouped,
        room_swaps_instructor_consecutive_slots=room_swaps_inst,
        room_swaps_group_consecutive_slots=room_swaps_grp,
        room_swaps_same_instructor_and_audience_consecutive_slots=room_swaps_joint,
        room_swaps_same_course_and_audience_consecutive_slots=room_swaps_course_aud,
    )


def assert_no_conflicts(result: SolveResult, cfg: ScheduleConfig | None = None) -> None:
    conflicts = list_conflicts(result, cfg)
    if conflicts:
        raise AssertionError(f"{len(conflicts)} scheduling conflict(s):\n" + "\n".join(conflicts))


def assert_all_satisfied(result: SolveResult, cfg: ScheduleConfig) -> None:
    unsatisfied = list_unsatisfied(result, cfg)
    if unsatisfied:
        raise AssertionError(
            f"schedule does not satisfy config-implied meeting counts ({len(unsatisfied)} issue(s)):\n"
            + "\n".join(unsatisfied)
        )


def _load_config(path: Path) -> ScheduleConfig:
    return ScheduleConfig.from_yaml(path)


def _load_solution(path: Path) -> SolveResult:
    with path.open(encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    return SolveResult.model_validate(payload)


def _collect_distinct_counts_for_groups(
    per_group: dict[str, dict[str, int]],
    group_ids: set[str] | None,
    *,
    weekdays: frozenset[int] | None = None,
) -> list[int]:
    """Distinct course counts per (group, calendar day), optionally filtered by group and weekday()."""
    out: list[int] = []
    for group_id, by_day in per_group.items():
        if group_ids is not None and group_id not in group_ids:
            continue
        for day_iso, n in by_day.items():
            wd = datetime.date.fromisoformat(day_iso).weekday()
            if weekdays is not None and wd not in weekdays:
                continue
            if weekdays is None and wd not in WEEKDAYS_MON_FRI:
                continue
            out.append(n)
    return out


def _avg_distinct_courses_by_weekday_label(per_group: dict[str, dict[str, int]]) -> dict[str, float]:
    """Mean distinct subjects per (group, calendar day), grouped by weekday name (%a)."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for _gid, by_day in per_group.items():
        for day_iso, n in by_day.items():
            buckets[datetime.date.fromisoformat(day_iso).strftime("%a")].append(n)
    return {wd: statistics.mean(vals) for wd, vals in buckets.items()}


def _print_human_report(metrics: ScheduleMetrics, cfg: ScheduleConfig) -> None:
    total_weighted_group_hours = sum(metrics.meeting_hours_weighted_by_group_count_per_weekday.values())
    total_capacity_overflow_students = sum(
        max(0, violation.expected_students - violation.room_capacity)
        for violation in metrics.events_exceeding_room_capacity
    )

    print("=== Schedule Metrics ===")
    print(f"Total events: {metrics.total_events}")
    print(f"Total timeslots count: {metrics.total_timeslots_count}")
    print(
        "Room swaps on consecutive time slots (less is better): "
        f"instructor={metrics.room_swaps_instructor_consecutive_slots}, "
        f"group={metrics.room_swaps_group_consecutive_slots}, "
        f"same instructor+audience={metrics.room_swaps_same_instructor_and_audience_consecutive_slots}, "
        f"same course+audience={metrics.room_swaps_same_course_and_audience_consecutive_slots}"
    )
    print(f"Total meeting hours weighted by group count: {total_weighted_group_hours:.1f}")
    print(
        "Back-to-back lec->tut (more is better): "
        f"{metrics.back_to_back_lecture_tutorial_scheduled}/"
        f"{metrics.back_to_back_lecture_tutorial_opportunities}"
    )
    print(
        "Same calendar day lec+tut+lab (more is better): "
        f"{metrics.same_day_lec_tut_lab_satisfied}/{metrics.same_day_lec_tut_lab_opportunities} "
        "(per course×group×term-week with all three on one day; lec/tut may be program-wide, lab per group)"
    )
    print(
        "Labs before tutorial or lecture in time (less is better): "
        f"{metrics.labs_before_tutorial_count} before tutorial, "
        f"{metrics.labs_before_lecture_count} before lecture"
    )
    print(f"Tutorials before lecture in time (less is better): {metrics.tutorials_before_lecture_count}")
    print(f"Events exceeding room capacity (less is better): {metrics.events_exceeding_room_capacity_count}")
    print(
        "Events with room much larger than students (less is better): "
        f"{metrics.events_with_room_much_larger_than_students_count}"
    )
    print(f"Total capacity overflow students (less is better): {total_capacity_overflow_students}")
    print(f"Conflicts (less is better): {len(metrics.conflicts)}")
    print(f"Unsatisfied items (less is better): {len(metrics.unsatisfied)}")
    print(f"Overall room capacity utilization: {metrics.room_capacity_utilization_overall:.3f}")
    print(f"Overall room time utilization: {metrics.room_time_utilization_overall:.3f}")
    _mf_distinct_counts = _collect_distinct_counts_for_groups(
        metrics.per_group_per_day_distinct_subjects, None
    )
    _n_mf_days = len(_mf_distinct_counts)
    _avg_distinct_courses_mf = (
        metrics.distinct_subjects_per_group_day_sum / _n_mf_days if _n_mf_days else 0.0
    )
    _median_distinct_mf = statistics.median(_mf_distinct_counts) if _mf_distinct_counts else 0.0
    _pct_one_course = (
        100.0 * sum(1 for x in _mf_distinct_counts if x == 1) / len(_mf_distinct_counts)
        if _mf_distinct_counts
        else 0.0
    )
    _pct_two_courses = (
        100.0 * sum(1 for x in _mf_distinct_counts if x == 2) / len(_mf_distinct_counts)
        if _mf_distinct_counts
        else 0.0
    )
    print(
        "All cohorts — distinct courses per group per Mon-Fri calendar day: "
        f"avg={_avg_distinct_courses_mf:.2f}, median={_median_distinct_mf:.2f}, "
        f"max={metrics.max_distinct_subjects_any_group_day} "
        f"({_n_mf_days} group×days; {_pct_one_course:.0f}% with 1 course, {_pct_two_courses:.0f}% with 2; "
        "1 often means lec/tut/lab same course; lower is better)"
    )
    _b1_en_groups = resolve_selector_map(cfg).get("@bachelor_1_en", set())
    if _b1_en_groups:
        _b1_mf = _collect_distinct_counts_for_groups(
            metrics.per_group_per_day_distinct_subjects, _b1_en_groups
        )
        _b1_mon = _collect_distinct_counts_for_groups(
            metrics.per_group_per_day_distinct_subjects,
            _b1_en_groups,
            weekdays=frozenset({0}),
        )
        _n_b1_mf = len(_b1_mf)
        _avg_b1_mf = sum(_b1_mf) / _n_b1_mf if _b1_mf else 0.0
        _med_b1_mf = statistics.median(_b1_mf) if _b1_mf else 0.0
        _avg_b1_mon = sum(_b1_mon) / len(_b1_mon) if _b1_mon else 0.0
        print(
            "  @bachelor_1_en only (B25-CSE*, B25-DSAI*): Mon–Fri avg="
            f"{_avg_b1_mf:.2f}, median={_med_b1_mf:.2f} ({_n_b1_mf} group×days); "
            f"Mondays avg distinct courses={_avg_b1_mon:.2f} ({len(_b1_mon)} group×Mondays)"
        )
    print(f"Saturday events (less is better): {metrics.saturday_event_count}")

    print(
        "\nPer weekday (events | timeslot histogram | meeting hours × group count | "
        "avg distinct courses per group×day):"
    )
    _avg_distinct_wd = _avg_distinct_courses_by_weekday_label(metrics.per_group_per_day_distinct_subjects)
    _weekdays_union = (
        set(metrics.events_per_weekday)
        | set(metrics.timeslot_histogram_by_weekday)
        | set(metrics.meeting_hours_weighted_by_group_count_per_weekday)
        | set(_avg_distinct_wd)
    )
    for weekday, _ in _sort_weekday_items([(w, None) for w in _weekdays_union]):
        ev = metrics.events_per_weekday.get(weekday, 0)
        histogram = metrics.timeslot_histogram_by_weekday.get(weekday, {})
        bins = ", ".join(f"{slot}={count}" for slot, count in histogram.items())
        hrs = metrics.meeting_hours_weighted_by_group_count_per_weekday.get(weekday, 0.0)
        mid = bins if bins else "—"
        avg_dc = _avg_distinct_wd.get(weekday, 0.0)
        print(f"  {weekday}: {ev} | {mid} | {hrs:.1f} | {avg_dc:.2f}")

    print("\nEvents per course/component:")
    for course_name, components in metrics.events_per_course_components_grouped.items():
        formatted = ", ".join(f"{tag} {count}" for tag, count in components.items())
        print(f"  {course_name}: [{formatted}]")

    print("\nRoom utilization by room:")
    for room_id in sorted(
        set(metrics.room_capacity_utilization_by_room) | set(metrics.room_time_utilization_by_room)
    ):
        time_u = metrics.room_time_utilization_by_room.get(room_id, 0.0)
        cap_u = metrics.room_capacity_utilization_by_room.get(room_id, 0.0)
        print(f"  {room_id}: time_util={time_u:.3f} capacity_util={cap_u:.3f}")

    print(
        "\nPer-group weekday load and distinct subjects "
        "(e=events, s=distinct courses; per weekday, mean over calendar days in term):"
    )
    _load_ids = list(metrics.per_group_weekday_load.keys())
    _subj_ids = [g for g in metrics.per_group_per_day_distinct_subjects if g not in metrics.per_group_weekday_load]
    for group_id in _load_ids + _subj_ids:
        load = metrics.per_group_weekday_load.get(group_id, {})
        by_day = metrics.per_group_per_day_distinct_subjects.get(group_id, {})
        _weekdays_here = set(load) | {
            datetime.date.fromisoformat(day_iso).strftime("%a") for day_iso in by_day
        }
        ordered_days = [d for d, _ in _sort_weekday_items([(d, 0) for d in _weekdays_here])]
        parts: list[str] = []
        for d in ordered_days:
            day_isos = [iso for iso in by_day if datetime.date.fromisoformat(iso).strftime("%a") == d]
            n_cal = len(day_isos)
            ev_total = load.get(d, 0)
            if n_cal:
                ev_m = ev_total / n_cal
                s_m = sum(by_day[iso] for iso in day_isos) / n_cal
            else:
                ev_m = float(ev_total)
                s_m = 0.0
            parts.append(f"{d}={round(ev_m)}e {round(s_m)}s")
        print(f"  {group_id}: {', '.join(parts)}")

    # Only instructors with enough weekly meetings to be "loaded"; sorted by total (desc).
    _min_instructor_week_load = 4
    _loaded: list[tuple[str, dict[str, int], int]] = []
    for inst, load in metrics.instructor_weekday_load.items():
        total = sum(load.values())
        if total >= _min_instructor_week_load:
            _loaded.append((inst, load, total))
    _loaded.sort(key=lambda t: (-t[2], t[0]))
    print(
        "\nInstructor weekday load "
        f"(total weekly meetings; only total>={_min_instructor_week_load}; more is busier):"
    )
    if _loaded:
        for inst, load, total in _loaded:
            ordered = _sort_weekday_items(list(load.items()))
            bins = ", ".join(f"{day}={count}" for day, count in ordered)
            print(f"  {inst} (total={total}): {bins}")
    else:
        print("  none")

    print("\nRoom capacity violations:")
    if metrics.events_exceeding_room_capacity:
        for violation in metrics.events_exceeding_room_capacity:
            e = violation.event
            print(
                f"  - {e.course}/{e.component_tag} groups={len(e.groups)} "
                f"date={e.date.isoformat()} start={e.start_time.isoformat(timespec='minutes')} "
                f"room={e.room} expected={violation.expected_students} capacity={violation.room_capacity}"
            )
    else:
        print("  none")

    print("\nWrong component order (early before late in wall-clock time):")
    combined_labs_before = (
        tuple(metrics.labs_before_tutorial_events)
        + tuple(metrics.labs_before_lecture_events)
        + tuple(metrics.tutorials_before_lecture_events)
    )
    if combined_labs_before:
        for course, pairs in _group_pairs_by_course(combined_labs_before).items():
            print(f"  {course}:")
            for late_tag, by_tag in _group_pairs_by_late_tag(pairs).items():
                for late_dt, by_late_dt in _group_pairs_by_late_datetime(by_tag).items():
                    print(
                        f"    {late_tag}={late_dt.isoformat(sep=' ', timespec='minutes')} "
                        f"later than events: {len(by_late_dt)}:"
                    )
                    for pair in by_late_dt:
                        print(
                            f"      - {pair.early_tag}={pair.early_datetime.isoformat(sep=' ', timespec='minutes')} "
                            f"shared_groups={list(pair.shared_groups)}"
                        )
    else:
        print("  none")

    if metrics.conflicts:
        print("\nConflicts:")
        for item in metrics.conflicts:
            print(f"  - {item}")
    if metrics.unsatisfied:
        print("\nUnsatisfied:")
        for item in metrics.unsatisfied:
            print(f"  - {item}")


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Compute schedule metrics from config and solution YAML files.")
    parser.add_argument("--config", required=True, type=Path, help="Path to schedule config YAML.")
    parser.add_argument("--solution", required=True, type=Path, help="Path to solver output YAML.")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    result = _load_solution(args.solution)
    metrics = calculate_schedule_metrics(result, cfg)
    _print_human_report(metrics, cfg)


if __name__ == "__main__":
    cli_main()
