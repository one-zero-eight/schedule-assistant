# IU Course Scheduler — Migration from UniTime to Python + OR-Tools

## Goal

Build a Python CLI tool that replaces UniTime's course timetabling functionality for Innopolis University. The tool reads a YAML config describing courses, rooms, staff, curricula, and constraints, and outputs a YAML schedule.

## Context

UniTime is a Java-based university scheduling system using an Iterative Forward Search (IFS) constraint solver. The full formulation is documented in `docs/REVERSE.md`. We don't need the entire UniTime feature set — only **course scheduling** for a single campus (Innopolis University) with the domain-specific structure described below.

### Innopolis University Scheduling Domain

**Academic structure:**
- 3 trimesters per year: Fall, Spring, Summer
- Each trimester is split into 2 teaching blocks: `block_1`, `block_2`
- 4 bachelor years + master programs (BSc and MSc tracks)
- Students belong to groups (e.g. `B22-CBS-02` = bachelor 2022, Cybersecurity track, group 2)
- Groups are not always disjoint: one student can be in an academic group and also in a shared cross-group stream (e.g., English level group)
- Years 1–2 are shared across all BSc tracks (before track distribution), distribution performed in 2nd part of year 2; year 3+ is track-specific. Also we have programs that specialized in the year 1, also having English and Russian programs (different curricula between them).
- BSc tracks: Software Development, Cybersecurity, Data Science, AI, Robotics, Game Development
- MSc programs: SE, AIDE, Robotics, Technological Entrepreneurship, SNE

**Course structure — each subject typically has:**
- **Lecture** — 1 class/week, all groups enrolled in the subject attend simultaneously, taught by a professor
- **Tutorial** — 1 class/week, same audience as lecture (all groups), often immediately after lecture, same or different instructor
- **Lab** — 1 class/week, per-group (each group separately), conducted by a Teaching Assistant
Note that we have exceptions for this rule.

**Electives:**
- In certain semesters, students choose Tech and/or Hum electives
- Students rank top-5 preferences, then are assigned to one elective
- Different programs can share the same elective
- Some electives have several groups in it, 1 lecture for all groups + 2 labs for each group during week taugh by one instructor.

**Rooms:**
- All in a single building (Innopolis University), no travel distance concerns
- Capacities range from 16 to 312 seats
- See `rooms.xml` for the full list (30 rooms)
- Some lessons conducted in online mode, in this case we need to specify the online room.

**Staff:**
- Professors, Associate Professors, Assistant Professors, Instructors, and Teaching Assistants
- See `staff.xml` for the full list (~130 people)

## Cases and Caveats

Use this section as a living list of scenarios that affect modeling, input schema, or solver behavior.

> Use plain text for each case:
>
> - `Title`: short name of caveat
- `Status`: draft | agreed | implemented
> - `Priority`: high | medium | low
> - `Context`: what real-world situation triggers this case
> - `Expected behavior`: what scheduler must do
> - `Solver impact`: hard constraint / soft penalty / preprocessing rule
> - `Open questions`: unresolved details

### Baseline full-semester all-core L-T-L schedule
- `Status`: draft
- `Priority`: high
- `Context`: We build the first term schedule with all programs starting at the same time, all subjects taught for the full semester, no electives, and each subject modeled as lecture + tutorial + lab. Input still includes the full university dataset (all groups and all subjects).
- `Expected behavior`: Produce a feasible complete schedule under the standard model (lecture/tutorial for full audience, labs per group) without elective branching.
- `Solver impact`: preprocessing rule (normalize to default subject structure) + hard constraints (no overlap, room capacity, allowed slots).
- `Open questions`: Whether any non-standard subjects in the real dataset should be excluded from this baseline or force explicit exceptions.

### Electives in two steps: estimated, then exact distribution
- `Status`: draft
- `Priority`: high
- `Context`: Term contains both core subjects and electives. Scheduling must run in two phases: (1) before exact elective allocation is known, with only expected enrollment per elective; (2) after exact student distribution file is provided.
- `Expected behavior`: Step 1 builds a feasible draft timetable for core + electives using estimated enrollment and group demand. Step 2 loads exact student-to-elective assignments, revalidates/fixes clashes, and outputs the final timetable with student-level consistency.
- `Solver impact`: preprocessing rule (phase 1: derive estimated elective groups/capacity, phase 2: load exact mapping) + hard constraints (no student overlap by membership in phase 2) + room-capacity checks in both phases.
- `Open questions`: Allowed change budget between step 1 and step 2 (full re-solve vs minimal perturbation), and canonical schema of the uploaded distribution file.

