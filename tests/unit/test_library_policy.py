import hashlib
from pathlib import Path


def test_machine_observability_library_matches_canonical_source():
    library = Path(__file__).resolve().parents[2] / "lib/charms/dwellir_observability/v0/machine_observability.py"

    assert hashlib.sha256(library.read_bytes()).hexdigest() == (
        "f93196c38bbd8343b1d72173e860e1adf5ddf04ce728fae36d6e57fc3916e679"
    )


def test_pprof_scrape_library_matches_canonical_source():
    library = Path(__file__).resolve().parents[2] / "lib/charms/dwellir_observability/v0/pprof_scrape.py"

    assert hashlib.sha256(library.read_bytes()).hexdigest() == (
        "eb03e0c3fd8b19711d1df0c099117dcf77211e53b96181dbdaab3755bde07baf"
    )
