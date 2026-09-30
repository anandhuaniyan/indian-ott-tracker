import gzip

from app.services.imdb_rating_enrichment import IMDbDatasetIndex, IMDB_ID, LANGUAGES, OPERATION, RECENT_YEARS


def test_imdb_dataset_index_loads_only_valid_ratings(tmp_path):
    path = tmp_path / "title.ratings.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("tconst\taverageRating\tnumVotes\n")
        handle.write("tt1234567\t7.4\t12543\n")
        handle.write("bad-id\t9.0\t5\n")
        handle.write("tt7654321\t11.0\t2\n")
    index = IMDbDatasetIndex(path)
    index.load()
    assert index.get("tt1234567").rating == 7.4
    assert index.get("tt1234567").votes == 12543
    assert index.get("bad-id") is None
    assert index.get("tt7654321") is None


def test_imdb_operation_scope_and_id_validation():
    assert OPERATION == "operations.imdb_rating_enrichment"
    assert RECENT_YEARS == (2026, 2025, 2024, 2023, 2022, 2021, 2020)
    assert LANGUAGES == ("ml", "ta", "te", "hi", "kn", "en", "ko", "ja")
    assert IMDB_ID.fullmatch("tt1234567")
    assert not IMDB_ID.fullmatch("tt123")
