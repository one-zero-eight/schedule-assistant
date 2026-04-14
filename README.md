# Schedule assistant

CP-SAT (OR-Tools) timetabling: reads a **YAML schedule config**, writes a solved schedule and optional solver logs under `results/`.

## Setup

Requires **Python ≥ 3.12**. Dependencies are declared in `pyproject.toml`; install with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Run commands with `uv run …` so they use the project environment (examples below).


## Generate configs 

```bash
uv run convert_json_to_config_candidate.py core-courses-lessons-spring-2026.json
```

```bash
uv run python tests/cases/generate_cases.py
```

## Run the solver (`main.py`)

```bash
uv run python main.py tests/cases/feasible_one_week_full_with_english_block1/all_bachelors_masters_phd_with_english.yaml --time-limit 10
```

**Output:** prints `status` and `stats` to stdout. Writes the full result to:

`results/<YYYY-mm-dd_HH-MM-SS>_<term-slug>/output.yaml`

Solver phase logs may appear as `solver_log_phase_*.txt` in that same folder.

## Check metrics (`metrics.py`)

Compares the **config** with a **solver output** and prints a human-readable report (conflicts, soft-constraint satisfaction, loads, room use, etc.).

```bash
uv run python metrics.py \
  --config tests/cases/feasible_one_week_full_with_english_block1/all_bachelors_masters_phd_with_english.yaml \
  --solution "$(ls -1t results/*/output.yaml | head -n1)"
  --short
```

For machine-readable output:

```bash
uv run python metrics.py \
  --config tests/cases/feasible_one_week_full_with_english_block1/all_bachelors_masters_phd_with_english.yaml \
  --solution results/<timestamp>_<term-slug>/output.yaml \
  --json
```

Use the `output.yaml` from the same `main.py` run (under `results/...`).

## Tests

```bash
uv run pytest # NOTE THEY VERY SLOW
```
