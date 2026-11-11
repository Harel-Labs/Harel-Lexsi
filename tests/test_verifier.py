import pathlib

BANNED = ("openai", "httpx", "huggingface_hub", "agenttune", "auditkit", "harel_lexsi.adapters", "litellm")


def test_verifier_is_model_free():
    root = pathlib.Path(__file__).parents[1] / "src/harel_lexsi/verifier"
    files = list(root.glob("*.py")) + list((root.parent / "spec").glob("*.py"))
    assert files
    for f in files:
        imports = [ln for ln in f.read_text().splitlines() if ln.startswith(("import ", "from "))]
        assert not [ln for ln in imports if any(b in ln.lower() for b in BANNED)], f
