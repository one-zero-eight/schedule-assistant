# UniTime Course Timetabling — Reverse-Engineered Optimization Formulation

> **Source repositories analyzed:**
> - [UniTime/cpsolver](https://github.com/UniTime/cpsolver) — constraint solver engine (IFS framework + course timetabling)
> - [UniTime/unitime](https://github.com/UniTime/unitime) — web application (database loading, configuration, UI)
>
> All code pointers use relative paths from each repository root.
>
> On this machine you can access code at `dante@dante-pc:../../../cpsolver` and `dante@dante-pc:../../../unitime`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Glossary](#2-glossary)
3. [Formal Mathematical Model](#3-formal-mathematical-model)
4. [Constraint Catalog](#4-constraint-catalog)
5. [Criteria / Objective Catalog](#5-criteria--objective-catalog)
6. [Preferences and Priorities](#6-preferences-and-priorities)
7. [Minimum Perturbation Problem (MPP)](#7-minimum-perturbation-problem-mpp)
8. [Search / Optimization Algorithm](#8-search--optimization-algorithm)
9. [UniTime Integration Layer](#9-unitime-integration-layer)
10. [Appendix: Property Keys](#10-appendix-property-keys)

---

## 1. Executive Summary

UniTime's course timetabling solver assigns **classes** (lectures) to **time slots and rooms** while satisfying hard constraints and minimizing a weighted sum of soft-constraint penalties. The solver is built on the **Iterative Forward Search (IFS)** framework, a local-search method for constraint satisfaction and optimization.

**Core abstraction.** The problem is modeled as a Constraint Satisfaction and Optimization Problem (CSOP):

- **Variables**: each class section (`Lecture`) that needs scheduling.
- **Domain**: for each lecture, the set of feasible `Placement` values — combinations of a `TimeLocation` (day pattern + start slot + weeks) and one or more `RoomLocation`s.
- **Hard constraints**: room no-overlap, instructor no-overlap, student joint-enrollment conflicts (above threshold), required/prohibited distribution constraints, class limits, and more. Hard constraints are enforced by conflict-driven unassignment: if assigning a value violates a hard constraint, conflicting assignments are removed.
- **Soft objective**: a weighted sum of ~20 criteria (student conflicts, time preferences, room preferences, distribution preferences, room utilization, instructor back-to-back distance, perturbation penalties, etc.). The solver minimizes this sum.

**Solution comparison** is lexicographic:
1. Fewer unassigned variables (completeness).
2. Fewer perturbations (MPP mode only).
3. Lower total weighted objective value.

**Search algorithms**: the default IFS uses variable selection (roulette-wheel over unassigned lectures, worst-value for assigned ones), hierarchical 3-level value selection, conflict-based statistics, and local improvement via backtracking suggestions when a complete solution is found. An alternative `SimpleSearch` uses hill-climbing + great deluge / simulated annealing with neighbourhood operators (time change, room change, time swap, room swap, suggestions).

**Minimum Perturbation Problem (MPP)**: when re-solving an existing timetable, the solver adds perturbation penalties that measure how much the new solution differs from the initial one. These penalties cover time changes, room changes, affected students/instructors, and quality deltas.

**Code pointers:**
- Solver engine: [cpsolver:src/org/cpsolver/ifs/solver/Solver.java](../../../cpsolver/src/org/cpsolver/ifs/solver/Solver.java#L205)
- Course timetabling model: [cpsolver:src/org/cpsolver/coursett/model/TimetableModel.java](../../../cpsolver/src/org/cpsolver/coursett/model/TimetableModel.java#L84)
- UniTime integration: `unitime:JavaSource/org/unitime/timetable/solver/TimetableSolver.java`

---

## 2. Glossary

| Code Name | Math Symbol | Description |
|-----------|-------------|-------------|
| `Lecture` | $\ell_i$ | Decision variable — a class section to be scheduled |
| `Placement` | $p_i = (t_i, R_i)$ | Assignment value — a time + room(s) for a lecture |
| `TimeLocation` | $t$ | Time slot: day code + start slot + length + week pattern + break time |
| `RoomLocation` | $r$ | Room: id, capacity, coordinates, preference |
| `TimetableModel` | $\mathcal{M}$ | The CSP model containing all variables, constraints, criteria |
| `Assignment` | $\sigma$ | A (partial) mapping from lectures to placements |
| `JenrlConstraint` | — | Joint enrollment constraint between two lectures sharing students |
| `GroupConstraint` | — | Distribution constraint (same-time, back-to-back, etc.) |
| `InstructorConstraint` | — | Instructor no-overlap + distance constraint |
| `RoomConstraint` | — | Room no-overlap constraint |
| `SpreadConstraint` | — | Same-subpart time-balancing constraint |
| `Criterion` / `TimetablingCriterion` | $C_k$ | An objective component with weight $w_k$ |
| `jenrl(i,j)` | $n_{ij}$ | Number of students jointly enrolled in lectures $i$ and $j$ |
| `weight` (Lecture) | $\omega_i$ | Lecture weight (typically 1.0, configurable) |
| `normPref(t)` | $\hat{\pi}(t)$ | Normalized time preference of time location $t$ |
| `roomPenalty(p)` | $\rho(p)$ | Room preference penalty of placement $p$ |
| `prefLevel` | $\pi$ | Integer preference level (see Section 6) |
| `perturbVariables` | $\mathcal{P}$ | Set of lectures whose current assignment differs from initial |

---

## 3. Formal Mathematical Model

### 3.1 Decision Variables

For each class section $i \in \{1, \ldots, N\}$, define a decision variable $\ell_i$ representing the lecture to be scheduled.

### 3.2 Domains

Each lecture $\ell_i$ has a domain $D_i$ of feasible placements:

$$D_i = \{ p = (t, R) \mid t \in \mathcal{T}_i,\; R \subseteq \mathcal{R}_i,\; |R| = k_i \}$$

where:
- $\mathcal{T}_i$ is the set of feasible time locations for lecture $i$ (filtered by availability, instructor hard constraints, prohibited preferences)
- $\mathcal{R}_i$ is the set of feasible room locations for lecture $i$
- $k_i$ is the number of rooms required ($k_i \geq 0$; classes with $k_i = 0$ are "roomless")

Domains are constructed in `Lecture.computeValues()` ([cpsolver:src/org/cpsolver/coursett/model/Lecture.java](../../../cpsolver/src/org/cpsolver/coursett/model/Lecture.java#L400)). Times with prohibited preferences or hard instructor unavailability are excluded. Room combinations that violate capacity or hard room constraints are excluded.

**Code pointers:**
- `Lecture.computeValues()`: lines 400–525
- `Placement` constructor: [cpsolver:src/org/cpsolver/coursett/model/Placement.java](../../../cpsolver/src/org/cpsolver/coursett/model/Placement.java#L64)
- `TimeLocation`: [cpsolver:src/org/cpsolver/coursett/model/TimeLocation.java](../../../cpsolver/src/org/cpsolver/coursett/model/TimeLocation.java#L34)
- `RoomLocation`: [cpsolver:src/org/cpsolver/coursett/model/RoomLocation.java](../../../cpsolver/src/org/cpsolver/coursett/model/RoomLocation.java#L34)

### 3.3 Hard Constraints

A solution $\sigma$ is **feasible** if all hard constraints are satisfied. Hard constraints are enforced by conflict-driven unassignment — if assigning $p_i$ to $\ell_i$ would violate a hard constraint, the conflicting assignments are unassigned.

Let $\sigma(\ell_i)$ denote the placement assigned to lecture $\ell_i$ under assignment $\sigma$, or $\bot$ if unassigned.

**Room no-overlap:** For each room $r$ and any two lectures $\ell_i, \ell_j$ assigned to use room $r$:

$$\sigma(\ell_i) \neq \bot \;\wedge\; \sigma(\ell_j) \neq \bot \;\wedge\; r \in R_i \cap R_j \implies \neg\text{overlap}(t_i, t_j)$$

**Instructor no-overlap:** For each instructor $k$ teaching lectures $\ell_i, \ell_j$:

$$\sigma(\ell_i) \neq \bot \;\wedge\; \sigma(\ell_j) \neq \bot \implies \neg\text{overlap}(t_i, t_j)$$

**Student joint-enrollment (when over threshold):** `JenrlConstraint` is technically hard with weakening. When $n_{ij} > \text{limit}_{ij}$, two lectures sharing students must not overlap:

$$n_{ij} > \text{limit}_{ij} \implies \neg\text{overlap}(t_i, t_j) \;\wedge\; \neg\text{distance}(p_i, p_j) \;\wedge\; \neg\text{workday}(p_i, p_j)$$

The limit defaults to $\text{JenrlMaxConflicts} \times \min(\text{classLimit}_i, \text{classLimit}_j)$ and is weakened by $\text{JenrlMaxConflictsWeaken}$ when the solver gets stuck.

**Distribution constraints (required/prohibited):** For each `GroupConstraint` $gc$ with `isHard() = true`:

$$\text{Required:}\quad \forall (i,j) \in \text{pairs}(gc): \text{type}_{gc}\text{.isSatisfied}(p_i, p_j) = \text{true}$$
$$\text{Prohibited:}\quad \forall (i,j) \in \text{pairs}(gc): \text{type}_{gc}\text{.isViolated}(p_i, p_j) = \text{true}$$

See Section 4 for the full list of constraint types and their pair-check functions.

### 3.4 Soft Objective

The objective function is a weighted sum of criteria, to be **minimized**:

$$\text{Obj}(\sigma) = \sum_{k=1}^{K} w_k \cdot C_k(\sigma)$$

This is computed by `TimetableModel.getTotalValue()` (line 447–460):

```java
public double getTotalValue(Assignment<Lecture, Placement> assignment) {
    double ret = 0;
    for (Criterion<Lecture, Placement> criterion : getCriteria())
        ret += criterion.getWeightedValue(assignment);
    return ret;
}
```

where `getWeightedValue() = weight * getValue()` for each criterion.

The default criteria registered in `TimetableModel` constructor (`General.Criteria` property, lines 125–148):

| # | Criterion | Weight Property | Default Weight |
|---|-----------|----------------|----------------|
| 1 | StudentConflict | `Weight.StudentConflict` | 0.0 (base; subclasses have weights) |
| 2 | StudentDistanceConflict | `Comparator.DistStudentConflictWeight` | 0.2 |
| 3 | StudentHardConflict | `Comparator.HardStudentConflictWeight` | 5.0 |
| 4 | StudentCommittedConflict | `Comparator.CommitedStudentConflictWeight` | 1.0 |
| 5 | StudentOverlapConflict | `Comparator.StudentConflictWeight` | 1.0 |
| 6 | UselessHalfHours | `sPreferenceLevelStronglyDiscouraged × Comparator.UselessSlotWeight` | $4 \times 0.1 = 0.4$ |
| 7 | BrokenTimePatterns | `sPreferenceLevelDiscouraged × Comparator.UselessSlotWeight` | $1 \times 0.1 = 0.1$ |
| 8 | TooBigRooms | `Comparator.TooBigRoomWeight` | 0.1 |
| 9 | TimePreferences | `Comparator.TimePreferenceWeight` | 1.0 |
| 10 | RoomPreferences | `Comparator.RoomPreferenceWeight` | 1.0 |
| 11 | DistributionPreferences | `Comparator.ContrPreferenceWeight` | 1.0 |
| 12 | SameSubpartBalancingPenalty | `12.0 × Comparator.SpreadPenaltyWeight` | 12.0 |
| 13 | DepartmentBalancingPenalty | `12.0 × Comparator.DeptSpreadPenaltyWeight` | 12.0 |
| 14 | BackToBackInstructorPreferences | `sPreferenceLevelDiscouraged × Comparator.DistanceInstructorPreferenceWeight` | 1.0 |
| 15 | Perturbations | `Comparator.PerturbationPenaltyWeight` | 1.0 |
| 16 | DeltaTimePreference | 0.0 (placement-only) | 0.0 |
| 17 | HardConflicts | 0.0 (placement-only) | 0.0 |
| 18 | PotentialHardConflicts | 0.0 (placement-only) | 0.0 |
| 19 | FlexibleConstraintCriterion | `FlexibleConstraint.Weight` | 1.0 |
| 20 | WeightedHardConflicts | 0.0 (placement-only) | 0.0 |

**Conditionally added:**
- `StudentWorkdayConflict` if `StudentConflict.WorkDayLimit > 0`
- `TimeViolations` + `RoomViolations` in interactive mode
- Additional criteria from `General.AdditionalCriteria` (e.g., `ImportantStudentConflict`, `InstructorLunchBreak`, `RoomSizePenalty`)
- `InstructorConflict` if `General.SoftInstructorConstraints = true`

### 3.5 Solution Comparator

The solution comparator (`GeneralSolutionComparator`, used by `TimetableComparator`) defines the best-solution logic:

$$\sigma_1 \succ \sigma_2 \iff \begin{cases}
|\text{unassigned}(\sigma_1)| < |\text{unassigned}(\sigma_2)| & \text{(completeness first)} \\
\text{Obj}(\sigma_1) < \text{Obj}(\sigma_2) & \text{if tied on unassigned}
\end{cases}$$

In MPP mode, perturbation count is checked between completeness and objective value.

**Code pointers:**
- `GeneralSolutionComparator.isBetterThanBestSolution()`: [cpsolver:src/org/cpsolver/ifs/solution/GeneralSolutionComparator.java](../../../cpsolver/src/org/cpsolver/ifs/solution/GeneralSolutionComparator.java#L55)
- `TimetableComparator`: [cpsolver:src/org/cpsolver/coursett/heuristics/TimetableComparator.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/TimetableComparator.java#L109) (deprecated wrapper)

---

## 4. Constraint Catalog

All constraint classes are in package `org.cpsolver.coursett.constraint`, e.g. [cpsolver:src/org/cpsolver/coursett/constraint/GroupConstraint.java](../../../cpsolver/src/org/cpsolver/coursett/constraint/GroupConstraint.java#L119).

### 4.1 RoomConstraint

**Type:** Hard. No two classes may overlap in the same room.

**Logic:** For each room, maintains a slot-indexed placement list. `computeConflicts` finds all placements overlapping with the candidate's time in this room. Supports room sharing via `RoomSharingModel`.

**LaTeX:**
$$\forall r,\; \forall i \neq j \text{ using } r:\quad r \in R_i \cap R_j \implies \neg\text{timeOverlap}(t_i, t_j)$$

**Code pointers:**
- Class: `RoomConstraint extends ConstraintWithContext`
- `computeConflicts()`, `inConflict()`: slot-based overlap detection
- `RoomConstraintContext`: maintains per-slot placement arrays and room sharing logic

**Key configuration:**
- `General.IgnoreRoomSharing` (default `false`)

---

### 4.2 InstructorConstraint

**Type:** Hard (unless `SoftInstructorConstraint` variant is used).

**Logic:** No two classes assigned to the same instructor may overlap in time. Additionally, back-to-back classes are penalized based on room distance (see `BackToBackInstructorPreferences` criterion).

**LaTeX:**
$$\forall \text{instructor } k,\; \forall i \neq j \text{ taught by } k:\quad \neg\text{timeOverlap}(t_i, t_j)$$

Distance preference for back-to-back:
$$\text{distPref}(p_i, p_j) = \begin{cases}
0 & \text{if } d(p_i, p_j) \leq d_0 \\
1 & \text{if } d_0 < d(p_i, p_j) \leq d_1 \\
4 & \text{if } d_1 < d(p_i, p_j) \leq d_2 \\
100 \text{ (prohibited)} & \text{if } d(p_i, p_j) > d_2
\end{cases}$$

Where $d_0$ = `Instructor.NoPreferenceLimit` (0m), $d_1$ = `Instructor.DiscouragedLimit` (50m), $d_2$ = `Instructor.ProhibitedLimit` (200m).

**Code pointers:**
- `InstructorConstraint`: `computeConflicts()`, `getDistancePreference()` (lines 168–202)
- `SoftInstructorConstraint`: extends `InstructorConstraint`, `isHard()=false`, no conflicts
- Unavailability: `setNotAvailable()`, `isAvailable()` (lines 90–139)

**Key configuration:**
- `Instructor.NoPreferenceLimit` (default `0.0`)
- `Instructor.DiscouragedLimit` (default `50.0`)
- `Instructor.ProhibitedLimit` (default `200.0`)
- `General.SoftInstructorConstraints` (default `false`)

---

### 4.3 JenrlConstraint (Joint Enrollment)

**Type:** Hard with weakening. Prevents overlapping/distance-conflicting placements for two classes sharing students above a configurable threshold.

**Logic:** For each pair of lectures $(i, j)$ sharing students:
- If `isOverLimit()` (i.e., $n_{ij} > \text{limit}_{ij}$), the constraint is active and `computeConflicts` will unassign the other placement when overlap/distance/workday conflict occurs.
- The limit weakens over time via `WeakeningConstraint` interface.

**LaTeX:**
$$n_{ij} > \alpha \cdot \min(L_i, L_j) \implies \neg(\text{overlap}(t_i, t_j) \;\lor\; \text{distance}(p_i, p_j) \;\lor\; \text{workday}(p_i, p_j))$$

where $\alpha$ = `General.JenrlMaxConflicts` (default 1.0, meaning effectively disabled when 1.0; activated when set below 1.0).

**Student conflict** is defined as:
- **Overlap**: $t_i$ and $t_j$ share days, share weeks, and time intervals intersect
- **Distance**: back-to-back classes where travel time > break time
- **Workday**: combined slot span > `StudentConflict.WorkDayLimit`

The number of affected students $n_{ij}$ is a weighted count stored as `jenrl()`.

**Code pointers:**
- `JenrlConstraint`: `computeConflicts()`, `isInConflict()` (static), `incJenrl()`, `decJenrl()`
- Weakening: `JenrlConstraintContext.weaken()`, `isOverLimit()`
- Student conflict definitions: `StudentConflict.overlaps()`, `distance()`, `workday()` in criteria

**Key configuration:**
- `General.JenrlMaxConflicts` (default `1.0`)
- `General.JenrlMaxConflictsWeaken` (default `0.001`)
- `StudentConflict.WorkDayLimit` (default `-1`, disabled)

---

### 4.4 GroupConstraint (Distribution Constraints)

**Type:** Hard if preference is Required or Prohibited; soft otherwise.

**Logic:** Enforces a relation among a set of lectures. The relation type is defined by the `ConstraintType` enum. For soft constraints, `getCurrentPreference()` computes a penalty proportional to the number of violated pairs.

**`isHard()` logic:**
```java
return iIsRequired || iIsProhibited;
```

**Soft penalty:**
$$\text{penalty}_{gc} = |\pi_{gc}| \times |\{(i,j) \in \text{pairs}(gc) : \neg\text{satisfied}(p_i, p_j)\}|$$

When fully satisfied: $\text{penalty}_{gc} = -|\pi_{gc}|$ (a reward). The `DistributionPreferences` criterion sums these.

#### 4.4.1 Complete List of Distribution Constraint Types

| Enum Constant | Reference | Pair Check (satisfied when) |
|---|---|---|
| `SAME_TIME` | `SAME_TIME` | Same start slot and length |
| `SAME_DAYS` | `SAME_DAYS` | Same day code |
| `BTB` | `BTB` | Back-to-back in time AND same room |
| `BTB_TIME` | `BTB_TIME` | Back-to-back in time (any room) |
| `DIFF_TIME` | `DIFF_TIME` | No time overlap |
| `NHB_1` through `NHB_8` | `NHB(1)` .. `NHB(8)` | Exactly N hours between classes |
| `NHB_1_5` | `NHB(1.5)` | Exactly 1.5 hours between |
| `NHB_4_5` | `NHB(4.5)` | Exactly 4.5 hours between |
| `NHB_GTE_1` | `NHB_GTE(1)` | At least 1 hour between |
| `NHB_LT_6` | `NHB_LT(6)` | Less than 6 hours between |
| `SAME_START` | `SAME_START` | Same start time slot |
| `SAME_ROOM` | `SAME_ROOM` | Same room |
| `SAME_STUDENTS` | `SAME_STUDENTS` | Marker: share students |
| `SAME_INSTR` | `SAME_INSTR` | Marker: share instructor |
| `CAN_SHARE_ROOM` | `CAN_SHARE_ROOM` | Allows room sharing |
| `PRECEDENCE` | `PRECEDENCE` | First ends before second starts |
| `BTB_DAY` | `BTB_DAY` | On adjacent days (no day gap) |
| `MEET_WITH` | `MEET_WITH` | Same time AND same room |
| `NDB_GT_1` | `NDB_GT_1` | More than 1 day between |
| `CH_NOTOVERLAP` | `CH_NOTOVERLAP` | Children cannot overlap |
| `FOLLOWING_DAY` | `FOLLOWING_DAY` | Next day |
| `EVERY_OTHER_DAY` | `EVERY_OTHER_DAY` | Two days after |
| `MAX_HRS_DAY(N)` | `MAX_HRS_DAY(N)` | At most N hours per day combined |
| `SAME_WEEKS` | `SAME_WEEKS` | Same week pattern |
| `LINKED_SECTIONS` | `LINKED_SECTIONS` | Linked class sections |
| `BTB_PRECEDENCE` | `BTB_PRECEDENCE` | Back-to-back with precedence |
| `SAME_DAYS_TIME` | `SAME_D_T` | Same days and time |
| `SAME_DAYS_ROOM_TIME` | `SAME_D_R_T` | Same days, room, and time |
| `WORKDAY(N)` | `WORKDAY(N)` | Workday of at most N hours |
| `MEET_WITH_WEEKS` | `MEET_WITH_WEEKS` | Meet together and same weeks |
| `MIN_GAP(N)` | `MIN_GAP(N)` | Minimum gap of N slots between |
| `BTB_WEEKS` | `BTB_WEEKS` | Back-to-back weeks |
| `FOLLOWING_WEEKS` | `FOLLOWING_WEEKS` | Following weeks |
| `SAME_DATES` | `SAME_DATES` | Same dates |
| `SAME_DAYS_ROOM_START` | `SAME_DAY_ROOM_START` | Same days, room, and start |
| `DAYBREAK(N,M)` | `DAYBREAK(N,M)` | Daybreak constraint |
| `ONLINE_ROOM` | `ONLINE_ROOM` | Online/offline room matching |
| `SAME_DATE_TIME_WEEKS` | `SAME_DTW` | Same days, time, and weeks |
| `DIFF_TIME_IGN_STUDS` | `DIFF_TIME_IGN_STUDS` | Different time + ignore student conflicts |
| `FOLLOWING_DATES` | `FOLLOWING_DATES` | Following dates |
| `MAX_DAYS_RANGE(N)` | `MAX_DAYS_RANGE(N)` | Max days range |
| `MAX_WORKDAYS_RANGE(N)` | `MAX_WORKDAYS(N)` | Max workdays range |

#### 4.4.2 Structure (How Lectures Are Grouped)

Structure is defined in `unitime:JavaSource/org/unitime/timetable/model/DistributionPref.java` and applied in `TimetableDatabaseLoader.loadGroupConstraint()`:

| Structure | Effect |
|-----------|--------|
| **All Classes** | One `GroupConstraint` containing all lectures |
| **Progressive** | Multiple constraints; lectures sorted, consecutive chains checked |
| **Groups of Two/Three/Four/Five** | One constraint per group of N lectures in order |
| **Pairwise** | One constraint per every unordered pair → $\binom{N}{2}$ constraints |
| **One Of Each** | Permutations of sections → one constraint per permutation |

Structure does not change the internal checking logic of `GroupConstraint`; it controls how many constraints are instantiated and which lectures are in each.

**Code pointers:**
- `GroupConstraint`: [cpsolver:src/org/cpsolver/coursett/constraint/GroupConstraint.java](../../../cpsolver/src/org/cpsolver/coursett/constraint/GroupConstraint.java#L119)
- `ConstraintType` enum: lines ~330–1262
- `getCurrentPreference()`: lines 1914–1957
- `isSatisfiedPair()`: lines 2485–2491
- `DistributionPref.Structure`: `unitime:JavaSource/org/unitime/timetable/model/DistributionPref.java`
- `loadGroupConstraint()`: `unitime:JavaSource/org/unitime/timetable/solver/TimetableDatabaseLoader.java` lines ~1838–2091

---

### 4.5 FlexibleConstraint (and 8 Subtypes)

**Type:** Hard if preference is Required; soft otherwise.

Abstract base class for parametric constraints parsed from reference strings like `_(MaxBlock):120:30_`. Eight concrete subtypes:

| Class | Reference Pattern | Logic |
|-------|------------------|-------|
| `MaxBlockFlexibleConstraint` | `_(MaxBlock):M:B_` | Max M minutes of BTB classes with break B |
| `BreakFlexibleConstraint` | `_(Break):D:B:L_` | Required break of B minutes in window D with max block L |
| `MaxBreaksFlexibleConstraint` | `_(MaxBreaks):M:B_` | Max M breaks (gaps ≥ B minutes) per day |
| `MaxWeeksFlexibleConstraint` | `_(MaxWeeks):N:W_` | Max W weeks with classes |
| `MaxDaysFlexibleConstraint` | `_(MaxDays):N_` | Max N days per week |
| `MaxHolesFlexibleConstraint` | `_(MaxHoles):N_` | Max N holes (gaps) per day |
| `MaxHalfDaysFlexibleConstraint` | `_(MaxHalfDays):N_` | Max N half-days per week |
| `MaxConsecutiveDaysFlexibleConstraint` | `_(MaxConsDays):N_` | Max N consecutive days |

**Code pointers:**
- Base: [cpsolver:src/org/cpsolver/coursett/constraint/FlexibleConstraint.java](../../../cpsolver/src/org/cpsolver/coursett/constraint/FlexibleConstraint.java#L47)
- Subtypes: same directory, e.g., `MaxBlockFlexibleConstraint.java`
- Criterion: `FlexibleConstraintCriterion` ([cpsolver:src/org/cpsolver/coursett/criteria/FlexibleConstraintCriterion.java](../../../cpsolver/src/org/cpsolver/coursett/criteria/FlexibleConstraintCriterion.java#L41))

**Key configuration:**
- `FlexibleConstraint.Weight` (default `1.0`)

---

### 4.6 SpreadConstraint

**Type:** Hard with weakening. Spreads classes of the same scheduling subpart evenly across time slots.

**Logic:** Computes an "ideal" number of classes per time slot based on the total count and available slots. Penalizes slots that exceed the ideal by more than `SpreadFactor` (default 1.2). Implements `WeakeningConstraint` to relax when the solver stalls.

**Code pointers:**
- `SpreadConstraint`: [cpsolver:src/org/cpsolver/coursett/constraint/SpreadConstraint.java](../../../cpsolver/src/org/cpsolver/coursett/constraint/SpreadConstraint.java#L47)
- Criterion: `SameSubpartBalancingPenalty` in criteria

**Key configuration:**
- `Spread.SpreadFactor` (default `1.2`)
- `Spread.Unassignments2Weaken` (default `50`)

---

### 4.7 DepartmentSpreadConstraint

**Type:** Hard with weakening. Extends `SpreadConstraint` for department-level time balancing.

**Code pointers:**
- `DepartmentSpreadConstraint extends SpreadConstraint`
- Criterion: `DepartmentBalancingPenalty`

**Key configuration:**
- `DeptBalancing.SpreadFactor` (default `1.2`)
- `DeptBalancing.Unassignments2Weaken` (default `0`)
- `General.DeptBalancing` (default `false`) — must be enabled

---

### 4.8 ClassLimitConstraint

**Type:** Hard. Ensures class enrollment limits are respected.

**Code pointers:**
- `ClassLimitConstraint extends Constraint`
- `computeConflicts()`: checks `currentClassLimit` against maximum

---

### 4.9 DiscouragedRoomConstraint

**Type:** Hard with weakening. Extends `RoomConstraint` to minimize usage of discouraged rooms.

**Logic:** Like `RoomConstraint` but additionally limits how many classes use the room. The limit is weakened after a configurable number of unassignment attempts.

**Code pointers:**
- `DiscouragedRoomConstraint extends RoomConstraint implements WeakeningConstraint`

**Key configuration:**
- `DiscouragedRoom.Unassignments2Weaken` (default `1000`)

---

### 4.10 MinimizeNumberOfUsedRoomsConstraint

**Type:** Hard with weakening. `MIN_ROOM_USE` — minimizes distinct rooms for a set of classes.

---

### 4.11 MinimizeNumberOfUsedGroupsOfTime

**Type:** Hard with weakening. `MIN_GRUSE(N)` — minimizes groups of N-hour blocks.

---

### 4.12 IgnoreStudentConflictsConstraint

**Type:** Soft (no-op). Marker constraint between two lectures that student conflicts should be ignored.

---

### 4.13 NoStudentOnlineConflicts

**Type:** Hard. Prevents a student from having online and face-to-face classes on the same day.

**Key configuration:**
- `General.OnlineRoom` (regex matching online room names)

---

### 4.14 ExtendedStudentConflicts

**Type:** Hard. Experimental global constraint for extended student overlap rules.

---

## 5. Criteria / Objective Catalog

All criterion classes are in package `org.cpsolver.coursett.criteria`, e.g. [cpsolver:src/org/cpsolver/coursett/criteria/FlexibleConstraintCriterion.java](../../../cpsolver/src/org/cpsolver/coursett/criteria/FlexibleConstraintCriterion.java#L41).

### Framework

Each criterion extends `TimetablingCriterion` (which extends `AbstractCriterion<Lecture, Placement>`).

**Weight resolution**: `configure()` reads `properties.getDouble(getWeightName(), getWeightDefault(properties))`. The weight name defaults to `"Weight." + className` but most criteria override `getWeightDefault()` to use `Comparator.*Weight` property keys.

**Placement selection**: `TimetablingCriterion` provides 3-level placement-selection weights via `Placement.<CriterionName>Weight1/2/3` for the hierarchical value heuristic (Section 8).

### 5.1 StudentConflict (base)

**Weight:** 0.0 (base class; subclasses override). **Placement selection key:** `null` (disabled).

**Score:** Counts the weighted number of students in conflicting placements between jointly-enrolled classes:

$$C_{\text{sc}} = \sum_{(i,j) \in \text{Jenrl}} \begin{cases} n_{ij} & \text{if overlap}(p_i, p_j) \lor \text{distance}(p_i, p_j) \\ 0 & \text{otherwise} \end{cases}$$

where the sum is over `JenrlConstraint` pairs where `isApplicable()` is true.

**Code:** `StudentConflict.getValue()` iterates jenrl constraints; `inConflict()` checks overlap OR distance.

---

### 5.2 StudentOverlapConflict

**Weight:** `Comparator.StudentConflictWeight` (default `1.0`). **Placement key:** `Placement.NrStudConfsWeight`.

**Score:** Same as base but `inConflict()` checks **overlap only** (no distance).

---

### 5.3 StudentHardConflict

**Weight:** `Comparator.HardStudentConflictWeight` (default `5.0`). **Placement key:** `Placement.NrHardStudConfsWeight`.

**Score:** Same as base but `isApplicable()` requires both lectures to be single-section (`isSingleSection()`), meaning students have no alternative.

---

### 5.4 StudentDistanceConflict

**Weight:** `Comparator.DistStudentConflictWeight` (default `0.2`). **Placement key:** `Placement.NrDistStudConfsWeight`.

**Score:** Same as base but `inConflict()` checks **distance only** (not overlap).

$$\text{distance}(p_i, p_j) = \begin{cases} \text{true} & \text{if BTB and } \text{travelMinutes}(p_i, p_j) > \text{breakTime} \\ \text{false} & \text{otherwise} \end{cases}$$

---

### 5.5 StudentCommittedConflict

**Weight:** `Comparator.CommitedStudentConflictWeight` (default `1.0`). **Placement key:** `Placement.NrCommitedStudConfsWeight`.

**Score:** Counts student conflicts where one class is committed (pre-assigned from another solver group).

---

### 5.6 StudentWorkdayConflict

**Weight:** `Comparator.WorkDayStudentConflictWeight` (default `0.2`). **Placement key:** `Placement.NrWorkDayStudConfsWeight`.

**Score:** Counts pairs where combined slot span exceeds `StudentConflict.WorkDayLimit`.

**Condition to register:** Only added when `StudentConflict.WorkDayLimit > 0`.

---

### 5.7 TimePreferences

**Weight:** `Comparator.TimePreferenceWeight` (default `1.0`). **Placement key:** `Placement.TimePreferenceWeight`.

**Score:**
$$C_{\text{time}} = \sum_{i : \ell_i \text{ assigned}} \omega_i \cdot \hat{\pi}(t_i)$$

where $\hat{\pi}(t_i)$ = `value.getTimePenalty()` which returns the normalized time preference of the assigned `TimeLocation`.

Time preferences are merged from class, subpart, and instructor preferences in `Class_.effectivePreferences(TimePref.class)` before the solver starts. The normalized preference uses `General.NormalizedPrefDecreaseFactor` (default 0.77).

**Code:** `TimePreferences.getValue()`, `Placement.getTimePenalty()`, `TimeLocation.getNormalizedPreference()`

---

### 5.8 RoomPreferences

**Weight:** `Comparator.RoomPreferenceWeight` (default `1.0`). **Placement key:** `Placement.RoomPreferenceWeight`.

**Score:**
$$C_{\text{room}} = \sum_{i : \ell_i \text{ assigned}} \omega_i \cdot \rho(p_i)$$

where $\rho(p_i)$ = `value.getRoomPenalty()` is the sum of room preference levels for the assigned room(s).

---

### 5.9 DistributionPreferences

**Weight:** `Comparator.ContrPreferenceWeight` (default `1.0`). **Placement key:** `Placement.ConstrPreferenceWeight`.

**Score:**
$$C_{\text{dist}} = \sum_{gc \in \text{softGC}} gc.\text{getCurrentPreference}()$$

Each soft group constraint contributes $|\pi_{gc}| \times \text{nrViolatedPairs}$ when violated, or $-|\pi_{gc}|$ when satisfied.

**Bounds:** $\sum_{gc} |\pi_{gc}| \cdot (1 + \binom{|gc|}{2})$

---

### 5.10 TooBigRooms

**Weight:** `Comparator.TooBigRoomWeight` (default `0.1`). **Placement key:** `Placement.TooBigRoomWeight`.

**Score:** For each assigned class, penalizes rooms that are too large relative to `minRoomSize()`:

$$\text{pref}(p_i) = \begin{cases}
0 & \text{if } \text{size}(r) \leq 1.25 \cdot \text{minSize}_i \\
1 \text{ (discouraged)} & \text{if } 1.25 \cdot \text{minSize}_i < \text{size}(r) \leq 1.5 \cdot \text{minSize}_i \\
4 \text{ (strongly disc.)} & \text{if } \text{size}(r) > 1.5 \cdot \text{minSize}_i
\end{cases}$$

**Key configuration:**
- `TooBigRooms.DiscouragedRoomSize` (default `1.25`)
- `TooBigRooms.StronglyDiscouragedRoomSize` (default `1.5`)

---

### 5.11 UselessHalfHours

**Weight:** `Constants.sPreferenceLevelStronglyDiscouraged × Comparator.UselessSlotWeight` (default $4 \times 0.1 = 0.4$). **Placement key:** `Placement.UselessSlotsWeight`.

**Score:** Counts empty half-hour gaps in rooms (6 consecutive empty slots = 30 minutes) that are bounded by occupied slots on both sides. These gaps are "useless" because no class shorter than 30 minutes typically exists.

---

### 5.12 BrokenTimePatterns

**Weight:** `Constants.sPreferenceLevelDiscouraged × Comparator.UselessSlotWeight` (default $1 \times 0.1 = 0.1$). **Placement key:** `Placement.UselessSlotsWeight`.

**Score:** Penalizes unused slots in rooms that break MWF or TTh day patterns. For example, if a room is occupied on Monday and Friday at a given time but empty on Wednesday, that creates a broken MWF pattern. The score is divided by 6.0 for normalization.

---

### 5.13 BackToBackInstructorPreferences

**Weight:** `Constants.sPreferenceLevelDiscouraged × Comparator.DistanceInstructorPreferenceWeight` (default $1 \times 1.0 = 1.0$). **Placement key:** `Placement.DistanceInstructorPreferenceWeight`.

**Score:**
$$C_{\text{btb}} = \sum_{k \in \text{instructors}} \text{ic}_k.\text{getPreference}(\sigma)$$

Sums distance-based penalties from all `InstructorConstraint`s (see Section 4.2).

---

### 5.14 SameSubpartBalancingPenalty

**Weight:** `12.0 × Comparator.SpreadPenaltyWeight` (default $12.0 \times 1.0 = 12.0$). **Placement key:** `Placement.SpreadPenaltyWeight`.

**Score:**
$$C_{\text{spread}} = \frac{1}{12} \sum_{sc \in \text{SpreadConstraints}} sc.\text{getPenalty}(\sigma)$$

The division by 12 normalizes against the weight multiplication of 12.

---

### 5.15 DepartmentBalancingPenalty

**Weight:** `12.0 × Comparator.DeptSpreadPenaltyWeight` (default $12.0 \times 1.0 = 12.0$). **Placement key:** `Placement.DeptSpreadPenaltyWeight`.

**Score:** Same structure as subpart balancing but using `DepartmentSpreadConstraint`.

---

### 5.16 Perturbations

**Weight:** `Comparator.PerturbationPenaltyWeight` (default `1.0`). **Placement key:** `Placement.MPP_DeltaInitialAssignmentWeight`.

**Score:** Delegates to `UniversalPerturbationsCounter.getPerturbationPenalty()`. See Section 7 for the full formulation.

---

### 5.17 FlexibleConstraintCriterion

**Weight:** `FlexibleConstraint.Weight` (default `1.0`). **Placement key:** `Placement.FlexibleConstrPreferenceWeight`.

**Score:** Sums soft penalties from all `FlexibleConstraint` instances.

---

### 5.18 Placement-Selection Criteria

These have **weight 0.0** in the objective (do not affect solution comparison) but provide weights for the 3-level value selection heuristic:

| Class | Placement Key | Logic |
|-------|---------------|-------|
| `HardConflicts` | `Placement.NrConflictsWeight` | Number of conflicts (default level-1 weight 3.0) |
| `WeightedHardConflicts` | `Placement.NrConflictsWeight` | Conflict-statistics-weighted conflicts |
| `PotentialHardConflicts` | `Placement.NrPotentialConflictsWeight` | Potential future conflicts |
| `DeltaTimePreference` | `Placement.DeltaTimePreferenceWeight` | Delta of time preference vs best possible |
| `AssignmentCount` | `Placement.NrAssignmentsWeight` | Number of previous assignments (deprecated) |

---

### 5.19 Additional Criteria (via `General.AdditionalCriteria`)

| Class | Weight Property | Default Weight | Score |
|-------|----------------|----------------|-------|
| `ImportantStudentConflict` | `Comparator.ImportantStudentConflictWeight` | $3 \times$ StudentConflictWeight | Uses `jenrl.priority()` for weighting |
| `ImportantStudentHardConflict` | `Comparator.ImportantHardStudentConflictWeight` | $3 \times$ HardStudentConflictWeight | Hard + important only |
| `InstructorStudentConflict` | `Comparator.InstructorStudentConflictWeight` | $10 \times$ StudentConflictWeight | Conflicts involving instructor-students |
| `InstructorStudentHardConflict` | `Comparator.InstructorHardStudentConflictWeight` | $10 \times$ HardStudentConflictWeight | Hard variant |
| `InstructorLunchBreak` | `InstructorLunch.Weight` | 0.3 | Lunch break availability for instructors |
| `InstructorFairness` | `Comparator.InstructorFairnessPreferenceWeight` | 1.0 | Fairness of time preferences across instructors |
| `InstructorConflict` | `Comparator.InstructorConflictWeight` | 100.0 | Soft instructor overlap (when `General.SoftInstructorConstraints`) |
| `RoomSizePenalty` | `Comparator.RoomSizeWeight` | 0.001 | $\max(0, \text{size} - \text{minSize})^{f}$, $f$ = `Comparator.RoomSizeFactor` (1.05) |
| `QuadraticStudentConflict` | `Comparator.StudentConflictWeight` | 1.0 | $n_{ij}^2$ instead of $n_{ij}$ |
| `StudentMinimizeScheduleHoles` | `Comparator.MinimizeStudentScheduleHolesWeight` | $0.05 \times$ StudentConflictWeight | Gap hours × jenrl |
| `StudentMinimizeDaysOfWeek` | `Comparator.MinimizeStudentScheduleDaysWeight` | $0.05 \times$ StudentConflictWeight | Day spread × jenrl |
| `StudentLuchBreak` | `Comparator.StudentLunchWeight` | StudentConflictWeight | Student lunch availability |
| `StudentOverLunchConflict` | `Comparator.StudentOverLunchConflictWeight` | $0.1 \times$ StudentConflictWeight | Morning vs afternoon split |
| `StudentOnlineConflict` | `Comparator.StudentOnlineConflictWeight` | $0.5 \times$ StudentConflictWeight | Online + in-person same day |
| `IgnoredStudentConflict` | `Comparator.IgnoredStudentConflictWeight` | 0.0 | Tracks ignored conflicts |
| `IgnoredCommittedStudentConflict` | `Comparator.IgnoredCommitedStudentConflictWeight` | 0.0 | Tracks ignored committed conflicts |

---

## 6. Preferences and Priorities

### 6.1 Preference Scale

Defined in [cpsolver:src/org/cpsolver/coursett/Constants.java](../../../cpsolver/src/org/cpsolver/coursett/Constants.java#L27):

| Prolog Code | Name | Integer Level | Meaning |
|-------------|------|:---:|---------|
| `R` | Required | -100 | Must be satisfied (hard) |
| `-2` | Strongly Preferred | -4 | Strong positive preference |
| `-1` | Preferred | -1 | Positive preference |
| `0` | Neutral | 0 | No preference |
| `1` | Discouraged | 1 | Mild negative preference |
| `2` | Strongly Discouraged | 4 | Strong negative preference |
| `P` | Prohibited | 100 | Must not be used (hard) |
| `N` | Not Available | 100 | Same as prohibited for scheduling |

**Key insight:** Required and Prohibited are treated as **hard** constraints (filtered from domains or enforced by `computeConflicts`). The numeric levels -4 through 4 are used as soft penalty weights. The asymmetry (-4 vs 4, not -2 vs 2) makes strongly preferred/discouraged carry 4× the weight of preferred/discouraged.

### 6.2 Time Preference Normalization

Time preferences from the UI grid (per day×time cell) are normalized to account for the number of available time options. The normalization is computed in `unitime:JavaSource/org/unitime/timetable/model/TimePatternModel.java`:

1. For each time slot, the "raw" preference is the integer level from the grid.
2. The normalized preference applies a decay factor for multiple time options:

$$\hat{\pi}(t) = \pi(t) \times \left(\frac{1}{f}\right)^{n-1}$$

where $f$ = `General.NormalizedPrefDecreaseFactor` (default 0.77) and $n$ is the position when preferences are sorted by absolute value (best options contribute more).

Special cases:
- Required ($\pi = -100$): normalized to 0 (no penalty, just domain filtering)
- Prohibited ($\pi = 100$): removed from domain entirely

The normalized preference is stored in `TimeLocation.iNormalizedPreference` and passed to the constructor.

### 6.3 Time Preference Merging (Instructor + Class + Subpart)

Instructor time preferences are **merged** with class/subpart time preferences before the solver starts:

1. `Class_.effectivePreferences(TimePref.class)` collects time preferences from the class, its scheduling subpart, and its assigned instructors.
2. `TimePref.combineWith()` performs cell-by-cell merging on `TimePatternModel`.
3. The merged preferences produce the `TimeLocation` objects in `TimetableDatabaseLoader.loadClass()`.

The solver sees a single merged preference per time slot — it does not distinguish between instructor and class preferences at runtime.

**Code pointers:**
- `Class_.effectivePreferences()`: `unitime:JavaSource/org/unitime/timetable/model/Class_.java`
- `TimePref.combineWith()`: `unitime:JavaSource/org/unitime/timetable/model/TimePref.java` lines 105–115
- `TimetableDatabaseLoader.loadClass()`: time pref expansion at lines ~1046–1191

### 6.4 Room Preferences

Room preferences are integer levels assigned to each `RoomLocation`. `Placement.getRoomPenalty()` sums room preferences for all rooms in the placement. Rooms with prohibited preference are excluded from the domain.

### 6.5 Distribution Preferences

For soft distribution constraints, the preference level acts as a multiplier on the violation count (see Section 4.4).

---

## 7. Minimum Perturbation Problem (MPP)

### 7.1 Overview

When `General.MPP = true`, the solver operates in Minimum Perturbation mode. The goal is to find a new solution that is as close as possible to an initial (previous) solution while still satisfying all constraints.

**Activation:**
- UI: `Basic.Mode = "MPP"` in solver settings
- Code: `CourseTimetablingSolverService.createConfig()` sets `General.MPP = true` and adds `ViolatedInitials` extension

### 7.2 Perturbation Penalty Formulation

The perturbation penalty is computed by `UniversalPerturbationsCounter.getPenalty()` ([cpsolver:src/org/cpsolver/coursett/heuristics/UniversalPerturbationsCounter.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/UniversalPerturbationsCounter.java#L299), lines 299–388).

For each lecture $\ell_i$ that has an initial placement $p_i^0$ and a current assignment $p_i \neq p_i^0$:

$$P(\ell_i) = \sum_{k} w_k \cdot f_k(\ell_i, p_i, p_i^0)$$

The 20 weighted terms are:

| # | Term $f_k$ | Weight Property | Default | Description |
|---|-----------|----------------|---------|-------------|
| 1 | 1 | `Perturbations.DifferentPlacement` | 0.0 | Constant per moved variable |
| 2 | $\text{classLimit}(\ell_i)$ | `Perturbations.AffectedStudentWeight` | 0.1 | Students affected by any change |
| 3 | $|\text{instructors}(\ell_i)|$ | `Perturbations.AffectedInstructorWeight` | 0.0 | Instructors affected by any change |
| 4 | $\text{nrDiffRooms}(p_i, p_i^0)$ | `Perturbations.DifferentRoomWeight` | 0.0 | Number of different rooms |
| 5 | $\text{nrDiffRooms} \times |\text{instructors}|$ | `Perturbations.AffectedInstructorByRoomWeight` | 0.0 | Instructors affected by room change |
| 6 | $\text{nrDiffRooms} \times \text{classLimit}$ | `Perturbations.AffectedStudentByRoomWeight` | 0.0 | Students affected by room change |
| 7 | $\text{nrDiffBldg}(p_i, p_i^0)$ | `Perturbations.DifferentBuildingWeight` | 0.0 | Number of different buildings |
| 8 | $\text{nrDiffBldg} \times |\text{instructors}|$ | `Perturbations.AffectedInstructorByBldgWeight` | 0.0 | Instructors affected by building change |
| 9 | $\text{nrDiffBldg} \times \text{classLimit}$ | `Perturbations.AffectedStudentByBldgWeight` | 0.0 | Students affected by building change |
| 10 | $\mathbb{1}[t_i \neq t_i^0]$ | `Perturbations.DifferentTimeWeight` | 0.0 | Time changed indicator |
| 11 | $\mathbb{1}[t_i \neq t_i^0] \times |\text{instructors}|$ | `Perturbations.AffectedInstructorByTimeWeight` | 0.0 | Instructors affected by time change |
| 12 | $\mathbb{1}[t_i \neq t_i^0] \times \text{classLimit}$ | `Perturbations.AffectedStudentByTimeWeight` | 0.0 | Students affected by time change |
| 13 | $\mathbb{1}[\text{day}(t_i) \neq \text{day}(t_i^0)]$ | `Perturbations.DifferentDayWeight` | 0.0 | Day changed indicator |
| 14 | $\mathbb{1}[\text{slot}(t_i) \neq \text{slot}(t_i^0)]$ | `Perturbations.DifferentHourWeight` | 0.0 | Hour changed indicator |
| 15 | Instructor distance tier penalty | `Perturbations.TooFarForInstructorsWeight` | 0.0 | Distance from old to new room (tiered) |
| 16 | $\text{classLimit} \times \mathbb{1}[d > 10\text{min}]$ | `Perturbations.TooFarForStudentsWeight` | 0.0 | Students affected by large distance |
| 17 | $\text{newConflicts} - \text{oldConflicts}$ | `Perturbations.DeltaStudentConflictsWeight` | 0.0 | Delta student conflict count |
| 18 | Count of new individual conflicts | `Perturbations.NewStudentConflictsWeight` | 0.0 | Students in conflict not in initial |
| 19 | $\hat{\pi}(t_i) - \hat{\pi}(t_i^0)$ | `Perturbations.DeltaTimePreferenceWeight` | 0.0 | Time preference quality delta |
| 20 | $\sum_r \rho(r) - \sum_r \rho(r^0)$ | `Perturbations.DeltaRoomPreferenceWeight` | 0.0 | Room preference quality delta |

**Instructor distance tiers (term 15):**
- $0 < d \leq d_0$: penalty = $1 \times w_{15} \times |\text{instructors}|$
- $d_0 < d \leq d_1$: penalty = $4 \times w_{15} \times |\text{instructors}|$
- $d > d_1$: penalty = $100 \times w_{15} \times |\text{instructors}|$

(plus an additional `DeltaInstructorDistancePreference` term, weight property `Perturbations.DeltaInstructorDistancePreferenceWeight`, comparing BTB distance preferences with paired lectures before/after the change)

**Total perturbation penalty:**
$$P_{\text{total}}(\sigma) = \sum_{i : p_i \neq p_i^0 \wedge p_i^0 \neq \bot} P(\ell_i)$$

### 7.3 MPP Integration with Objective

The perturbation penalty is a standard criterion in the objective:

$$\text{Obj}_{\text{MPP}}(\sigma) = \text{Obj}(\sigma) + w_{\text{pert}} \cdot P_{\text{total}}(\sigma)$$

where $w_{\text{pert}}$ = `Comparator.PerturbationPenaltyWeight` (default 1.0).

### 7.4 MPP Termination Condition

`MPPTerminationCondition` ([cpsolver:src/org/cpsolver/ifs/termination/MPPTerminationCondition.java](../../../cpsolver/src/org/cpsolver/ifs/termination/MPPTerminationCondition.java#L81)) stops when:

1. Complete solution found AND $|\mathcal{P}| \leq$ `Termination.MinPerturbances` (default -1, disabled)
2. Complete solution found AND $P_{\text{total}} \leq$ `Termination.MinPerturbationPenalty` (default -1, disabled)
3. Iteration count $\geq$ `Termination.MaxIters` (default -1, disabled)
4. Time $>$ `Termination.TimeOut` (default 1800 seconds)
5. If `Termination.StopWhenComplete = true`: stop on first complete solution

### 7.5 MPP Value Selection

`PlacementSelection` gives initial placements special treatment in MPP:
- With probability `Placement.MPP_InitialProb` (default 0.20), select the initial placement if it exists
- If perturbation count exceeds `Placement.MPP_Limit` or perturbation penalty exceeds `Placement.MPP_PenaltyLimit`, force initial placement
- Among equally good placements, prefer the initial assignment

### 7.6 ViolatedInitials Extension

When MPP is active, `ViolatedInitials` extension ([cpsolver:src/org/cpsolver/ifs/extension/ViolatedInitials.java](../../../cpsolver/src/org/cpsolver/ifs/extension/ViolatedInitials.java#L51)) tracks which initial placements are violated by current assignments.

---

## 8. Search / Optimization Algorithm

### 8.1 IFS Main Loop

The solver loop is in `Solver.SolverThread.run()` ([cpsolver:src/org/cpsolver/ifs/solver/Solver.java](../../../cpsolver/src/org/cpsolver/ifs/solver/Solver.java#L638), lines 638–687):

```
INITIALIZE solver
WHILE termination condition allows:
    neighbour ← neighbourSelection.selectNeighbour(solution)
    IF neighbour is null:
        update solution stats
        CONTINUE
    LOCK solution (write)
    neighbour.assign(assignment, iteration)
    UNLOCK solution
    update solution (time, iteration)
    IF solution is best so far:
        save best solution
```

`ParallelSolver` ([cpsolver:src/org/cpsolver/ifs/solver/ParallelSolver.java](../../../cpsolver/src/org/cpsolver/ifs/solver/ParallelSolver.java#L66)) runs multiple `SolverThread` instances sharing the same solution, controlled by `Parallel.NrSolvers`.

### 8.2 Variable Selection (LectureSelection)

[cpsolver:src/org/cpsolver/coursett/heuristics/LectureSelection.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/LectureSelection.java#L178)

**When unassigned variables exist:**
1. With probability `Lecture.RandomWalkProb` (default 1.0): pick random unassigned lecture
2. Otherwise: roulette-wheel selection over a subset using weights:
   - Domain size: `Lecture.DomainSizeWeight` (default 30.0) — smaller domain preferred
   - Assignment count: `Lecture.NrAssignmentsWeight` (default 10.0)
   - Constraint count: `Lecture.NrConstraintsWeight` (default 0.0)
   - MPP initial weight: `Lecture.InitialAssignmentWeight` (default 20.0) — lectures with conflicting initials preferred

**When all variables assigned (improvement):**
1. Select from `perturbVariables` (or all assigned if empty)
2. With probability `Lecture.RandomWalkProb`: random selection
3. Otherwise: pick the variable with **worst** `placement.toDouble()` value (most improvable)

### 8.3 Value Selection (PlacementSelection)

[cpsolver:src/org/cpsolver/coursett/heuristics/PlacementSelection.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/PlacementSelection.java#L188)

Uses a **3-level hierarchical** evaluation with threshold filtering:

**Level 1 (construction):** fast filtering using weights like `Placement.NrConflictsWeight1` (default 1.0), `Placement.WeightedConflictsWeight1` (default 2.0), `Placement.NrHardStudConfsWeight1` (default 0.3), etc.

**Level 2 (optimization):** uses `Comparator.*Weight` values for each criterion (defaults match the objective weights).

**Level 3:** disabled by default (all weights 0.0).

Each level computes:
$$\text{cost}(\text{level}, p) = \sum_{k} w_k^{\text{level}} \cdot C_k(\sigma \cup \{p\}) $$

where $w_k^{\text{level}}$ = `criterion.getPlacementSelectionWeight(level, threadIndex)`.

**Threshold filtering:** Level 1 uses `Placement.ThresholdKoef1` (default 0.1) — values within 10% of the best level-1 score are kept for level-2 evaluation.

**Special MPP behavior:**
- `Placement.MPP_InitialProb` (default 0.20): probability of selecting initial placement
- `Placement.MPP_Limit`: if perturbation count exceeds this, force initial
- `Placement.MPP_PenaltyLimit`: if perturbation penalty exceeds this, force initial

**Random walk:** `Placement.RandomWalkProb` (default 0.00): probability of random value selection.

**Tabu list:** `Placement.TabuLength` (default -1, disabled): recently assigned values are excluded.

### 8.4 FixCompleteSolutionNeighbourSelection

[cpsolver:src/org/cpsolver/coursett/heuristics/FixCompleteSolutionNeighbourSelection.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/FixCompleteSolutionNeighbourSelection.java#L48)

The default neighbour selection for the IFS/Legacy algorithm. Wraps `NeighbourSelectionWithSuggestions` and adds local improvement phases:

**Phase 0 (normal):** Delegate to parent (`NeighbourSelectionWithSuggestions`).

**Phase 1 (fix incomplete/complete):** Triggered when a best solution is found. Iterates all lectures, tries to find a conflict-free swap that improves `toDouble()`.

**Phase 2 (deep fix):** Uses `NeighbourSelectionWithSuggestions.selectNeighbourWithSuggestions()` with depth 2 for deeper local search.

**Intervals:**
- `General.CompleteSolutionFixInterval` (default 1): iterations between complete-solution fixes
- `General.IncompleteSolutionFixInterval` (default 5000): iterations between incomplete fixes

### 8.5 NeighbourSelectionWithSuggestions

[cpsolver:src/org/cpsolver/coursett/heuristics/NeighbourSelectionWithSuggestions.java](../../../cpsolver/src/org/cpsolver/coursett/heuristics/NeighbourSelectionWithSuggestions.java#L51)

Extends the standard IFS variable+value selection with **backtracking suggestions**: when a value creates conflicts, recursively try to resolve them up to a configurable depth.

**Configuration:**
- `Neighbour.SuggestionProbability` (default 0.1): probability of using suggestion search
- `Neighbour.SuggestionProbabilityAllAssigned` (default 0.5): probability when all assigned
- `Neighbour.SuggestionTimeout` (default 500ms)
- `Neighbour.SuggestionDepth` (default 4)

### 8.6 SimpleSearch (Alternative Algorithm)

Activated by `General.SearchAlgorithm = Experimental/Deluge/GD/SA/Annealing`.

Uses [cpsolver:src/org/cpsolver/ifs/algorithms/SimpleSearch.java](../../../cpsolver/src/org/cpsolver/ifs/algorithms/SimpleSearch.java#L55) with phases:
1. **Hill Climbing:** accept only improving moves
2. **Great Deluge / Simulated Annealing:** accept worsening moves with decreasing probability/bound

**Neighbourhood operators** (from package `org.cpsolver.coursett.neighbourhoods`, e.g. [cpsolver:src/org/cpsolver/coursett/neighbourhoods/TimeChange.java](../../../cpsolver/src/org/cpsolver/coursett/neighbourhoods/TimeChange.java#L45)):

| Operator | Description |
|----------|-------------|
| `TimeChange` | Change time of a single lecture (keep room) |
| `RoomChange` | Change room of a single lecture (keep time) |
| `TimeSwap` | Change time with recursive conflict resolution |
| `RoomSwap` | Swap rooms with recursive conflict resolution |
| `Suggestion` | Backtracking suggestion search |

### 8.7 Conflict-Based Statistics

When `General.CBS = true`, `ConflictStatistics` extension tracks how often each variable-value pair appears in conflicts. This information feeds into `WeightedHardConflicts` and `PotentialHardConflicts` placement-selection criteria to guide the search away from frequently conflicting assignments.

---

## 9. UniTime Integration Layer

### 9.1 Solver Configuration

`unitime:JavaSource/org/unitime/timetable/solver/service/CourseTimetablingSolverService.java`, method `createConfig()`:

1. Loads all `SolverParameterDef` rows for `SolverType.COURSE` from database
2. Overlays selected `SolverPredefinedSetting` parameters
3. Applies user option overrides
4. Derives runtime properties:
   - **Search algorithm** → `Neighbour.Class` (IFS default or SimpleSearch)
   - **Extensions** → `SearchIntensification`, `ConflictStatistics`, `ViolatedInitials` (MPP)
   - **Mode** → `General.MPP`, `General.InteractiveMode`
   - **Additional criteria** conditional registration (instructor conflicts, lunch, room size)
   - **Student sectioning** variant selection
   - **Parallel solvers** count
5. Calls `properties.expand()` to resolve `%Property%` references

### 9.2 Database Loading

`unitime:JavaSource/org/unitime/timetable/solver/TimetableDatabaseLoader.java`:

- Creates `Lecture` objects from `Class_` entities with time/room domains
- Creates constraints: `RoomConstraint`, `InstructorConstraint`, `GroupConstraint`, `SpreadConstraint`, `DepartmentSpreadConstraint`, `JenrlConstraint`, `ClassLimitConstraint`, flexible constraints
- Loads student enrollments, instructor assignments, committed solutions
- Merges time preferences from class + subpart + instructor
- Handles MPP: loads initial placements when `General.MPP = true`, with optional `General.MPP.FixedTimes`

### 9.3 Key Properties Affecting Model Construction

| Property | Default | Effect |
|----------|---------|--------|
| `General.MPP` | `false` | Enable minimum perturbation mode |
| `General.MPP.FixedTimes` | `false` | Fix times in MPP (only room changes allowed) |
| `General.DeptBalancing` | `false` | Enable department spread constraints |
| `General.Spread` | `true` | Enable same-subpart spread constraints |
| `General.InteractiveMode` | `false` | Allow prohibited time/room violations (tracked) |
| `General.SoftInstructorConstraints` | `false` | Make instructor constraints soft |
| `General.AutoSameStudents` | `true` | Auto-create same-students constraints |
| `General.WeakenDistributions` | `false` | Weaken required/prohibited to preferred/discouraged |
| `General.AllowProhibitedRooms` | `false` | Allow prohibited room placements (tracked) |
| `General.UseDistanceConstraints` | `true` | Enable instructor distance constraints |
| `General.CommittedStudentConflicts` | `Load` | How to handle committed student conflicts |
| `General.LoadCommittedAssignments` | `false` | Load committed class placements |
| `General.NormalizedPrefDecreaseFactor` | `0.77` | Time preference normalization decay |

---

## 10. Appendix: Property Keys

### 10.1 Objective Weights (`Comparator.*`)

| Property | Default | Used By | Meaning |
|----------|:---:|---------|---------|
| `Comparator.HardStudentConflictWeight` | 0.8* | `StudentHardConflict` | Weight for hard (single-section) student conflicts |
| `Comparator.StudentConflictWeight` | 0.2* | `StudentOverlapConflict` | Weight for general student overlap conflicts |
| `Comparator.TimePreferenceWeight` | 0.3* | `TimePreferences` | Weight for time preference penalties |
| `Comparator.ContrPreferenceWeight` | 2.0* | `DistributionPreferences` | Weight for distribution constraint violations |
| `Comparator.RoomPreferenceWeight` | 1.0* | `RoomPreferences` | Weight for room preference penalties |
| `Comparator.UselessSlotWeight` | 0.1 | `UselessHalfHours`, `BrokenTimePatterns` | Weight for useless slot penalties |
| `Comparator.TooBigRoomWeight` | 0.1 | `TooBigRooms` | Weight for too-big-room penalties |
| `Comparator.DistanceInstructorPreferenceWeight` | 1.0 | `BackToBackInstructorPreferences` | Weight for instructor BTB distance |
| `Comparator.PerturbationPenaltyWeight` | 1.0 | `Perturbations` | Weight for MPP perturbation penalty |
| `Comparator.DeptSpreadPenaltyWeight` | 1.0 | `DepartmentBalancingPenalty` | Weight for dept balancing |
| `Comparator.SpreadPenaltyWeight` | 1.0 | `SameSubpartBalancingPenalty` | Weight for subpart balancing |
| `Comparator.CommitedStudentConflictWeight` | 1.0 | `StudentCommittedConflict` | Weight for committed conflicts |
| `Comparator.DistStudentConflictWeight` | 0.2 | `StudentDistanceConflict` | Weight for distance-based student conflicts |
| `Comparator.ImportantStudentConflictWeight` | 0.0 | `ImportantStudentConflict` | Weight for important student conflicts |
| `Comparator.ImportantHardStudentConflictWeight` | 0.0 | `ImportantStudentHardConflict` | Weight for important hard student conflicts |
| `Comparator.InstructorStudentConflictWeight` | 10.0* | `InstructorStudentConflict` | Weight for instructor-student conflicts |
| `Comparator.InstructorHardStudentConflictWeight` | 50.0* | `InstructorStudentHardConflict` | Weight for hard instructor-student conflicts |
| `Comparator.InstructorConflictWeight` | 100.0 | `InstructorConflict` | Soft instructor overlap weight |
| `Comparator.InstructorFairnessPreferenceWeight` | 1.0 | `InstructorFairness` | Instructor fairness weight |
| `Comparator.RoomSizeWeight` | 0.001 | `RoomSizePenalty` | Room size penalty weight |
| `Comparator.WorkDayStudentConflictWeight` | 0.2 | `StudentWorkdayConflict` | Workday conflict weight |

*Values marked with * are the defaults from code; the UniTime DB seed data (`blank-data.sql`) may override these with different values (see Section 9).

### 10.2 Placement Selection Weights (`Placement.*`)

Each criterion has three levels of placement selection weights (`Weight1/2/3`). Key examples:

| Property | Default (Level 1) | Default (Level 2) | Used By |
|----------|:---:|:---:|---------|
| `Placement.NrConflictsWeight` | 1.0 | 0.0 | `HardConflicts` |
| `Placement.WeightedConflictsWeight` | 2.0 | 0.0 | `WeightedHardConflicts` |
| `Placement.NrHardStudConfsWeight` | 0.3 | `%Comparator.HardStudentConflictWeight%` | `StudentHardConflict` |
| `Placement.NrStudConfsWeight` | 0.05 | `%Comparator.StudentConflictWeight%` | `StudentOverlapConflict` |
| `Placement.TimePreferenceWeight` | 0.0 | `%Comparator.TimePreferenceWeight%` | `TimePreferences` |
| `Placement.DeltaTimePreferenceWeight` | 0.2 | 0.0 | `DeltaTimePreference` |
| `Placement.ConstrPreferenceWeight` | 0.25 | `%Comparator.ContrPreferenceWeight%` | `DistributionPreferences` |
| `Placement.RoomPreferenceWeight` | 0.1 | `%Comparator.RoomPreferenceWeight%` | `RoomPreferences` |
| `Placement.UselessSlotsWeight` | 0.0 | `%Comparator.UselessSlotWeight%` | `UselessHalfHours`, `BrokenTimePatterns` |
| `Placement.TooBigRoomWeight` | 0.01 | `%Comparator.TooBigRoomWeight%` | `TooBigRooms` |
| `Placement.DistanceInstructorPreferenceWeight` | 0.1 | `%Comparator.DistanceInstructorPreferenceWeight%` | `BackToBackInstructorPreferences` |
| `Placement.DeptSpreadPenaltyWeight` | 0.1 | `%Comparator.DeptSpreadPenaltyWeight%` | `DepartmentBalancingPenalty` |
| `Placement.SpreadPenaltyWeight` | 0.1 | `%Comparator.SpreadPenaltyWeight%` | `SameSubpartBalancingPenalty` |
| `Placement.MPP_DeltaInitialAssignmentWeight` | 0.1 | `%Comparator.PerturbationPenaltyWeight%` | `Perturbations` |
| `Placement.NrCommitedStudConfsWeight` | 0.5 | `%Comparator.CommitedStudentConflictWeight%` | `StudentCommittedConflict` |
| `Placement.ThresholdKoef1` | 0.1 | — | Threshold for level-1 filtering |
| `Placement.ThresholdKoef2` | 0.1 | — | Threshold for level-2 filtering |
| `Placement.RandomWalkProb` | 0.00 | — | Random value selection probability |
| `Placement.MPP_InitialProb` | 0.20 | — | MPP initial value selection probability |
| `Placement.MPP_Limit` | -1 | — | MPP perturbation count limit |
| `Placement.MPP_PenaltyLimit` | -1.0 | — | MPP perturbation penalty limit |
| `Placement.TabuLength` | -1 | — | Tabu list length |

### 10.3 Variable Selection Weights (`Lecture.*`)

| Property | Default | Meaning |
|----------|:---:|---------|
| `Lecture.RouletteWheelSelection` | `true` | Use roulette wheel for unassigned selection |
| `Lecture.RandomWalkProb` | 1.0 | Random walk probability |
| `Lecture.DomainSizeWeight` | 30.0 | Smaller domain → higher selection probability |
| `Lecture.NrAssignmentsWeight` | 10.0 | More previous assignments → higher probability |
| `Lecture.InitialAssignmentWeight` | 20.0 | MPP: weight for lectures with conflicting initials |
| `Lecture.NrConstraintsWeight` | 0.0 | Constraint count weight |
| `Lecture.SelectionSubSet` | `true` | Use subset selection |
| `Lecture.SelectionSubSetMinSize` | 10 | Minimum subset size |
| `Lecture.SelectionSubSetPart` | 0.2 | Fraction of variables in subset |

### 10.4 Perturbation Weights (`Perturbations.*`)

| Property | Default | Meaning |
|----------|:---:|---------|
| `Perturbations.DifferentPlacement` | 0.0 | Base penalty per moved variable |
| `Perturbations.AffectedStudentWeight` | 0.1 | × classLimit for any change |
| `Perturbations.AffectedInstructorWeight` | 0.0 | × instructorCount for any change |
| `Perturbations.DifferentRoomWeight` | 0.0 | Per different room |
| `Perturbations.DifferentBuildingWeight` | 0.0 | Per different building |
| `Perturbations.DifferentTimeWeight` | 0.0 | Per time change |
| `Perturbations.DifferentDayWeight` | 0.0 | Per day change |
| `Perturbations.DifferentHourWeight` | 0.0 | Per hour change |
| `Perturbations.AffectedStudentByTimeWeight` | 0.0 | × classLimit for time change |
| `Perturbations.AffectedInstructorByTimeWeight` | 0.0 | × instructorCount for time change |
| `Perturbations.AffectedStudentByRoomWeight` | 0.0 | × classLimit for room change |
| `Perturbations.AffectedInstructorByRoomWeight` | 0.0 | × instructorCount for room change |
| `Perturbations.AffectedStudentByBldgWeight` | 0.0 | × classLimit for building change |
| `Perturbations.AffectedInstructorByBldgWeight` | 0.0 | × instructorCount for building change |
| `Perturbations.DeltaStudentConflictsWeight` | 0.0 | New minus old student conflicts |
| `Perturbations.NewStudentConflictsWeight` | 0.0 | Truly new student conflicts |
| `Perturbations.TooFarForInstructorsWeight` | 0.0 | Distance tiers × instructorCount |
| `Perturbations.TooFarForStudentsWeight` | 0.0 | × classLimit if distance > 10min |
| `Perturbations.DeltaTimePreferenceWeight` | 0.0 | Time pref quality delta |
| `Perturbations.DeltaRoomPreferenceWeight` | 0.0 | Room pref quality delta |
| `Perturbations.DeltaInstructorDistancePreferenceWeight` | 0.0 | Instructor distance pref delta |

### 10.5 Termination and Search Properties

| Property | Default | Meaning |
|----------|:---:|---------|
| `Termination.TimeOut` | 1800 | Max solver time in seconds |
| `Termination.MaxIters` | -1 | Max iterations (-1 = unlimited) |
| `Termination.StopWhenComplete` | `false` | Stop on first complete solution |
| `Termination.MinPerturbances` | -1 | MPP: stop when perturbations ≤ this |
| `General.SearchAlgorithm` | `Default` | Algorithm selection (Default/IFS/Experimental/Deluge/SA) |
| `General.CompleteSolutionFixInterval` | 1 | Iterations between complete-solution local search |
| `General.IncompleteSolutionFixInterval` | 5000 | Iterations between incomplete-solution local search |
| `General.SaveBestUnassigned` | -1 | Max unassigned to save as best |
| `SearchIntensification.IterationLimit` | 100 | Intensification iteration limit |
| `SearchIntensification.ResetInterval` | 5 | Reset interval for intensification |
| `Parallel.NrSolvers` | auto | Number of parallel solver threads |

### 10.6 Constraint Configuration

| Property | Default | Meaning |
|----------|:---:|---------|
| `General.JenrlMaxConflicts` | 1.0 | Fraction of min class limit for hard student conflict threshold |
| `General.JenrlMaxConflictsWeaken` | 0.001 | Weakening increment per stuck iteration |
| `Spread.SpreadFactor` | 1.2 | Subpart spread tolerance factor |
| `Spread.Unassignments2Weaken` | 50 | Unassignments before weakening spread |
| `DeptBalancing.SpreadFactor` | 1.2 | Department spread tolerance |
| `DeptBalancing.Unassignments2Weaken` | 0 | Unassignments before weakening dept spread |
| `DiscouragedRoom.Unassignments2Weaken` | 1000 | Unassignments before weakening discouraged room |
| `Instructor.NoPreferenceLimit` | 0.0 | BTB distance below which no preference |
| `Instructor.DiscouragedLimit` | 50.0 | BTB distance for discouraged pref |
| `Instructor.ProhibitedLimit` | 200.0 | BTB distance for prohibited pref |
| `Student.DistanceLimit` | 67.0 | Student BTB distance limit (meters) |
| `StudentConflict.WorkDayLimit` | -1 | Max slots per student workday (-1 = disabled) |
| `TooBigRooms.DiscouragedRoomSize` | 1.25 | Room size ratio for discouraged |
| `TooBigRooms.StronglyDiscouragedRoomSize` | 1.5 | Room size ratio for strongly discouraged |
| `FlexibleConstraint.Weight` | 1.0 | Soft flexible constraint weight |
| `General.NormalizedPrefDecreaseFactor` | 0.77 | Time pref normalization decay |

### 10.7 Model Configuration

| Property | Default | Meaning |
|----------|:---:|---------|
| `General.MPP` | `false` | Enable minimum perturbation mode |
| `General.MPP.FixedTimes` | `false` | Fix times in MPP |
| `General.CBS` | `true` | Enable conflict-based statistics |
| `General.SearchIntensification` | `true` | Enable search intensification extension |
| `General.InteractiveMode` | `false` | Allow prohibited placements |
| `General.SoftInstructorConstraints` | `false` | Make instructor constraints soft |
| `General.DeptBalancing` | `false` | Enable department balancing |
| `General.Spread` | `true` | Enable same-subpart spreading |
| `General.SwitchStudents` | `true` | Enable student sectioning at end |
| `General.AutoSameStudents` | `true` | Auto same-students constraints |
| `General.UseDistanceConstraints` | `true` | Enable instructor distance |
| `General.AllowBreakHard` | `false` | Allow hard constraint violations |
| `General.AllowProhibitedRooms` | `false` | Allow prohibited room assignments |
| `General.WeakenDistributions` | `false` | Weaken required/prohibited distributions |
| `General.PurgeInvalidPlacements` | `true` | Remove invalid placements from domains |
| `General.AutoPrecedence` | `Neutral` | Auto-generate precedence constraints |
| `General.Criteria` | (see Section 3.4) | Semicolon-separated list of criterion classes |
| `General.AdditionalCriteria` | ImportantStudent* | Additional criterion classes |
| `General.GlobalConstraints` | (empty) | Additional global constraint classes |

---

*Generated by reverse-engineering UniTime/cpsolver v1.4.x and UniTime/unitime source code.*
