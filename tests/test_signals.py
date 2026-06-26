def test_register_and_check():
    from copytrade.signals import register_elite_buy, is_elite_signal, clear_mint, _elite_mints
    _elite_mints.clear()
    register_elite_buy("mint_abc")
    assert is_elite_signal("mint_abc")
    assert not is_elite_signal("mint_xyz")


def test_clear_removes_mint():
    from copytrade.signals import register_elite_buy, is_elite_signal, clear_mint, _elite_mints
    _elite_mints.clear()
    register_elite_buy("mint_abc")
    clear_mint("mint_abc")
    assert not is_elite_signal("mint_abc")


def test_clear_nonexistent_no_crash():
    from copytrade.signals import clear_mint
    clear_mint("never_registered")  # no debe lanzar