### Russian-track program with shifted study dates
- `Status`: draft
- `Priority`: high
- `Context`: Russian program starts and ends on dates different from English bachelor programs in the same term.
- `Expected behavior`: Classes for Russian-track groups are only placed within their own active date window while still respecting shared resources (rooms/instructors) with other programs.
- `Solver impact`: hard constraint (program availability window per group/program) + preprocessing (derive eligible dates per class instance).
- `Open questions`: Whether shared courses between Russian and English tracks should use intersection of date windows or be split into separate class instances.

### English-stream groups across academic groups
- `Status`: draft
- `Priority`: high
- `Context`: English groups are cross-cutting (students belong both to academic groups and English groups). Real student distribution for English groups will be provided.
- `Expected behavior`: Schedule English classes for stream groups and enforce no clashes with each student's academic-group classes.
- `Solver impact`: hard constraint (no student overlap by membership) + preprocessing (build multi-group student membership graph).
- `Open questions`: Whether English streams always run in a fixed block/period and whether level changes mid-term are possible.

### Burst-week intensive course delivery
- `Status`: draft
- `Priority`: medium
- `Context`: Some subjects are taught in compressed mode (for example, several meetings per week during one or two specific weeks).
- `Expected behavior`: Allow high-frequency placement only inside configured active date ranges while still obeying all overlap and room constraints.
- `Solver impact`: hard constraint (active date ranges) + preprocessing (candidate-slot pruning by burst window).
- `Open questions`: Whether burst classes can violate regular max-classes-per-day limits for groups/instructors.

### Manual editing and pinned classes
- `Status`: draft
- `Priority`: high
- `Context`: Scheduler admin manually fixes some lessons (date/time/room/instructor) before or after solve.
- `Expected behavior`: Treat pinned lessons as immutable hard assignments and schedule all remaining lessons around them without breaking feasibility.
- `Solver impact`: hard constraint (fixed placement variables) + preprocessing (lock manually edited entries before optimization).
- `Open questions`: Conflict-handling policy when a manual edit makes the instance infeasible (reject edit vs allow unresolved classes).

### One lesson shared by two programs
- `Status`: draft
- `Priority`: medium
- `Context`: A single class instance is attended by groups from different programs (joint teaching).
- `Expected behavior`: Create one shared meeting with combined enrollment, one room, one time, and all participating groups attached.
- `Solver impact`: preprocessing rule (merge group audience for shared class instance) + hard constraints (room capacity, group/instructor no-overlap).
- `Open questions`: Whether shared lessons may include programs with different slot grids or date windows, and how to resolve incompatibility.

### Teaching assistant is also a student
- `Status`: draft
- `Priority`: high
- `Context`: Same person appears both as instructor (usually TA) and as a student in one or more groups.
- `Expected behavior`: Never place a TA teaching slot in conflict with any class where that person is enrolled as a student.
- `Solver impact`: hard constraint (person-level no-overlap across roles) + preprocessing (identity resolution between instructor IDs and student IDs).
- `Open questions`: Canonical identity key (email vs internal ID) and whether this rule applies to all staff roles or only TAs.


## Parsing Existing Google Sheets Schedule

Use existing university schedules in Google Sheets as both a bootstrap source and a benchmark target.

### Why

- Bootstrap initial YAML inputs (subjects, student groups, instructors) from current operational schedule data.
- Compare solver output with real schedule quality and compute a score/report for validation.
- Primary source strategy: parse raw Google Sheets directly or use normalized `.ics` \ `json` output from existing parsers (`one-zero-eight/parsers`) as canonical import input. UPDATE: You could use `core-courses-lessons-spring-2026.json` that I exported from the existing parser.

### v1 workflow

