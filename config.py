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
We often care about balance of daily workload across instructors and groups, 
and their availability (preferred time-slots).
Additionaly, some teaching assistant are study on master or bachelor program 
in the same university so their availability can depends on the schedule itself. 

============================================================
Scheduling Objectives (Soft Preferences)
============================================================
In addition to hard feasibility constraints (no overlaps, room capacity,
required weekly meetings), the timetable optimization should prefer balanced
student load across weekdays:

* Keep per-group daily distinct subjects close to **2-3** where possible.
* Keep per-group daily meeting load close to **4-5 meetings/day**
  (about **6-7.5 hours**, with each meeting = 90 minutes), and avoid
  overload patterns such as 6-7+ meetings/day unless unavoidable.
* Avoid Saturday classes when possible.
* Try to keep lecture and tutorial in the morning or afternoon, not in the evening.
* Try to avoid events in late evening and weekends.

These are optimization targets, not strict hard constraints: feasibility and
curriculum requirements still take precedence.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SettingBaseModel(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid", populate_by_name=True)


class TermConfig(SettingBaseModel):
    class DateRange(SettingBaseModel):
        start_date: datetime.date
        "Inclusive range start date in ISO format (YYYY-MM-DD)"
        end_date: datetime.date
        "Inclusive range end date in ISO format (YYYY-MM-DD)"

    name: str
    "Academic term name (for example, Fall 2025)"
    semester: DateRange
    "Single teaching period (start and end dates inclusive)"
    days: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    "Working days used by the scheduler (for example, Mon..Sat)"
    time_slots: list[datetime.time] = [
        datetime.time(9, 0),
        datetime.time(10, 30),
        datetime.time(12, 10),
        datetime.time(14, 0),
        datetime.time(15, 30),
        datetime.time(17, 10),
        datetime.time(18, 40),
    ]
    "Slot start times; YAML/JSON may use ``HH:MM`` or ``HH:MM:SS`` strings, which are parsed to time"


class RoomConfig(SettingBaseModel):
    id: str
    "Room identifier used in schedule output"
    name: str
    "Human-readable room name"
    capacity: int
    "Maximum room capacity"


class InstructorConfig(SettingBaseModel):
    id: str
    "Instructor unique identifier"
    name: str
    "Instructor display name"
    role: str | None = None
    "Instructor role (for example, professor or teaching_assistant)"


class ProgramConfig(SettingBaseModel):
    class ProgramTrack(SettingBaseModel):
        name: str
        "Track name inside a program"
        groups: list[str] = []
        "Track groups as direct group ids"

    id: str
    "Program identifier (for example, bachelor_1)"
    name: str
    "Program display name"
    year: int | None = None
    "Program year (for example, 1 for bachelor 1st year, 2 for bachelor 2nd year, etc.)"
    language: Literal["en", "ru"] | None = None
    "Program language marker (en/ru)"
    tracks: list[ProgramTrack] = []
    "Hierarchical program tracks"


class StudentGroupsBucket(SettingBaseModel):
    class StudentGroupRef(SettingBaseModel):
        id: str
        "Student group identifier"
        name: str | None = None
        "Optional display name"
        estimated_size: int | None = None
        "Expected student count"
        students: list[str] = []
        "Optional explicit student membership list"

    academic: list[StudentGroupRef] = []
    "Academic groups bucket"
    english: list[StudentGroupRef] = []
    "English/cross-program groups bucket"
    elective: list[StudentGroupRef] = []
    "Elective-generated groups bucket"


type CommonCourseTags = Literal["core_course", "elective", "english"]
type CommonCourseClassTags = Literal["lec", "tut", "lab", "class"]

class CourseConfig(SettingBaseModel):
    class Component(SettingBaseModel):
        tag: CommonCourseClassTags | str
        "Class tag (for example, lec, tut, lab, class)"
        per_week: int = 1
        "Number of weekly meetings"
        instructor_pool: list[str | list[str]] = []
        """
        Candidate instructors; nested list means co-teaching set
        
        Example:
        - [nikolay_kudasov] # only nikolay_kudasov can teach this class
        - [nikolay_kudasov, anatoliy_baskakov] # any of them can teach this class
        - [[nikolay_kudasov, anatoliy_baskakov], [alexey_stepanov]] # any of them can teach this class, either nikolay_kudasov with anatoliy_baskakov co-teaching, or alexey_stepanov teaching alone
        """
        student_groups: list[str] = []
        """
        Who attends: each entry is a group id or an ``@`` selector from ``programs`` (union if several).

        Examples:
        - ``[@bachelor_1_en]`` — whole program
        - ``[@master_1/AIDE]`` — one track
        - ``[@bachelor_2_en/Software Development, @bachelor_2_en/Cybersecurity]`` — union of tracks
        - ``[ENG-eap1]`` or ``[B22-CBS-02]`` — direct group id
        """
        expected_enrollment: int | None = None
        "Expected enrollment used for room sizing, defer from sum(student_group.size for groups in student_groups) if None"
        per_group: bool = False
        "Whether one class instance should be created per group, if True, then one class instance will be created for each group in student_groups. It is useful for lab classes where each group needs a separate meeting. If false, then one class instance (meeting) will be created for all groups in student_groups, so they will be effectively in same time, same room, same instructor."
        relates_to: int | list[int] | None = None
        "Optional component index or list of indices that this component depends on for same-day/order/back-to-back preferences."

    name: str
    "Course name"
    course_tags: list[CommonCourseTags | str] = []
    "Course tags (for example, core_course / elective / english)"
    components: list[Component]
    "Course subparts (lec/tut/lab/…) to schedule"


class ScheduleConfig(SettingBaseModel):
    schema_: str | None = Field(None, alias="$schema")
    "Optional JSON schema reference"
    term: TermConfig
    "Term-level configuration"
    rooms: list[RoomConfig] = []
    "Available rooms"
    instructors: list[InstructorConfig] = []
    "Available instructors"
    programs: dict[str, list[ProgramConfig]] = {}
    "Programs grouped by level (for example, bachelor/master/phd/english)"
    student_groups: StudentGroupsBucket
    "Student groups grouped by bucket"
    courses: list[CourseConfig] = []
    "All courses to schedule"

    @classmethod
    def from_yaml(cls, path: Path) -> ScheduleConfig:
        with open(path, encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
        return cls.model_validate(yaml_config)

    @classmethod
    def save_schema(cls, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            schema = {
                "$schema": "https://json-schema.org/draft-07/schema",
                **cls.model_json_schema(),
            }
            yaml.dump(schema, f, sort_keys=False)


def resolve_selector_map(cfg: ScheduleConfig) -> dict[str, set[str]]:
    selector_map: dict[str, set[str]] = {}
    for level in cfg.programs.values():
        for program in level:
            program_groups: set[str] = set()
            for track in program.tracks:
                if track.groups:
                    g = set(track.groups)
                    selector_map[f"@{program.id}/{track.name}"] = g
                    program_groups.update(g)
            if program_groups:
                selector_map[f"@{program.id}"] = program_groups
    return selector_map


def expand_groups(tokens: list[str], selector_map: dict[str, set[str]]) -> list[str]:
    out: set[str] = set()
    for t in tokens:
        if t in selector_map:
            out.update(selector_map[t])
        else:
            out.add(t)
    return sorted(out)


ScheduleConfig.model_rebuild()
