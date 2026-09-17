"""The regression corpus, run as part of the ordinary test suite.

The hand-written tests in this directory pin rules somebody thought of. This one pins the app's reading of
real posts that actually came through the channels — including the ones that were misread on the live map and
had to be found by eye. `scripts/corpus.py` captures and reviews them; this file is what makes a regression
fail the build instead of appearing on somebody's phone during a raid.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import corpus  # noqa: E402

CASES = corpus.load()
VERIFIED = [c for c in CASES if c.get("state") == "verified"]
KNOWN_BAD = [c for c in CASES if c.get("state") == "known_bad"]


def _label(c):
    return f"{c['channel']}:{c['id'][:6]} {(c.get('note') or c['text'])[:48]}"


@pytest.mark.skipif(not VERIFIED, reason="corpus has no verified cases yet")
@pytest.mark.parametrize("case", VERIFIED, ids=_label)
def test_a_verified_reading_never_changes(case):
    """Somebody looked at this post and at what the app made of it, and said it was right."""
    actual = corpus.project(case["channel"], case["text"])
    d = corpus.diff(case["expect"], actual)
    assert not d, (
        f"\n@{case['channel']}: {case.get('note') or ''}\n"
        f"  {case['text'][:220]}\n  " + "\n  ".join(d)
        + "\n\nIf the NEW reading is the correct one, re-verify the case:\n"
        f"  python scripts/corpus.py review --id {case['id']}"
    )


@pytest.mark.skipif(not KNOWN_BAD, reason="no known-bad cases")
@pytest.mark.parametrize("case", KNOWN_BAD, ids=_label)
def test_a_known_bad_reading_is_still_broken_or_gets_promoted(case):
    """A bug nobody has fixed yet. When it stops reproducing, that is good news the suite should announce —
    a fix that nobody records becomes a bug that comes back."""
    actual = corpus.project(case["channel"], case["text"])
    d = corpus.diff(case["expect"], actual)
    assert not d, (
        f"\nThis known-bad case no longer reproduces — it looks FIXED:\n"
        f"  @{case['channel']}: {case.get('note') or ''}\n  {case['text'][:220]}\n  " + "\n  ".join(d)
        + f"\n\nCheck the new reading and promote it:\n  python scripts/corpus.py review --id {case['id']}"
    )


def test_the_corpus_file_is_well_formed():
    """A corpus nobody can read is a corpus nobody maintains."""
    path = corpus.CORPUS
    if not os.path.exists(path):
        pytest.skip("no corpus yet")
    seen = set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)            # one JSON object per line, so git diffs stay readable
            for k in ("id", "channel", "text", "state", "expect"):
                assert k in c, f"line {n}: missing {k}"
            assert c["state"] in ("pending", "verified", "known_bad"), f"line {n}: {c['state']}"
            assert c["id"] == corpus.case_id(c["channel"], c["text"]), f"line {n}: id does not match its text"
            assert c["id"] not in seen, f"line {n}: duplicate {c['id']}"
            seen.add(c["id"])


# -- the corpus has to reach the running app -------------------------------------------------------

def test_the_corpus_lives_where_the_deployed_server_looks_for_it():
    """It sat under tests/, which .dockerignore excludes, so the review panel on a deployed machine said
    "nothing left to review" while 259 cases waited in the repo. The file is app data now, not a fixture."""
    assert corpus.CORPUS.replace("\\", "/").endswith("data/corpus.jsonl")
    assert os.path.exists(corpus.CORPUS)


def test_the_image_actually_copies_it():
    """A guard against the same failure coming back through the Dockerfile rather than the code."""
    docker = os.path.join(ROOT, "Dockerfile")
    ignore = os.path.join(ROOT, ".dockerignore")
    body = open(docker, encoding="utf-8").read()
    assert "COPY data" in body, "the corpus directory is not copied into the image"
    if os.path.exists(ignore):
        for line in open(ignore, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                assert not line.rstrip("/*").endswith("data"), f".dockerignore excludes the corpus: {line}"


def test_the_server_resolves_the_same_path():
    """server.py computes the path itself; if the two ever disagree the panel goes quiet with no error."""
    import server
    served = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(server.__file__))),
                          "data", "corpus.jsonl")
    assert os.path.normpath(served) == os.path.normpath(corpus.CORPUS)
