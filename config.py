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
    starting_day: str = "Mon"
    "Starting day of the week (for example, Mon)"
    time_slots: list[datetime.time] = [
        datetime.time(9, 0), # 09:00-10:30
        datetime.time(10, 40), # 10:40-12:10
        datetime.time(12, 40), # 12:40-14:10
        datetime.time(14, 20), # 14:20-15:50 
        datetime.time(16, 00), # 16:00-17:30
        datetime.time(17, 40), # 17:40-19:10
        datetime.time(19, 20), # 19:20-20:50
    ]
    "Slot start times; YAML/JSON may use ``HH:MM`` strings, which are parsed to time"


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


class SectionConfig(SettingBaseModel):
    class SectionProgram(SettingBaseModel):
        class ProgramTrack(SettingBaseModel):
            code: str
            "Track identifier"
            name: str
            "Track display name"
            kind: Literal["track", "english_program"] | str | None = None
            "Track kind marker"
            groups: list[str] = []
            "Track groups as plain group codes"

        code: str
        "Program identifier"
        name: str
        "Program display name"
        kind: Literal["degree_year", "english_program", "elective_bucket"] | str | None = None
        "Program kind marker"
        degree: str | None = None
        "Optional degree marker (for example, bs/ms/phd)"
        language: Literal["en", "ru"] | None = None
        "Program language marker (en/ru)"
        year: int | None = None
        "Program year"
        applies_to: list[str] = []
        "Optional list of entity codes this program applies to (for example, [BS_Y1_EN, BS_Y1_RU])"
        tracks: list[ProgramTrack] = []
        "Program tracks (optional wrapper when groups are split by track)"
        groups: list[str] = []
        "Program-level groups when tracks are not used (for example, elective bucket ids)"


    code: str
    "Section identifier"
    name: str
    "Section display name"
    kind: Literal["core", "english", "electives"] | str | None = None
    "Section kind marker (for example, core/english/electives)"
    programs: list[SectionProgram] = []
    "Programs inside the section"


class StudentsGroups(SettingBaseModel):
    code: str
    "Student entity code (group/program/selector id)"
    kind: str
    "Distribution kind (for example, core/english/elective)"
    name: str | None = None
    "Optional display name"
    estimated_size: int | None = None
    "Expected student count"
    students: list[str] = []
    "Optional explicit student membership list"


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
        Who attends: each entry is a group id or an ``@`` selector from ``sections`` hierarchy (union if several).

        Examples:
        - ``[@BS_Y1_EN]`` — whole program
        - ``[@MS_Y1/AIDE]`` — one track
        - ``[@BS_Y2_EN/Software Development, @BS_Y2_EN/Cybersecurity]`` — union of tracks
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
    sections: list[SectionConfig] = []
    "Section-based hierarchy from dtsn.yaml"
    students_groups: list[StudentsGroups] = []
    "Student groups entries"
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
    for section in cfg.sections:
        for program in section.programs:
            program_groups: set[str] = set(program.groups)
            for track in program.tracks:
                if track.groups:
                    g = set(track.groups)
                    selector_map[f"@{program.code}/{track.name}"] = g
                    program_groups.update(g)
            if program_groups:
                selector_map[f"@{program.code}"] = program_groups
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
