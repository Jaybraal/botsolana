"""Cliente Alchemy para crear y gestionar webhooks."""

import os
import httpx
from utils.logger import get_logger

log = get_logger("alchemy_client")

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ALCHEMY_BASE_URL = "https://dashboard.alchemy.com/api/webhooks"


async def create_alchemy_webhook(
    wallet_address: str,
    webhook_url: str,
    network: str = "eth-mainnet",
) -> dict | None:
    """Crea un webhook en Alchemy para monitorear una wallet.

    Args:
        wallet_address: Dirección Ethereum (0x...)
        webhook_url: URL pública donde recibir notificaciones (ej: https://tudominio.com/api/webhook/alchemy)
        network: 'eth-mainnet', 'eth-sepolia', etc.

    Returns:
        dict con webhook_id si éxito, None si error
    """
    if not ALCHEMY_API_KEY:
        log.warning("ALCHEMY_API_KEY no configurado")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{ALCHEMY_BASE_URL}",
                headers={
                    "X-Alchemy-Token": ALCHEMY_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "webhook_type": "address_activity",
                    "webhook_url": webhook_url,
                    "addresses": [wallet_address],
                    "network": network,
                },
            )

            if response.status_code == 201:
                data = response.json()
                webhook_id = data.get("id")
                log.info(f"Webhook creado para {wallet_address[:8]}... | ID: {webhook_id}")
                return {"webhook_id": webhook_id, "wallet": wallet_address}
            else:
                log.error(f"Error creando webhook: {response.status_code} {response.text}")
                return None

    except Exception as e:
        log.error(f"Error creando webhook Alchemy: {e}")
        return None


async def delete_alchemy_webhook(webhook_id: str) -> bool:
    """Elimina un webhook."""
    if not ALCHEMY_API_KEY:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                f"{ALCHEMY_BASE_URL}/{webhook_id}",
                headers={"X-Alchemy-Token": ALCHEMY_API_KEY},
            )
            return response.status_code == 204

    except Exception as e:
        log.error(f"Error eliminando webhook: {e}")
        return False


async def list_alchemy_webhooks() -> list[dict]:
    """Lista todos los webhooks configurados."""
    if not ALCHEMY_API_KEY:
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{ALCHEMY_BASE_URL}",
                headers={"X-Alchemy-Token": ALCHEMY_API_KEY},
            )

            if response.status_code == 200:
                return response.json().get("data", [])
            else:
                log.error(f"Error listando webhooks: {response.status_code}")
                return []

    except Exception as e:
        log.error(f"Error listando webhooks: {e}")
        return []