1. Export source sheets to CSV (or fetch via Google Sheets API).
2. Parse and normalize rows into internal canonical fields: `date`, `start_time`, `end_time`, `course`, `class_tag`, `instructor`, `room`, `groups`, `mode`.
3. Resolve aliases and typos (group names, room names, instructor names) through mapping tables.
4. Generate:
   - bootstrap artifacts for solver input (`courses`, optional `pinned`, and membership hints);
   - normalized "real schedule" dataset for comparison.
5. Run solver and compute comparison metrics against normalized real schedule.

### Comparison and scoring

- Feasibility checks: hard-constraint violations in generated schedule.
- Similarity checks vs real schedule: time difference, room difference, instructor difference, group coverage difference.
- Quality metrics: room utilization, group gaps, instructor daily load, unassigned classes.
- Output: machine-readable score file + short human-readable report.

### Open points

- Whether comparison should be strict equality or tolerance-based (for example, same day but different slot).
- Whether score should prioritize feasibility first and treat similarity as secondary.

## Scope for v1

### Input: YAML config file

The canonical input shape is defined by Pydantic models in [`config.py`](../config.py) (`ScheduleConfig` and nested types). Load with `ScheduleConfig.from_yaml(path)`.

Top-level fields:

| Field | Purpose |
| --- | --- |
| `$schema` | Optional JSON Schema reference (`alias="$schema"`). |
| `term` | Term name, inclusive `semester` date range, `days`, `time_slots`. |
| `rooms` | `id`, `name`, `capacity` per room. |
| `instructors` | `id`, `name`, optional `role`. |
| `programs` | Map of level → list of programs (`id`, `name`, optional `language` `en`\|`ru`, `tracks` with `name` and `groups`). |
| `student_groups` | Three buckets: `academic`, `english`, `elective`; each entry has `id`, optional `name`, `estimated_size`, `students`. |
| `courses` | List of courses: `name`, `course_tags`, `classes`. |
| `constraints` | Global `weights` and per-tag `course_constraints` (see below). |

**`term`** — one inclusive teaching period and the working-day grid:

- `semester.start_date` / `semester.end_date` — ISO dates.
- `days` — default `["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]`.
- `time_slots` — default slot start times `["09:00", "10:30", "12:10", "14:00", "15:30", "17:10", "18:40"]`.

**`courses[].classes[]` (`CourseClass`)** — each class part to schedule:

- `tag` — e.g. `lec`, `tut`, `lab`.
- `required_count` — integer or `"half"` / `"full"` (`"full"` default).
- `per_week`, `duration_slots` — weekly frequency and length in slot units.
- `instructor_pool` — list of instructor ids; a nested list is one co-teaching set. Example: `[[a, b], [c]]` means either `a`+`b` together or `c` alone.
- `student_groups` — audience group ids (or selectors resolved upstream).
- `expected_enrollment` — optional; room sizing if set.
- `per_group` — if true, one scheduled instance per entry in `student_groups`.

**`constraints.weights`** — map each `ConstraintType` id to a positive float or `"hard"`. Defaults in code include: `no_room_overlap`, `no_instructor_overlap`, `no_group_overlap`, `no_student_overlap_by_membership`, `allowed_slots` as `"hard"`; `room_capacity` as `3.0`; `minimize_gaps_for_groups` `5.0`; `instructor_max_per_day` `8.0`; `time_preference` `1.0`; `too_big_room` `0.5`.

**`constraints.course_constraints`** — map `course_tags` value (or `null` for the default bucket) to `CourseConstraintType` → weight or `"hard"`. Built-in tags include `core_course`, `english`, `elective`; extra string tags are allowed. The model default applies `same_day`, `ordered`, `back_to_back`, `same_room` to the `null` bucket for courses that do not match a more specific tag.

Representative YAML (structure only; not every field shown). Optional top-level `$schema` may point at a generated JSON Schema from `ScheduleConfig.save_schema`.

