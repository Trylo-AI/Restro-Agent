from app.domain.ai_agent.profile_note import validate


def test_valid_ops_are_normalised() -> None:
    ops = validate([
        {"op": "set", "kind": "diet", "value": "Vegan"},
        {"op": "add", "kind": "dislike", "value": " Mushrooms "},
        {"op": "add", "kind": "allergy", "value": "nuts"},
        {"op": "add", "kind": "allergy", "value": "kiwi"},
        {"op": "set", "kind": "spice", "value": "mild"},
        {"op": "add", "kind": "milk", "value": "oat"},          # single kind: add → set
        {"op": "set", "kind": "like", "value": "cold brew"},   # list kind: set → add
    ])
    assert ops == [
        {"op": "set", "kind": "diet", "value": "vegan"},
        {"op": "add", "kind": "dislike", "value": "mushrooms"},
        {"op": "add", "kind": "allergy", "value": "tree_nuts"},
        {"op": "add", "kind": "allergy", "value": "kiwi"},
        {"op": "set", "kind": "spice", "value": "1"},
        {"op": "set", "kind": "milk", "value": "oat"},
        {"op": "add", "kind": "like", "value": "cold brew"},
    ]


def test_bad_ops_are_dropped() -> None:
    assert validate([
        {"op": "add", "kind": "consent", "value": "opted out"},       # not a kind
        {"op": "add", "kind": "other", "value": "doesn't want offers"},  # consent is never a profile fact
        {"op": "add", "kind": "other", "value": "paid ₹500 last time"},
        {"op": "add", "kind": "other", "value": "x" * 41},
        {"op": "set", "kind": "diet", "value": "keto"},                # not a diet we know
        {"op": "set", "kind": "spice", "value": "lava"},
        {"op": "delete", "kind": "dislike", "value": "onion"},
        "nonsense", None,
    ]) == []
    assert validate("not a list") == []


def test_duplicates_collapse() -> None:
    ops = validate([{"op": "add", "kind": "dislike", "value": "onion"}, {"op": "add", "kind": "dislike", "value": "Onion"}])
    assert ops == [{"op": "add", "kind": "dislike", "value": "onion"}]
