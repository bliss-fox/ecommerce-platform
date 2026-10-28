import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def isolated_git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git is required to verify ignore rules")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    source = Path(__file__).resolve().parents[1] / ".gitignore"
    shutil.copyfile(source, tmp_path / ".gitignore")
    return tmp_path


def ignored(repo, name):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", name],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr.decode(errors="replace")
    return result.returncode == 0


@pytest.mark.parametrize(
    "name",
    [
        ".env.local",
        ".ENV.production",
        "credentials.env",
        "orders.csv",
        "imports/ORDERS.CSV",
        "examples/p4/customer-orders.csv",
        "examples/import_cases/private.csv",
        "data_templates/p4/real-data.csv",
        "logs/app.log",
        "logs/app.LOG.1",
        "backups/local.sqlite3",
        "backups/local.sqlite3-wal",
        "backups/local.sqlite3.bak",
        "backups/local.DB",
        "backups/local.db.bak",
        "exports/orders.xlsx",
        "images/product.JPG",
        "materials/notes.txt",
        "user_data/notes.txt",
    ],
)
def test_private_data_is_ignored_by_default(isolated_git_repo, name):
    assert ignored(isolated_git_repo, name)


@pytest.mark.parametrize(
    "name", [".env.example", "examples/p4/orders.csv", "data_templates/p4/orders.csv", "app.py"]
)
def test_synthetic_fixtures_and_source_remain_trackable(isolated_git_repo, name):
    assert not ignored(isolated_git_repo, name)


def test_all_shipped_fixture_csvs_remain_trackable(isolated_git_repo):
    names = {
        "p1": ["product_candidates.csv", "supplier_quotes.csv", "skus.csv"],
        "p2": ["research_notes.csv", "product_research_links.csv", "candidate_checks.csv"],
        "p4": [
            "orders.csv",
            "expenses.csv",
            "expense_allocations.csv",
            "cost_adjustments.csv",
            "settlements.csv",
        ],
    }
    fixtures = [
        f"{root}/{phase}/{name}"
        for root in ("examples", "data_templates")
        for phase, files in names.items()
        for name in files
    ]
    fixtures += [
        f"examples/import_cases/{name}"
        for name in (
            "expense_actual_revision2.csv",
            "product_revision2.csv",
            "product_same_revision_conflict.csv",
            "product_stale_revision1.csv",
            "products_atomic_error.csv",
            "products_reordered.csv",
            "settlement_after_refund_revision2.csv",
        )
    ]
    assert all(not ignored(isolated_git_repo, name) for name in fixtures)