```yaml
term:
  name: "Fall 2025"
  semester:
    start_date: "2025-09-01"
    end_date: "2025-12-21"
  days: [Mon, Tue, Wed, Thu, Fri, Sat]
  time_slots: ["09:00", "10:30", "12:10", "14:00", "15:30", "17:10", "18:40"]

rooms:
  - id: "301"
    name: "Lecture Room 301"
    capacity: 24
  - id: "105"
    name: "Lecture Room 105 (West)"
    capacity: 240

instructors:
  - id: "REDACTED_EMAIL"
    name: "Oleg Kiselev"
    role: professor

programs:
  bachelor:
    - id: bachelor_1
      name: "1st Year Bachelor"
      language: en
      tracks: []
    - id: bachelor_3
      name: "3rd Year Bachelor"
      tracks:
        - name: "Cybersecurity"
          groups: ["B22-CBS-01", "B22-CBS-02"]
  master:
    - id: master_1
      name: "1st Year Master"
      tracks:
        - name: "Software Engineering"
          groups: ["M24-SE-01"]

student_groups:
  academic:
    - id: "B24-01"
      estimated_size: 30
      students: []
  english:
    - id: "ENG-A2-01"
      estimated_size: 14
      students: []
  elective: []

courses:
  - name: "Analytical Geometry and Linear Algebra I"
    course_tags: [core_course]
    classes:
      - tag: lec
        required_count: 16
        per_week: 1
        duration_slots: 1
        instructor_pool: ["REDACTED_EMAIL"]
        student_groups: ["B24-01", "B24-02"]
      - tag: tut
        required_count: 16
        per_week: 1
        duration_slots: 1
        instructor_pool: ["REDACTED_EMAIL"]
        student_groups: ["B24-01", "B24-02"]
      - tag: lab
        required_count: 16
        per_week: 1
        duration_slots: 1
        per_group: true
        instructor_pool: ["REDACTED_EMAIL", "REDACTED_EMAIL"]
        student_groups: ["B24-01", "B24-02"]

constraints:
  weights:
    no_room_overlap: hard
    no_instructor_overlap: hard
    no_group_overlap: hard
    no_student_overlap_by_membership: hard
    allowed_slots: hard
    room_capacity: 3.0
    minimize_gaps_for_groups: 5.0
    instructor_max_per_day: 8.0
    time_preference: 1.0
    too_big_room: 0.5
  course_constraints:
    core_course: {}
    english: {}
    elective: {}
    null:
      same_day: hard
      ordered: hard
      back_to_back: 10.0
      same_room: 1.0
```

Roadmap items not in `ScheduleConfig` yet (domain doc may still discuss them): trimester blocks (`block_1` / `block_2`), per-program date windows, per-instructor slot preferences and caps in YAML, per-class room features, `pinned` placements, and per-course `class_constraints` lists. Prefer extending `config.py` first, then this document.

### Output: YAML schedule file

Written by `main.py` via `yaml.safe_dump` (default path `--output`, usually `schedule.yaml`). Not modeled by `ScheduleConfig`. A concrete nested example is [`../output.yaml`](../output.yaml).

**Top level**

| Field | Meaning |
| --- | --- |
| `metadata` | `status` (CP-SAT status name), `num_meetings`, `num_slots` |
| `schedule` | `{ courses: [...] }` — mirrors input courses with placement details |

**`schedule.courses[]`**

| Field | Meaning |
| --- | --- |
| `name` | Course title (same as input) |
| `course_tags` | Tags from config |
| `classes` | One entry per class part (`CourseClass`) that got a solution row |

**`schedule.courses[].classes[]`**

| Field | Meaning |
| --- | --- |
| `tag` | Class part id (`lec`, `tut`, `lab`, …) |
| `student_groups` | Audience from config |
| `instructor_pool` | Serialized pool (strings or nested lists for co-teaching) |
| `instances` | List of placement bundles (see below) |

**`instances[]` (per bundle)** — parallel arrays for each scheduled meeting in that bundle:

| Field | Meaning |
| --- | --- |
| `groups` | Group id(s) for this bundle |
| `instructors` | List of per-meeting instructor assignments; each element is a list of ids (co-teaching = multiple ids in that inner list) |
| `dates` | ISO date strings |
| `start_times` | Slot start times (`HH:MM`), aligned with `term.time_slots` |
| `rooms` | Room ids per meeting |

