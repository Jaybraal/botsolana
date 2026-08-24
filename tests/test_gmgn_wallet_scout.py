from gmgn_wallet_scout import find_new_candidates


def test_find_new_candidates_excluye_conocidas():
    smart_money = [
        {"address": "A", "win_rate": 80.0},
        {"address": "B", "win_rate": 70.0},
    ]
    result = find_new_candidates(smart_money, known_addresses={"A"})
    assert result == [{"address": "B", "win_rate": 70.0}]


def test_find_new_candidates_sin_conocidas_devuelve_todas():
    smart_money = [{"address": "A", "win_rate": 80.0}]
    result = find_new_candidates(smart_money, known_addresses=set())
    assert result == smart_money


def test_find_new_candidates_lista_vacia():
    assert find_new_candidates([], known_addresses={"A"}) == []
