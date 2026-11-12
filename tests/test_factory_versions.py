"""G1: factory versions are archived, never overwritten, and tamper-evident. No database needed."""
import json
import os
import stat

import pytest

from harel_lexsi.factory import (
    NoCurrentVersion,
    UnknownVersion,
    VersionCorrupt,
    VersionStore,
)


@pytest.fixture
def store(tmp_path):
    return VersionStore(tmp_path / "versions")


def test_create_records_prompt_memory_and_why(store):
    v = store.create("You write shop handlers.", "remember: use transactions", note="first factory")
    assert (v.id, v.parent) == ("v0001", None)
    assert (v.prompt, v.memory, v.note) == ("You write shop handlers.", "remember: use transactions", "first factory")
    assert store.get("v0001") == v


def test_ids_are_sequential_and_listed_in_order(store):
    ids = [store.create(f"prompt {i}").id for i in range(3)]
    assert ids == ["v0001", "v0002", "v0003"]
    assert [v.id for v in store.list()] == ids


def test_a_version_is_never_overwritten(store):
    first = store.create("original", "m")
    path = store.root / "v0001.json"
    # the file is read-only
    assert not os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    # creating again never reuses an id, even if the counter would collide
    again = store.create("different")
    assert again.id == "v0002"
    assert store.get("v0001") == first and store.get("v0001").prompt == "original"


def test_create_does_not_clobber_an_existing_file_even_when_ids_are_out_of_step(store, tmp_path):
    store.create("a")
    (store.root / "v0002.json").write_text("not a real record")  # something already sits on the next id
    assert store.create("b").id == "v0003"
    assert (store.root / "v0002.json").read_text() == "not a real record"


def test_an_edit_behind_our_back_is_detected(store):
    store.create("original")
    path = store.root / "v0001.json"
    os.chmod(path, 0o644)
    raw = json.loads(path.read_text())
    raw["prompt"] = "quietly changed"
    path.write_text(json.dumps(raw))
    with pytest.raises(VersionCorrupt):
        store.get("v0001")
    with pytest.raises(VersionCorrupt):
        store.pin("v0001")


def test_unknown_and_malformed_ids_are_rejected(store):
    store.create("a")
    for bad in ("v0099", "../v0001", "v1", "", "v0001.json", "CURRENT"):
        with pytest.raises(UnknownVersion):
            store.get(bad)


def test_parent_defaults_to_current_and_must_exist(store):
    a = store.create("a")
    store.promote(a.id)
    b = store.create("b", note="governor change")
    assert b.parent == "v0001"
    assert store.create("fresh start", parent=None).parent is None
    with pytest.raises(UnknownVersion):
        store.create("c", parent="v0042")


def test_current_pointer(store):
    with pytest.raises(NoCurrentVersion):
        store.current()
    with pytest.raises(NoCurrentVersion):
        store.pin()
    a, b = store.create("a"), store.create("b")
    assert store.promote(a.id) == a and store.current() == a
    store.promote(b.id)
    assert store.current() == b
    assert store.get(a.id) == a  # promoting b changed the pointer, not version a
    with pytest.raises(UnknownVersion):
        store.promote("v0099")
    assert store.current() == b


def test_pin_returns_the_named_or_current_version(store):
    a, b = store.create("a"), store.create("b")
    store.promote(a.id)
    assert store.pin() == a
    assert store.pin("v0002") == b
    with pytest.raises(UnknownVersion):
        store.pin("v0009")


def test_versions_survive_a_new_store_object(store):
    v = store.create("kept", "memory")
    store.promote(v.id)
    reopened = VersionStore(store.root)
    assert reopened.current() == v and reopened.list() == [v]