With `per_group: true` and several `student_groups`, the solver emits **one instance object per group** (each with its own `groups`, `dates`, …). Index *i* across `dates`, `start_times`, `rooms`, and `instructors` is one meeting. The current `main.py` usually writes a single meeting per solved row (length-1 lists); [`output.yaml`](../output.yaml) also shows longer parallel lists as a richer example.

```yaml
metadata:
  status: OPTIMAL
  num_meetings: 42
  num_slots: 210

schedule:
  courses:
    - name: Advanced Compiler Construction and Program Analysis
      course_tags:
        - core_course
      classes:
        - tag: lec
          student_groups: ["@bachelor_3_en/Software Development"]
          instructor_pool: [nikolay_kudasov]
          instances:
            - groups: ["@bachelor_3_en/Software Development"]
              instructors: [[nikolay_kudasov], [nikolay_kudasov], [nikolay_kudasov]]
              dates: ["2026-01-19", "2026-01-20", "2026-01-21"]
              start_times: ["14:00", "14:00", "14:00"]
              rooms: ["108", "108", "108"]
        - tag: lab
          student_groups: ["B24-01"]
          instructor_pool: [alexey_stepanov, anatoliy_baskakov]
          instances:
            - groups: ["B24-01"]
              instructors: [[alexey_stepanov]]
              dates: ["2026-01-19"]
              start_times: ["12:10"]
              rooms: ["461"]
            - groups: ["B24-02"]
              instructors: [[anatoliy_baskakov]]
              dates: ["2026-01-19"]
              start_times: ["12:10", "12:10", "12:10"]
              rooms: ["461"]
```

There is no `unassigned` / `warnings` block in the current writer; extend `solve_schedule` if you need them.

## Hard Constraints to Implement (v1)

These map from UniTime's constraint model (see `docs/REVERSE.md` §3.3, §4) to OR-Tools CP-SAT. In [`config.py`](../config.py), each has a `ConstraintType` string id and a default weight in `constraints.weights` (either `"hard"` or a numeric soft weight).

| # | Constraint | Config id | UniTime Equivalent | OR-Tools Approach |
|---|---|---|---|---|
| 1 | **Room no-overlap** | `no_room_overlap` | `RoomConstraint` §4.1 | For each room and time slot, `sum(class_uses_room_at_slot) <= 1` |
| 2 | **Instructor no-overlap** | `no_instructor_overlap` | `InstructorConstraint` §4.2 | For each instructor and time slot, `sum(class_uses_instructor_at_slot) <= 1` |
| 3 | **Group no-overlap** | `no_group_overlap` | `JenrlConstraint` §4.3 (when all students shared) | For each group and time slot, `sum(class_has_group_at_slot) <= 1` |
| 4 | **Student no-overlap** | `no_student_overlap_by_membership` | `JenrlConstraint` §4.3 | If two groups share at least one student, their classes cannot overlap in time |
| 5 | **Room capacity** | `room_capacity` | Domain filtering in `Lecture.computeValues()` §3.2 | Soft penalty by default (`3.0`); can be set to `"hard"` in YAML |
| 6 | **Required count per subpart** | (structural) | Completeness in `GeneralSolutionComparator` §3.5 | For each class subpart (`tag`), solver places exactly `required_count` meetings (per generated instance when `per_group: true`) |
| 7 | **Allowed slots** | `allowed_slots` | Domain filtering | Class can only be placed in allowed slot grids |
| 8 | **Block eligibility** | (not in `ScheduleConfig` yet) | Date-pattern restrictions | Roadmap: dates per block / burst windows |
| 9 | **Program availability window** | (not in `ScheduleConfig` yet) | Date-pattern restrictions | Roadmap: `starts_from` / `until` per program or group |

Additional global soft objectives (same `constraints.weights` map): `minimize_gaps_for_groups`, `instructor_max_per_day`, `time_preference`, `too_big_room`.

## Per-course relation constraints (`CourseConstraintType`)

Configured under `constraints.course_constraints` keyed by `course_tags` (or the `null` bucket for defaults). Each maps to a weight or `"hard"`:

| Config id | Meaning |
|---|---|
| `same_day` | All referenced class instances must be on the same day |
| `ordered` | Classes follow order: lecture → tutorial → lab |
| `back_to_back` | Prefer consecutive slots without gaps for lecture and tutorial |
| `same_room` | Prefer the same room for lecture and tutorial |

