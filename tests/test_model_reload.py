import subprocess
import sys
from pathlib import Path


def test_models_can_reload_and_reopen_existing_database(tmp_path):
    script = """
import sys
import warnings
from pathlib import Path
from sqlalchemy.exc import SAWarning
from sqlmodel import Session, select

sys.path.insert(0, sys.argv[1])
warnings.simplefilter('error', SAWarning)
from xhs_seller.db import make_engine
from xhs_seller.models import ProductCandidate
from xhs_seller.seed_demo import seed

engine = make_engine(Path(sys.argv[2]))
seed(engine)
engine.dispose()

# Streamlit evicts watched project modules while SQLModel remains loaded.
for name in list(sys.modules):
    if name == 'xhs_seller' or name.startswith('xhs_seller.'):
        del sys.modules[name]

from xhs_seller.db import init_db, make_engine
from xhs_seller.models import ProductCandidate, Sku
engine = make_engine(Path(sys.argv[2]))
init_db(engine)
with Session(engine) as session:
    assert len(session.exec(select(ProductCandidate)).all()) == 3
    assert len(session.exec(select(Sku)).all()) == 3
    assert session.get(Sku, 'S-01').quote_id == 'Q-01A'
engine.dispose()
"""
    src = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", script, str(src), str(tmp_path / "reload.db")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
