"""Detecta blockchain por formato de wallet."""

def detect_blockchain(wallet: str) -> str:
    """Retorna 'solana', 'ethereum' o 'unknown'."""
    wallet = wallet.strip()

    # Ethereum: 0x seguido de 40 hex chars
    if wallet.lower().startswith("0x") and len(wallet) == 42:
        return "ethereum"

    # Solana: base58, típicamente 44 chars
    if len(wallet) >= 43 and len(wallet) <= 44:
        try:
            import base58
            base58.b58decode(wallet)
            return "solana"
        except:
            pass

    return "unknown"


def format_wallet(wallet: str, blockchain: str = None) -> str:
    """Formatea wallet corto para logs."""
    if blockchain is None:
        blockchain = detect_blockchain(wallet)

    if blockchain == "ethereum":
        return f"{wallet[:8]}...{wallet[-4:]}"  # 0xabc...def
    else:  # solana o unknown
        return f"{wallet[:8]}...{wallet[-4:]}"
