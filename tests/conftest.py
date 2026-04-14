from __future__ import annotations

from pathlib import Path

import pytest


def _infer_artifacts_dir(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    funcargs = request.node.funcargs

    tmp_path = funcargs.get("tmp_path")
    if isinstance(tmp_path, Path):
        return tmp_path

    case_path = funcargs.get("case_path")
    if isinstance(case_path, Path):
        if "group_cases_artifacts_root" in funcargs:
            block = "block1" if "block1" in request.node.name else "block2"
            return Path(funcargs["group_cases_artifacts_root"]) / block / case_path.stem
        if "program_year_cases_artifacts_root" in funcargs:
            block = "block1" if "block1" in request.node.name else "block2"
            return Path(funcargs["program_year_cases_artifacts_root"]) / block / case_path.stem

    if "full_config_cases_artifacts_root" in funcargs:
        block = "block1" if "block1" in request.node.name else "block2"
        return Path(funcargs["full_config_cases_artifacts_root"]) / block / "all_bachelors_masters_phd"

    node_name = request.node.nodeid.replace("/", "__").replace("::", "__")
    return tmp_path_factory.mktemp("test_output") / node_name


@pytest.fixture(autouse=True)
def save_test_output_artifact(
    request: pytest.FixtureRequest,
    capfd: pytest.CaptureFixture[str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    yield
    captured = capfd.readouterr()
    artifacts_dir = _infer_artifacts_dir(request, tmp_path_factory)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifacts_dir / "test_output.txt"
    output_path.write_text(
        f"=== stdout ===\n{captured.out}\n=== stderr ===\n{captured.err}",
        encoding="utf-8",
    )
