import hashlib
from pathlib import Path


def test_machine_observability_library_matches_canonical_source():
    library = Path(__file__).resolve().parents[2] / "lib/charms/dwellir_observability/v0/machine_observability.py"

    assert hashlib.sha256(library.read_bytes()).hexdigest() == (
        "1ee5c616d6b863fc1b93c5515fdc4e1ca9c1d41251fdd6b28814d2706e668f7f"
    )


def test_pprof_scrape_library_matches_canonical_source():
    library = Path(__file__).resolve().parents[2] / "lib/charms/dwellir_observability/v0/pprof_scrape.py"

    assert hashlib.sha256(library.read_bytes()).hexdigest() == (
        "eb03e0c3fd8b19711d1df0c099117dcf77211e53b96181dbdaab3755bde07baf"
    )
