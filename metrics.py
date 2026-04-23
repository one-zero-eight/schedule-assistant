"""
Innopolis University (IU) — Scheduling

============================================================
Calendar & Trimester Layout
============================================================
Each academic year consists of three trimesters: Fall, Spring, and Summer.
Fall and Spring trimesters are divided into two teaching blocks (half-trimester sub-periods). Summer trimester is not divided into teaching blocks. The fourth year of the bachelor's program and second year of the master's program is fully dedicated to the thesis and
has no scheduled courses.

============================================================
Programmes & Tracks
============================================================
Bachelor of Science tracks include Software Development, Cybersecurity, Data
Science, AI, Robotics, and Game Development. Master's programmes (SE, AIDE,
Robotics, etc.) follow separate curricula. English-taught and Russian-taught
programmes coexist with distinct track structures and course sets.

============================================================
Student Grouping
============================================================
Every student belongs to an **academic group** identified by a three-part code:
``<cohort>-<track>-<index>`` (e.g. ``B22-CBS-02`` — bachelors, 2022 intake,
Cybersecurity, group 02). Academic groups are the primary vehicle for
co-scheduling mandatory courses.

Groups are **not always disjoint**: a student may simultaneously belong to
cross-cutting streams that cut across academic groups — most notably
**English level groups**, which are formed independently of the academic group
and determine placement in English-language classes during the first year. Also some students can attend classes from year below their study to retake courses they failed in.

Years 1–2 are largely shared across BSc tracks before the track split (they will have the same courses, yet having different groups); later years (3+) are increasingly track-specific.

============================================================
Course Types & Instructional Formats
============================================================
**Core Courses** — the standard instructional pattern is a triplet of:

  * **Lecture** — one session per week, delivered to the full audience
    (all academic groups enrolled in the subject) simultaneously.
  * **Tutorial** — one session per week, also full-audience, typically
    scheduled immediately after the corresponding lecture (same instructor
    or a different one).
  * **Lab** — one session per week, usually taught by a Teaching Assistant,
    and typically scheduled **per academic group** (i.e. each group gets its
    own lab section). Labs are spread throughout the week but should
    ideally follow the tutorial for the same topic to avoid topic jumps.
    Real data may contain exceptions to this ordering.

Note that in real data there are exceptions to this pattern, for example some courses have only lecture and lab; or some courses taught by co-teaching set of instructors at the same time in the same room; some courses have more than one lecture and tutorial per week, some courses have load distribution between lecture and tutorial (one meeting per [group 1, group 2, group 3] and other meeting per [group 4, group 5, group 6] at the same time but different instructors and rooms). Also meeting can include groups from different tracks and different programs very rarely.

**English Course (Year 1)** — although classified as a core course, English
does NOT follow the lecture/tutorial/lab pattern. Instead it consists of
two class meetings per week in the student's *English level group* (not the
academic group). There is no separate lecture, tutorial, or lab component.

**Electives** — students rank their top-5 elective preferences and are
subsequently assigned to exactly one. Multiple academic groups (and
potentially multiple tracks) may enrol in the same elective. Some electives
bundle several groups together for a shared lecture while maintaining
separate per-group lab sections. Electives appear in specific trimesters:

  * Summer of Year 1 — one Tech elective + one Humanities elective
  * Summer of Year 2 — one Tech elective + one Humanities elective
  * Fall of Year 3  — one Tech elective

**Sports Electives** — throughout the semester each student must accumulate
30 academic hours of sports classes. Enrolment is free-form and the sports
schedule is drawn up independently from the academic timetable.

============================================================
Venue & Delivery Mode
============================================================
IU has a single campus building. Rooms vary by capacity (lecture halls,
seminar rooms, labs). In addition to physical rooms, **online delivery** is
supported — an online meeting does not consume a physical room and there is
no practical limit on the number of concurrent online sessions.

============================================================
Personnel
============================================================
Teaching staff range from professors (who typically deliver lectures and
tutorials) through teaching assistants (who run lab sections).
We often care about instructor availability (preferred time-slots).
For instructors, workload should usually be clustered into fewer weekdays (instead of being spread by single classes), while keeping daily load within a practical limit of roughly 4-5 meetings/day where possible.

Additionaly, some teaching assistant are study on master or bachelor program 
in the same university so their availability can depends on the schedule itself. 

============================================================
Scheduling Quality Targets
============================================================
In addition to hard feasibility constraints (no overlaps, room capacity,
required weekly meetings), the timetable optimization should strive to achieve Scheduling Quality Targets:

* Keep per-group daily meeting load no more than **5 meetings/day**
  (about **7.5 hours**, with each meeting = 90 minutes), and avoid
  overload patterns such as 6-7+ meetings/day unless unavoidable.
* Keep per-group daily distinct subjects no more than **3 subjects/day**.
* Per-group workload should also usually be clustered into fewer weekdays (instead of being spread over week by one meeting per day), while keeping daily load within a practical limit.
* Prefer the smallest suitable room; avoid assigning small audiences to excessively large rooms where possible (for example, scheduling a 2-student group in a 60-seat room is operationally unreasonable).
* For back-to-back classes, prefer using the same room to avoid unnecessary transitions; instructor-side continuity has higher priority, while student room-to-room movement is acceptable.
* For lecture -> tutorial sequences, prefer immediate adjacency and placement in the same room.
* Prefer scheduling lecture, tutorial, and lab for the same audience on the same day where possible.
* Avoid Saturday classes when possible.
* Try to keep lecture and tutorial in the morning or afternoon, not in the evening.
* Try to avoid events in late evening (later than 18:00) and weekends.

These are optimization targets, not strict hard constraints: feasibility and
curriculum requirements still take precedence.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from config import CourseConfig, ScheduleConfig, expand_groups, resolve_selector_map
from main import Schedule, SolveResult, teaching_days

TIME_SLOT_DURATION = datetime.timedelta(minutes=90)
WEEKDAY_RANK = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
WEEKDAYS_MON_SAT = frozenset(range(6))
BAD_DAY_EVENT_THRESHOLD = 5
BAD_DAY_DISTINCT_SUBJECTS_THRESHOLD = 3
INSTRUCTOR_DAILY_EVENT_THRESHOLD = 5
LARGE_ROOM_CAPACITY_THRESHOLD = 100
LARGE_ROOM_EXCEEDING_GAP = 0.9
ROOM_OVERSIZE_PCT_THRESHOLD = 30


@dataclass(frozen=True, slots=True)
class FlatMeeting:
    course_idx: int
    component_idx: int
    course: str
    component_tag: str
    groups: tuple[str, ...]
    day: str
    day_index: int
    start_time: datetime.time
    room: str
    instructors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduleMetrics:
    run_status: str
    run_elapsed_seconds: float | None
    hard_conflicts: tuple[str, ...]
    hard_unsatisfied_constraints: tuple[str, ...]
    ctx_total_events: int
    ctx_total_timeslots_count: int
    ctx_total_weighted_group_hours: float
    ctx_total_core_course_groups_count: int
    quality_back_to_back_lec_tut_opportunities: int
    quality_back_to_back_lec_tut_scheduled: int
    quality_back_to_back_lec_tut_ratio: float
    quality_same_day_lec_tut_lab_opportunities: int
    quality_same_day_lec_tut_lab_satisfied: int
    quality_same_day_lec_tut_lab_ratio: float
    quality_labs_before_tutorial_count: int
    quality_labs_before_lecture_count: int
    quality_tutorials_before_lecture_count: int
    rooms_events_exceeding_capacity_count: int
    rooms_events_much_larger_than_students_count: int
    rooms_overflow_students_total: int
    rooms_wasted_seat_hours: float
    room_continuity_lec_tut_same_room_opportunities: int
    room_continuity_lec_tut_same_room_count: int
    room_continuity_lec_tut_same_room_ratio: float
    room_continuity_instructor_room_swaps_consecutive_opportunities: int
    room_continuity_instructor_room_swaps_consecutive: int
    room_continuity_instructor_room_swaps_consecutive_ratio: float
    hatred_global_saturday_event_count: int
    hatred_global_sunday_event_count: int
    hatred_global_late_events_count: int
    hatred_global_late_events_lec_count: int
    hatred_global_late_events_tut_count: int
    hatred_global_late_events_lab_or_class_count: int
    hatred_student_bad_days_events_total: int
    hatred_student_groups_with_bad_days_events: int
    hatred_student_bad_days_distinct_total: int
    hatred_student_groups_with_bad_days_distinct: int
    hatred_student_total_active_days_excess_groups: int
    hatred_student_groups_with_excess_day_count: int
    hatred_instructor_total_active_days_excess: int
    hatred_instructor_with_excess_day_count: int


def _slot_end(start: datetime.time) -> datetime.time:
    return (datetime.datetime.combine(datetime.date.min, start) + TIME_SLOT_DURATION).time()


def _slot_index(start: datetime.time, time_slots: list[datetime.time]) -> int | None:
    for i, t in enumerate(time_slots):
        if t == start:
            return i
    return None


def _is_consecutive(a: FlatMeeting, b: FlatMeeting, time_slots: list[datetime.time]) -> bool:
    if a.day != b.day:
        return False
    ia = _slot_index(a.start_time, time_slots)
    ib = _slot_index(b.start_time, time_slots)
    return ia is not None and ib is not None and ib == ia + 1


def _lec_tut_same_room_stats(events: list[FlatMeeting], cfg: ScheduleConfig) -> tuple[int, int]:
    opportunities = _back_to_back_lecture_tutorial_opportunities(cfg)
    tuts_by_start_room: dict[tuple[int, tuple[str, ...], str, datetime.time, str], set[int]] = defaultdict(set)
    same_room_pairs: set[tuple[int, int, int]] = set()
    for e in events:
        if str(e.component_tag).lower() == "tut":
            key = (e.course_idx, e.groups, e.day, e.start_time, e.room)
            tuts_by_start_room[key].add(e.component_idx)
    for e in events:
        if str(e.component_tag).lower() != "lec":
            continue
        key = (e.course_idx, e.groups, e.day, _slot_end(e.start_time), e.room)
        for tut_component_idx in tuts_by_start_room.get(key, set()):
            same_room_pairs.add((e.course_idx, e.component_idx, tut_component_idx))
    return opportunities, len(same_room_pairs)


def _instructor_room_swaps_consecutive(
    events: list[FlatMeeting], time_slots: list[datetime.time]
) -> tuple[int, int]:
    by_instructor_day: dict[tuple[str, str], list[FlatMeeting]] = defaultdict(list)
    for e in events:
        for instructor in e.instructors:
            by_instructor_day[(instructor, e.day)].append(e)
    opportunities = 0
    total = 0
    for evs in by_instructor_day.values():
        evs.sort(key=lambda x: x.start_time)
        for a, b in zip(evs, evs[1:]):
            if _is_consecutive(a, b, time_slots):
                opportunities += 1
                if a.room != b.room:
                    total += 1
    return opportunities, total


def _iter_flat_meetings(schedule: Schedule, cfg: ScheduleConfig) -> Iterator[FlatMeeting]:
    rank = {d: i for i, d in enumerate(teaching_days(cfg))}
    for c_idx, course in enumerate(schedule.courses):
        for comp_idx, comp in enumerate(course.components):
            for series in comp.sessions:
                n = len(series.days)
                if not (len(series.start_times) == n and len(series.rooms) == n and len(series.instructors) == n):
                    raise AssertionError(f"parallel lists length mismatch for {course.name}/{comp.tag}")
                for i in range(n):
                    day = series.days[i]
                    yield FlatMeeting(
                        course_idx=c_idx,
                        component_idx=comp_idx,
                        course=course.name,
                        component_tag=comp.tag,
                        groups=tuple(series.audience),
                        day=day,
                        day_index=rank.get(day, WEEKDAY_RANK.get(day, 999)),
                        start_time=series.start_times[i],
                        room=series.rooms[i],
                        instructors=tuple(series.instructors[i]),
                    )


def _pairwise_overlap(intervals: list[tuple[datetime.time, datetime.time, str]], label: str) -> list[str]:
    out: list[str] = []
    for i, (s1, e1, l1) in enumerate(intervals):
        for s2, e2, l2 in intervals[i + 1 :]:
            if s1 < e2 and s2 < e1:
                out.append(
                    f"{label}: overlapping meetings {l1} "
                    f"[{s1.isoformat(timespec='minutes')},{e1.isoformat(timespec='minutes')}) vs "
                    f"{l2} [{s2.isoformat(timespec='minutes')},{e2.isoformat(timespec='minutes')})"
                )
    return out


def _group_size_map(cfg: ScheduleConfig) -> dict[str, int]:
    out: dict[str, int] = {}
    for g in cfg.students_groups:
        out[g.code] = max(len(g.students or []), int(g.estimated_size or 0))
    return out


def _group_students_map(cfg: ScheduleConfig) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for g in cfg.students_groups:
        for s in g.students:
            s = s.strip().lower()
            if s:
                out[g.code].add(s)
    return out


def _meeting_expected_students(comp_cfg: CourseConfig.Component, groups: tuple[str, ...], sizes: dict[str, int]) -> int:
    if comp_cfg.expected_enrollment is not None:
        return max(0, int(comp_cfg.expected_enrollment))
    return max(0, sum(sizes.get(g, 0) for g in groups))


def _back_to_back_lecture_tutorial_opportunities(cfg: ScheduleConfig) -> int:
    total = 0
    for course in cfg.courses:
        lecs = sum(1 for c in course.components if str(c.tag).lower() == "lec")
        tuts = sum(1 for c in course.components if str(c.tag).lower() == "tut")
        total += lecs * tuts
    return total


def _back_to_back_lecture_tutorial_scheduled(events: list[FlatMeeting]) -> int:
    tuts_by_start: dict[tuple[int, tuple[str, ...], str, datetime.time], set[int]] = defaultdict(set)
    satisfied_pairs: set[tuple[int, int, int]] = set()
    for e in events:
        tag = str(e.component_tag).lower()
        if tag == "tut":
            key = (e.course_idx, e.groups, e.day, e.start_time)
            tuts_by_start[key].add(e.component_idx)
    for e in events:
        if str(e.component_tag).lower() != "lec":
            continue
        tut_key = (e.course_idx, e.groups, e.day, _slot_end(e.start_time))
        for tut_component_idx in tuts_by_start.get(tut_key, set()):
            satisfied_pairs.add((e.course_idx, e.component_idx, tut_component_idx))
    return len(satisfied_pairs)


def _lec_tut_lab_rows(cfg: ScheduleConfig) -> list[tuple[int, int, int, int, str]]:
    selector_map = resolve_selector_map(cfg)
    rows: list[tuple[int, int, int, int, str]] = []
    for c_idx, course in enumerate(cfg.courses):
        lecs = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "lec"]
        tuts = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "tut"]
        labs = [(i, c) for i, c in enumerate(course.components) if str(c.tag).lower() == "lab"]
        if not tuts or len(lecs) != 1:
            continue
        lec_i, lec = lecs[0]
        lec_groups = set(expand_groups(lec.student_groups, selector_map))
        for tut_i, tut in tuts:
            tut_groups = set(expand_groups(tut.student_groups, selector_map))
            for lab_i, lab in labs:
                lab_groups = set(expand_groups(lab.student_groups, selector_map))
                for g in sorted(lec_groups & tut_groups & lab_groups):
                    rows.append((c_idx, lec_i, tut_i, lab_i, g))
    return rows


def _same_day_lec_tut_lab_opportunities(cfg: ScheduleConfig) -> int:
    total = 0
    for c_idx, lec_i, tut_i, lab_i, _g in _lec_tut_lab_rows(cfg):
        comps = cfg.courses[c_idx].components
        total += min(comps[lec_i].per_week, comps[tut_i].per_week, comps[lab_i].per_week)
    return total


def _same_day_lec_tut_lab_satisfied(events: list[FlatMeeting], cfg: ScheduleConfig) -> int:
    by_key: dict[tuple[int, str, str], dict[int, str]] = defaultdict(dict)
    for e in events:
        tag = str(e.component_tag).lower()
        if tag not in {"lec", "tut", "lab"}:
            continue
        for g in e.groups:
            by_key[(e.course_idx, g, e.day)][e.component_idx] = tag
    triples: dict[tuple[int, str], list[tuple[int, int, int]]] = defaultdict(list)
    for c, li, ti, bi, g in _lec_tut_lab_rows(cfg):
        triples[(c, g)].append((li, ti, bi))
    satisfied: set[tuple[int, int, int, int, str]] = set()
    for (c, g, _d), comp_map in by_key.items():
        for li, ti, bi in triples.get((c, g), []):
            if comp_map.get(li) == "lec" and comp_map.get(ti) == "tut" and comp_map.get(bi) == "lab":
                satisfied.add((c, li, ti, bi, g))
    return len(satisfied)


def _component_relation_allows_pair(cfg: ScheduleConfig, course_idx: int, early_idx: int, late_idx: int) -> bool:
    comps = cfg.courses[course_idx].components if 0 <= course_idx < len(cfg.courses) else []
    if early_idx < 0 or late_idx < 0 or early_idx >= len(comps) or late_idx >= len(comps):
        return False
    early_rel = comps[early_idx].relates_to
    late_rel = comps[late_idx].relates_to
    if early_rel is None and late_rel is None:
        return True
    early_set = set(early_rel if isinstance(early_rel, list) else [early_rel]) if early_rel is not None else set()
    late_set = set(late_rel if isinstance(late_rel, list) else [late_rel]) if late_rel is not None else set()
    return late_idx in early_set or early_idx in late_set


def _count_tag_before_tag(events: list[FlatMeeting], cfg: ScheduleConfig, early_tag: str, late_tag: str) -> int:
    by_course: dict[int, list[FlatMeeting]] = defaultdict(list)
    for e in events:
        by_course[e.course_idx].append(e)
    count = 0
    for c_idx, evs in by_course.items():
        early_events = [e for e in evs if str(e.component_tag).lower() == early_tag]
        late_events = [e for e in evs if str(e.component_tag).lower() == late_tag]
        for a in early_events:
            for b in late_events:
                if (a.day_index, a.start_time) >= (b.day_index, b.start_time):
                    continue
                if not _component_relation_allows_pair(cfg, c_idx, a.component_idx, b.component_idx):
                    continue
                if set(a.groups) & set(b.groups):
                    count += 1
    return count


def list_conflicts(result: SolveResult, cfg: ScheduleConfig | None = None) -> list[str]:
    if cfg is None or result.status not in ("OPTIMAL", "FEASIBLE") or not result.schedule.courses:
        return []
    events = list(_iter_flat_meetings(result.schedule, cfg))
    by_room: dict[tuple[str, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_group: dict[tuple[str, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_inst: dict[tuple[str, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)
    by_student: dict[tuple[str, str], list[tuple[datetime.time, datetime.time, str]]] = defaultdict(list)

    group_students = _group_students_map(cfg)
    student_groups: dict[str, set[str]] = defaultdict(set)
    for g, students in group_students.items():
        for student in students:
            student_groups[student].add(g)
    shared_students = {s for s, groups in student_groups.items() if len(groups) > 1}

    for e in events:
        start, end = e.start_time, _slot_end(e.start_time)
        label = f"{e.course}/{e.component_tag}"
        by_room[(e.day, e.room)].append((start, end, label))
        for g in e.groups:
            by_group[(e.day, g)].append((start, end, label))
        for inst in e.instructors:
            by_inst[(e.day, inst)].append((start, end, label))
        if shared_students:
            meeting_students: set[str] = set()
            for g in e.groups:
                meeting_students.update(group_students.get(g, set()))
            for student in meeting_students:
                if student in shared_students:
                    by_student[(e.day, student)].append((start, end, label))

    out: list[str] = []
    for k, v in by_room.items():
        out.extend(_pairwise_overlap(v, f"room {k}"))
    for k, v in by_group.items():
        out.extend(_pairwise_overlap(v, f"group {k}"))
    for k, v in by_inst.items():
        out.extend(_pairwise_overlap(v, f"instructor {k}"))
    for k, v in by_student.items():
        out.extend(_pairwise_overlap(v, f"student {k}"))
    return out


def _expected_audience_meeting_counts(cfg: ScheduleConfig) -> dict[tuple[str, str, tuple[str, ...]], int]:
    selector_map = resolve_selector_map(cfg)
    out: dict[tuple[str, str, tuple[str, ...]], int] = {}
    for course in cfg.courses:
        for comp in course.components:
            groups = expand_groups(comp.student_groups, selector_map)
            if not groups:
                continue
            audiences = [[g] for g in groups] if comp.per_group else [groups]
            for aud in audiences:
                key = (course.name, str(comp.tag), tuple(aud))
                out[key] = out.get(key, 0) + comp.per_week
    return out


def _actual_audience_meeting_counts(schedule: Schedule) -> dict[tuple[str, str, tuple[str, ...]], int]:
    out: dict[tuple[str, str, tuple[str, ...]], int] = {}
    for course in schedule.courses:
        for comp in course.components:
            for series in comp.sessions:
                key = (course.name, str(comp.tag), tuple(series.audience))
                out[key] = out.get(key, 0) + len(series.days)
    return out


def list_unsatisfied(result: SolveResult, cfg: ScheduleConfig) -> list[str]:
    expected = _expected_audience_meeting_counts(cfg)
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        return [f"solve status is {result.status!r} but config implies meetings for {len(expected)} keys"] if expected else []

    actual = _actual_audience_meeting_counts(result.schedule)
    issues: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        exp = expected.get(key)
        act = actual.get(key)
        if exp == act:
            continue
        if exp is None:
            issues.append(f"unexpected scheduled meetings for key={key!r}: actual={act}")
        elif act is None:
            issues.append(f"missing scheduled meetings for key={key!r}: expected={exp}, actual=0")
        else:
            issues.append(f"meeting count mismatch for key={key!r}: expected={exp}, actual={act}")
    return issues


def calculate_schedule_metrics(result: SolveResult, cfg: ScheduleConfig) -> ScheduleMetrics:
    events = list(_iter_flat_meetings(result.schedule, cfg)) if result.status in ("OPTIMAL", "FEASIBLE") else []
    group_sizes = _group_size_map(cfg)
    room_caps = {room.id: room.capacity for room in cfg.rooms}
    total_slots = len(teaching_days(cfg)) * len(cfg.term.time_slots)

    per_group_day_load: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_group_day_subjects: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    per_inst_day_load: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    saturday_event_count = 0
    sunday_event_count = 0
    total_weighted_group_hours = 0.0
    events_exceeding_room_capacity_count = 0
    events_with_room_much_larger_than_students_count = 0
    total_capacity_overflow_students = 0
    wasted_seat_hours = 0.0

    for e in events:
        event_hours = TIME_SLOT_DURATION.total_seconds() / 3600.0
        total_weighted_group_hours += event_hours * len(e.groups)
        if e.day == "Sat":
            saturday_event_count += 1
        if e.day == "Sun":
            sunday_event_count += 1
        for g in e.groups:
            per_group_day_load[g][e.day] += 1
            if WEEKDAY_RANK.get(e.day, 999) in WEEKDAYS_MON_SAT:
                per_group_day_subjects[g][e.day].add(e.course)
        for inst in e.instructors:
            per_inst_day_load[inst][e.day] += 1

        room_capacity = room_caps.get(e.room, 0)
        comp_cfg = (
            cfg.courses[e.course_idx].components[e.component_idx]
            if e.course_idx < len(cfg.courses) and e.component_idx < len(cfg.courses[e.course_idx].components)
            else None
        )
        expected_students = _meeting_expected_students(comp_cfg, e.groups, group_sizes) if comp_cfg is not None else 0
        wasted_seat_hours += max(0, room_capacity - expected_students) * event_hours

        allowed_capacity = math.ceil(room_capacity / LARGE_ROOM_EXCEEDING_GAP) if room_capacity > LARGE_ROOM_CAPACITY_THRESHOLD else room_capacity
        if expected_students > allowed_capacity:
            events_exceeding_room_capacity_count += 1
            total_capacity_overflow_students += max(0, expected_students - room_capacity)

        feasible_for_full = any(cap >= expected_students for cap in room_caps.values())
        required_capacity = max(1, expected_students if feasible_for_full else math.ceil(expected_students * 0.9))
        if room_capacity > required_capacity:
            oversize_pct = ((room_capacity - required_capacity) * 100) // max(1, required_capacity)
            if oversize_pct > ROOM_OVERSIZE_PCT_THRESHOLD:
                events_with_room_much_larger_than_students_count += 1

    core_course_idx = {i for i, c in enumerate(cfg.courses) if "core_course" in {str(t).lower() for t in c.course_tags}}
    core_events_per_group_day: dict[tuple[str, str], int] = defaultdict(int)
    core_subjects_per_group_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in events:
        if e.course_idx not in core_course_idx:
            continue
        for g in e.groups:
            core_events_per_group_day[(g, e.day)] += 1
            core_subjects_per_group_day[(g, e.day)].add(e.course)

    core_group_ids = {g for g, _ in core_events_per_group_day}
    bad_events_total = 0
    bad_distinct_total = 0
    groups_with_bad_events = 0
    groups_with_bad_distinct = 0
    for g in core_group_ids:
        gbad_events = 0
        gbad_distinct = 0
        days = {d for gg, d in core_events_per_group_day if gg == g}
        for d in days:
            if core_events_per_group_day[(g, d)] > BAD_DAY_EVENT_THRESHOLD:
                gbad_events += 1
            if len(core_subjects_per_group_day[(g, d)]) > BAD_DAY_DISTINCT_SUBJECTS_THRESHOLD:
                gbad_distinct += 1
        bad_events_total += gbad_events
        bad_distinct_total += gbad_distinct
        if gbad_events > 0:
            groups_with_bad_events += 1
        if gbad_distinct > 0:
            groups_with_bad_distinct += 1

    group_excess_total = 0
    group_excess_count = 0
    for day_load in per_group_day_load.values():
        weekly_events = sum(day_load.values())
        if weekly_events <= 0:
            continue
        active_days = sum(1 for n in day_load.values() if n > 0)
        min_active = max(1, math.ceil(weekly_events / BAD_DAY_EVENT_THRESHOLD))
        excess = max(0, active_days - min_active)
        group_excess_total += excess
        if excess > 0:
            group_excess_count += 1

    inst_excess_total = 0
    inst_excess_count = 0
    for day_load in per_inst_day_load.values():
        weekly_events = sum(day_load.values())
        if weekly_events <= 0:
            continue
        active_days = sum(1 for n in day_load.values() if n > 0)
        min_active = max(1, math.ceil(weekly_events / INSTRUCTOR_DAILY_EVENT_THRESHOLD))
        excess = max(0, active_days - min_active)
        inst_excess_total += excess
        if excess > 0:
            inst_excess_count += 1

    late_events = [e for e in events if _slot_end(e.start_time) > datetime.time(18, 0)]
    lec_tut_same_room_opportunities, lec_tut_same_room_count = _lec_tut_same_room_stats(events, cfg)
    (
        instructor_room_swaps_consecutive_opportunities,
        instructor_room_swaps_consecutive,
    ) = _instructor_room_swaps_consecutive(events, cfg.term.time_slots)
    back_to_back_lec_tut_opportunities = _back_to_back_lecture_tutorial_opportunities(cfg)
    back_to_back_lec_tut_scheduled = _back_to_back_lecture_tutorial_scheduled(events)
    same_day_lec_tut_lab_opportunities = _same_day_lec_tut_lab_opportunities(cfg)
    same_day_lec_tut_lab_satisfied = _same_day_lec_tut_lab_satisfied(events, cfg)

    return ScheduleMetrics(
        run_status=result.status,
        run_elapsed_seconds=result.stats.elapsed_seconds,
        hard_conflicts=tuple(list_conflicts(result, cfg)),
        hard_unsatisfied_constraints=tuple(list_unsatisfied(result, cfg)),
        ctx_total_events=len(events),
        ctx_total_timeslots_count=total_slots,
        ctx_total_weighted_group_hours=total_weighted_group_hours,
        ctx_total_core_course_groups_count=len(core_group_ids),
        quality_back_to_back_lec_tut_opportunities=back_to_back_lec_tut_opportunities,
        quality_back_to_back_lec_tut_scheduled=back_to_back_lec_tut_scheduled,
        quality_back_to_back_lec_tut_ratio=(
            back_to_back_lec_tut_scheduled / back_to_back_lec_tut_opportunities
            if back_to_back_lec_tut_opportunities > 0
            else 0.0
        ),
        quality_same_day_lec_tut_lab_opportunities=same_day_lec_tut_lab_opportunities,
        quality_same_day_lec_tut_lab_satisfied=same_day_lec_tut_lab_satisfied,
        quality_same_day_lec_tut_lab_ratio=(
            same_day_lec_tut_lab_satisfied / same_day_lec_tut_lab_opportunities
            if same_day_lec_tut_lab_opportunities > 0
            else 0.0
        ),
        quality_labs_before_tutorial_count=_count_tag_before_tag(events, cfg, "lab", "tut"),
        quality_labs_before_lecture_count=_count_tag_before_tag(events, cfg, "lab", "lec"),
        quality_tutorials_before_lecture_count=_count_tag_before_tag(events, cfg, "tut", "lec"),
        rooms_events_exceeding_capacity_count=events_exceeding_room_capacity_count,
        rooms_events_much_larger_than_students_count=events_with_room_much_larger_than_students_count,
        rooms_overflow_students_total=total_capacity_overflow_students,
        rooms_wasted_seat_hours=wasted_seat_hours,
        room_continuity_lec_tut_same_room_opportunities=lec_tut_same_room_opportunities,
        room_continuity_lec_tut_same_room_count=lec_tut_same_room_count,
        room_continuity_lec_tut_same_room_ratio=(
            lec_tut_same_room_count / lec_tut_same_room_opportunities if lec_tut_same_room_opportunities > 0 else 0.0
        ),
        room_continuity_instructor_room_swaps_consecutive_opportunities=instructor_room_swaps_consecutive_opportunities,
        room_continuity_instructor_room_swaps_consecutive=instructor_room_swaps_consecutive,
        room_continuity_instructor_room_swaps_consecutive_ratio=(
            instructor_room_swaps_consecutive / instructor_room_swaps_consecutive_opportunities
            if instructor_room_swaps_consecutive_opportunities > 0
            else 0.0
        ),
        hatred_global_saturday_event_count=saturday_event_count,
        hatred_global_sunday_event_count=sunday_event_count,
        hatred_global_late_events_count=len(late_events),
        hatred_global_late_events_lec_count=sum(1 for e in late_events if str(e.component_tag).lower() == "lec"),
        hatred_global_late_events_tut_count=sum(1 for e in late_events if str(e.component_tag).lower() == "tut"),
        hatred_global_late_events_lab_or_class_count=sum(
            1 for e in late_events if str(e.component_tag).lower() in {"lab", "class"}
        ),
        hatred_student_bad_days_events_total=bad_events_total,
        hatred_student_groups_with_bad_days_events=groups_with_bad_events,
        hatred_student_bad_days_distinct_total=bad_distinct_total,
        hatred_student_groups_with_bad_days_distinct=groups_with_bad_distinct,
        hatred_student_total_active_days_excess_groups=group_excess_total,
        hatred_student_groups_with_excess_day_count=group_excess_count,
        hatred_instructor_total_active_days_excess=inst_excess_total,
        hatred_instructor_with_excess_day_count=inst_excess_count,
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


def _latest_results_output_path(results_dir: Path) -> Path:
    if not results_dir.exists() or not results_dir.is_dir():
        raise FileNotFoundError(f"results directory not found: {results_dir}")

    latest_dir: Path | None = None
    latest_mtime = -1.0
    for child in results_dir.iterdir():
        if not child.is_dir():
            continue
        mtime = child.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_dir = child

    if latest_dir is None:
        raise FileNotFoundError(f"no run directories found under: {results_dir}")

    output_path = latest_dir / "output.yaml"
    if not output_path.exists():
        raise FileNotFoundError(f"latest run directory has no output.yaml: {latest_dir}")
    return output_path


def _resolve_solution_path(solution_path: Path | None) -> Path:
    if solution_path is not None:
        return solution_path
    project_root = Path(__file__).resolve().parent
    return _latest_results_output_path(project_root / "results")


def _print_human_report(metrics: ScheduleMetrics) -> None:
    print("=== Schedule Metrics ===")
    print("Context / dataset:")
    print(f"- solution status: {metrics.run_status}")
    print(f"- elapsed seconds: {metrics.run_elapsed_seconds}")
    print(f"- total scheduled events: {metrics.ctx_total_events}")
    print(f"- total available timeslots (teaching days x day slots): {metrics.ctx_total_timeslots_count}")
    print(f"- total meeting hours weighted by group count: {metrics.ctx_total_weighted_group_hours:.1f}")
    print(f"- total core-course groups count: {metrics.ctx_total_core_course_groups_count}")
    print("---")
    print("Metrics:")
    print(f"- hard conflicts (should be 0): {len(metrics.hard_conflicts)}")
    print(f"- unsatisfied constraints (should be 0): {len(metrics.hard_unsatisfied_constraints)}")
    print(
        f"- back-to-back lec->tut coverage (higher is better): "
        f"{metrics.quality_back_to_back_lec_tut_scheduled}/{metrics.quality_back_to_back_lec_tut_opportunities}={metrics.quality_back_to_back_lec_tut_ratio:.3f}"
    )
    print(
        f"- lec+tut+lab on same-day coverage (higher is better): "
        f"{metrics.quality_same_day_lec_tut_lab_satisfied}/{metrics.quality_same_day_lec_tut_lab_opportunities}={metrics.quality_same_day_lec_tut_lab_ratio:.3f}"
    )
    print(
        "- wrong component order counts (lower is better):\n"
        f"  - labs-before-tutorial={metrics.quality_labs_before_tutorial_count}\n"
        f"  - labs-before-lecture={metrics.quality_labs_before_lecture_count}\n"
        f"  - tutorials-before-lecture={metrics.quality_tutorials_before_lecture_count}"
    )
    print(
        "- room capacity violations (lower is better):\n"
        f"  - undersized-room events={metrics.rooms_events_exceeding_capacity_count}\n"
        f"  - oversized-room events={metrics.rooms_events_much_larger_than_students_count}\n"
        f"  - overflow students={metrics.rooms_overflow_students_total}\n"
        f"  - space inefficiency: wasted seats x hours={metrics.rooms_wasted_seat_hours:.1f}"
    )
    print(
        "- room continuity:\n"
        f"  - lec->tut same room (higher is better)={metrics.room_continuity_lec_tut_same_room_count}/{metrics.room_continuity_lec_tut_same_room_opportunities}={metrics.room_continuity_lec_tut_same_room_ratio:.3f}\n"
        f"  - instructor room swaps on consecutive slots (lower is better)={metrics.room_continuity_instructor_room_swaps_consecutive}/{metrics.room_continuity_instructor_room_swaps_consecutive_opportunities}"
        f"={metrics.room_continuity_instructor_room_swaps_consecutive_ratio:.3f}"
    )
    print(
        "- global hatred:\n"
        f"  - saturday events={metrics.hatred_global_saturday_event_count}\n"
        f"  - sunday events={metrics.hatred_global_sunday_event_count}\n"
        f"  - late events (>18:00) all={metrics.hatred_global_late_events_count}\n"
        f"  - late lec (>18:00)={metrics.hatred_global_late_events_lec_count}\n"
        f"  - late tut (>18:00)={metrics.hatred_global_late_events_tut_count}\n"
        f"  - late lab/class (>18:00)={metrics.hatred_global_late_events_lab_or_class_count}"
    )
    print(
        "- student hatred:\n"
        f"  - bad days when more than {BAD_DAY_EVENT_THRESHOLD} events={metrics.hatred_student_bad_days_events_total}\n"
        f"  - groups with >=1 bad_day by events={metrics.hatred_student_groups_with_bad_days_events}\n"
        f"  - bad days when more than {BAD_DAY_DISTINCT_SUBJECTS_THRESHOLD} distinct subjects={metrics.hatred_student_bad_days_distinct_total}\n"
        f"  - groups with >=1 bad_day by distinct subjects={metrics.hatred_student_groups_with_bad_days_distinct}\n"
        f"  - total excess active days (groups)={metrics.hatred_student_total_active_days_excess_groups}\n"
        f"  - groups with >=1 excess day={metrics.hatred_student_groups_with_excess_day_count}"
    )
    print(
        "- instructor hatred:\n"
        f"  - total excess active days (instructors)={metrics.hatred_instructor_total_active_days_excess}\n"
        f"  - instructors with >=1 excess day={metrics.hatred_instructor_with_excess_day_count}"
    )
    print("======\n")


def _print_json_report(metrics: ScheduleMetrics) -> None:
    print(json.dumps(asdict(metrics), indent=2, sort_keys=True, default=str))


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Compute schedule metrics from config and solution YAML files.")
    parser.add_argument("--config", required=True, type=Path, help="Path to schedule config YAML.")
    parser.add_argument(
        "--solution",
        type=Path,
        help="Path to solver output YAML. If omitted, uses latest results/*/output.yaml.",
    )
    parser.add_argument("--json", action="store_true", help="Print metrics as JSON.")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    solution_path = _resolve_solution_path(args.solution)
    print(f"Using solution: {solution_path.relative_to(Path.cwd())}", file=sys.stderr)
    result = _load_solution(solution_path)
    metrics = calculate_schedule_metrics(result, cfg)
    if args.json:
        _print_json_report(metrics)
    else:
        _print_human_report(metrics)


if __name__ == "__main__":
    cli_main()
