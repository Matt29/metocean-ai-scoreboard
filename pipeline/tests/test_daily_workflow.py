from pathlib import Path


def test_scoreboard_commit_excludes_public_buoy_tree_recursively():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "daily.yml"
    contents = workflow.read_text()

    recursive_exclusion = "':(exclude,glob)data/buoys/**'"
    assert contents.count(recursive_exclusion) == 2
    assert "':(exclude)data/buoys/'" not in contents
