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
uv run tests/cases/generate_cases.py
```

## Run the solver (`main.py`)

```bash
uv run main.py tests/cases/feasible_by_program_year_block1/core_year_1.yaml
```

Usage:
```txt
dante@dante-pc:~/PycharmProjects/schedule-assistant$ uv run main.py --help
usage: main.py [-h] [--time-limit TIME_LIMIT] [--num-workers NUM_WORKERS] [--artifacts-dir ARTIFACTS_DIR] [--no-progress] config

positional arguments:
  config

options:
  -h, --help            show this help message and exit
  --time-limit TIME_LIMIT
  --num-workers NUM_WORKERS
  --artifacts-dir ARTIFACTS_DIR
  --no-progress
```


**Output:** prints `status` and `stats` to stdout. Writes the full result to:

`results/<YYYY-mm-dd_HH-MM-SS>_<term-slug>/output.yaml` or `--artifacts-dir <path>`

Solver phase logs may appear as `solver_log_phase_*.txt` in that same folder.

For experiments better to pass `--artifacts-dir <path>` to avoid clobbering the default `results/` directory and to be able to run several experiments in parallel.

## Check metrics (`metrics.py`)

Compares the **config** with a **solver output** and prints a human-readable report (conflicts, soft-constraint satisfaction, loads, room use, etc.).

```bash
uv run metrics.py \
  --config tests/cases/feasible_by_program_year_block1/core_year_1.yaml \
  --solution "$(ls -1t results/*/output.yaml | head -n1)"
```

For machine-readable output:

```bash
uv run metrics.py \
  --config tests/cases/feasible_by_program_year_block1/core_year_1.yaml \
  --solution results/<timestamp>_<term-slug>/output.yaml \
  --json
```

Use the `output.yaml` from the same `main.py` run (under `results/...`).

## Get cpsat-primer examples and README.md

```bash
git clone https://github.com/d-krupke/cpsat-primer --depth 1
cd cpsat-primer
mv README.md ../cpsat-primer-README.md
mv examples ../cpsat-primer-examples
cd ..
rm -rf cpsat-primer
```

## Tests

```bash
uv run pytest # NOTE THEY VERY SLOW
```