A typical L–T–T chain is expressed by tag-level defaults (e.g. `same_day` + `ordered` + weighted `back_to_back`) rather than a per-course list in YAML.

## Global Soft Objectives (v1)

Weighted sum, minimized — ids match `ConstraintType` in [`config.py`](../config.py):

| # | Objective | Config id | UniTime Equivalent | OR-Tools Approach |
|---|---|---|---|---|
| 1 | **Minimize group gaps** | `minimize_gaps_for_groups` | `StudentMinimizeScheduleHoles` §5.19 | For each group-day: penalize idle slots between first and last class |
| 2 | **Instructor daily load** | `instructor_max_per_day` | `MaxHoursPerDay` flexible constraint §4.5 | Penalty when instructor exceeds configured daily load (solver-side limit) |
| 3 | **Time preferences** | `time_preference` | `TimePreferences` §5.7 | Penalize placements outside preferred instructor times |
| 4 | **Too-big room** | `too_big_room` | `TooBigRooms` §5.10 | Penalize rooms much larger than needed |

## Architecture

```
schedule-assistant/
├── main.py                 # argparse CLI + CP-SAT wiring (current entry point)
├── config.py               # Pydantic input schema: ScheduleConfig, enums, defaults
├── config_tui.py           # optional TUI for configuration
├── convert_json_to_config_candidate.py  # import helpers
├── pyproject.toml          # project metadata and dependencies
└── uv.lock                 # lockfile (uv)
```

## Tech Stack

- **Python 3.12+**
- **ortools** (Google OR-Tools) — CP-SAT solver
- **pydantic** — input validation (`ScheduleConfig`)
- **pyyaml** — YAML parsing
- **argparse** — CLI framework

## CLI Interface

```bash
cd schedule-assistant
uv run python main.py config.yaml --output schedule.yaml
# Optional: --time-limit SECONDS (default 60), --no-progress
```

## Non-Goals for v1

- No web UI (CLI only)
- No student-level sectioning (we schedule at group level)
- No exam scheduling
- No event management
- No Minimum Perturbation Problem (incremental re-scheduling)
- No room sharing / distance constraints (single building)
- No burst windows or per-class date ranges in the input schema until modeled in `ScheduleConfig` (see Cases above for roadmap intent)
- No department balancing

## Migration Path from UniTime Concepts

| UniTime Concept | IU Scheduler Equivalent |
|---|---|
| `Lecture` (decision variable) | A class instance (`course` + `tag` + optional per-group instance) to schedule |
| `Placement = (TimeLocation, RoomLocation)` | `(date, slot, room_id)` tuple |
| `TimeLocation` (day code + start slot + length) | Slot index within `term.time_slots` + `duration_slots`; optional date filters are roadmap |
| `RoomLocation` | `room_id` from `rooms` list |
| `JenrlConstraint` (joint enrollment) | Group no-overlap + `no_student_overlap_by_membership` |
| `InstructorConstraint` | Instructor no-overlap |
| `GroupConstraint` (distribution) | `constraints.course_constraints` by `course_tags` (`CourseConstraintType`) |
| `SpreadConstraint` | Not modeled in `config.py` yet; use roadmap or custom solver penalties |
| IFS solver with backtracking | CP-SAT (complete solver, finds optimal within time limit) |
| `Criterion` weighted sum | CP-SAT `Minimize(sum(weight * penalty))` using `constraints.weights` |

## Implementation Order

1. **Data models** — `ScheduleConfig` and nested models in `config.py` (JSON Schema via `model_json_schema` / `save_schema`)
2. **YAML reader** — `ScheduleConfig.from_yaml`
3. **CP-SAT model builder** — Variables, hard constraints, soft objectives in `main.py` (or split module later)
4. **Solver wrapper** — Run CP-SAT, extract solution
5. **YAML writer** — Emit `schedule.yaml` (or stdout) as implemented
6. **CLI** — `main.py` entry point
7. **Test with real Innopolis data** — Pipelines such as `convert_json_to_config_candidate.py` and XML/JSON assets in-repo
