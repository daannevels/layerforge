"""Section 6 / 8: variations, determinism, selection, recipe re-render."""

import json

import numpy as np
import pytest

from layerforge.engine.presets import group_keepmap
from layerforge.engine.render import render
from layerforge.engine.select import generate, min_pairwise
from layerforge.engine.types import Recipe, Settings
from tests.fixtures import make_kick


def kicks(n=3, dur=0.4, stereo=False):
    out = []
    for k in range(n):
        x = np.concatenate([np.zeros(100 * k), make_kick(150 + 25 * k, 45 + 5 * k, 0.02 + 0.01 * k,
                                                         0.15 + 0.05 * k, 0.3 + 0.1 * k, k, seed=k, dur=dur)])
        out.append(np.stack([x, 0.9 * x]) if stereo else x[None])
    return out


def run(n, amount, seed, inputs=None, choices=(0, None, 1)):
    ins = inputs or kicks()
    km = group_keepmap("kick", len(ins), list(choices))
    return generate(ins, [f"k{i}" for i in range(len(ins))], km, Settings(), n, amount, seed)


def test_same_seed_same_recipes_and_audio():
    a, b = run(4, 50, 7), run(4, 50, 7)
    assert [v.recipe.to_dict() for v in a.variations] == [v.recipe.to_dict() for v in b.variations]
    assert all(np.array_equal(x.audio, y.audio) for x, y in zip(a.variations, b.variations))
    c = run(4, 50, 8)
    assert [v.recipe.to_dict() for v in a.variations] != [v.recipe.to_dict() for v in c.variations]


def test_first_variation_is_the_base():
    res = run(3, 50, 1)
    assert res.variations[0].recipe.candidate == 0
    assert res.variations[0].recipe.variation_amount == 0.0


def test_amount_zero_gives_only_the_base():
    res = run(8, 0, 3)
    assert len(res.variations) == 1 and res.variations[0].recipe.candidate == 0
    assert res.notes


def test_selection_spreads_more_than_random():
    res = run(16, 60, 11)
    assert len(res.variations) == 16
    z, rows = res.debug["z"], res.debug["picked_rows"]
    assert min_pairwise(z[rows]) > min_pairwise(z[:16])


@pytest.mark.parametrize("n", range(1, 17))
def test_count_is_honoured(n):
    ins = kicks(2, dur=0.25)
    res = run(n, 50, 100 + n, ins, choices=(0, None, None))
    assert len(res.variations) == n
    assert len({v.recipe.candidate for v in res.variations}) == n


def test_recipe_json_round_trip_renders_bit_identical():
    ins = kicks(3, stereo=True)
    res = run(3, 50, 5, ins)
    for v in res.variations:
        r = Recipe.from_dict(json.loads(json.dumps(v.recipe.to_dict())))
        assert np.array_equal(render(r, ins).audio, v.audio)


def test_low_end_keeps_one_owner_in_every_variation():
    res = run(8, 100, 21)
    for v in res.variations:
        for b in (0, 1):   # Sub and Body of a kick
            for owners in v.recipe.cells[b]:
                assert len(owners) <= 1
        assert v.recipe.notes["low_end_r"] >= 0.5


def test_group_keepmap():
    km = group_keepmap("kick", 3, [2, None, 0])
    states = [[km.cells[i][b][0].state for b in range(5)] for i in range(3)]
    assert states[2][:2] == ["keep", "keep"] and states[0][3:] == ["keep", "keep"]
    assert states[1] == ["open"] * 5 and states[0][2] == "open"
    with pytest.raises(ValueError):
        group_keepmap("kick", 2, [5, None, None])
