"""Isolated persistence coverage for data shown by the review tab."""

from review_queue import ReviewItem, ReviewStore


def test_review_queue_uses_only_synthetic_temporary_data(tmp_path):
    queue_path = tmp_path / "manual_review.json"
    item = ReviewItem(
        file_path="manual_check/anonymous.pdf",
        raw_name="Sample Student",
        raw_subject="Matematik",
        reason="Unbekanntes Fach",
        suggestions=["Mathematik"],
        preview_text="Anonymous preview",
    )
    store = ReviewStore(queue_path)
    store.add(item)

    assert store.save()

    loaded = ReviewStore(queue_path)
    loaded.load()

    assert loaded.count() == 1
    assert loaded.get_all()[0].to_dict() == item.to_dict()
    assert loaded.store_path.parent == tmp_path
