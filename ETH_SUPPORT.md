# Soporte Ethereum — BotSolana v2.1

## Estado de implementación ✓

El bot ahora soporta **copy trading en Ethereum** con simulación realista.

### Componentes implementados:

1. **eth_simulator.py** ✓
   - Simula trades en Uniswap V3
   - Calcula gas fees dinámicos (30 gwei default = ~$8-10 por swap)
   - Slippage dinámico basado en pool size
   - Market impact no lineal (igual que Solana)
   - Fail rate inteligente (5% en ETH vs 8% Solana)
   - Persistencia en `data/eth_*.json`

2. **eth_executor.py** ✓
   - Ejecuta swaps (simulación completa)
   - Live trading: versión 0.1 (estructura lista, falta firma de TX)
   - Detecta si hay wallet configurada (ETH_WALLET_ADDRESS + ETH_WALLET_PRIVKEY)
   - Registra trades en `data/eth_copytrades.json`

3. **eth_watcher.py** ✓ (mejorado)
   - Monitorea wallets Ethereum via Etherscan API
   - Detecta swaps en Uniswap V2/V3
   - Integra eth_executor para replicar trades

4. **main.py** ✓ (mejorado)
   - Muestra panel "Simulador Ethereum" con stats
   - Balance, PnL, retorno %, posiciones abiertas

---

## Configuración

### .env actual (ya configurado)

```env
# APIs
ETH_RPC_HTTP=https://eth.llamarpc.com
ETHERSCAN_API_KEY=TXKIDJZQC7SIT2SC3449...
ALCHEMY_API_KEY=3MGhJwHviY68S0Cxt5wns...

# Gas price (30 gwei = realista, normal)
ETH_GAS_PRICE_GWEI=30.0

# Wallet Ethereum (dejar vacío para simulación pura)
ETH_WALLET_ADDRESS=
ETH_WALLET_PRIVKEY=

# Polling interval (max 3s sin rate limit)
ETH_POLL_INTERVAL=3

# Puerto webhooks (si quieres Alchemy speed)
WEBHOOK_PORT=8000
```

---

## Realismo de la simulación

### Gas fees
- **30 gwei** (normal) → ~$8.28 por swap
- **50 gwei** (rápido) → ~$13.80 por swap
- **100 gwei** (urgente) → ~$27.60 por swap

Cálculo real: `gas * gwei * 1e9 / 1e18 * precio_ETH`

### Slippage
- Base: 0.5% (SLIPPAGE_BPS=50)
- Dinámico: +0.5% por cada 1% del tamaño del pool
- Cap: 20%

### Market impact
- Raíz cuadrada como AMM: `sqrt(ratio) * 0.30`
- Cap: 35%

### Fail rate
- Base: 5% en ETH (vs 8% en Solana)
- Ethereum es más estable

---

## Test

Ejecutar la prueba:

```bash
python3 test_eth.py
```

Genera archivos:
- `data/eth_positions.json` — posiciones abiertas
- `data/eth_balance.json` — balance simulado + stats
- `data/eth_history.json` — historial de trades

---

## Modos de operación

### 1. Simulación pura (defecto)
- No configures ETH_WALLET_ADDRESS/PRIVKEY
- Todos los trades se simulan
- Perfecto para validar la estrategia

### 2. Live trading (pendiente)
- Configura ETH_WALLET_ADDRESS + ETH_WALLET_PRIVKEY
- El bot ejecutará swaps REALES en Uniswap
- ⚠️ Requiere implementar: firma de TX, broadcast via RPC

---

## Roadmap

✓ Simulador con realismo brutal (gas, slippage, impact)
✓ Detector de wallets Ethereum (Etherscan)
✓ Integración en main.py

◯ Live trading en Uniswap (firmar TX)
◯ Alchemy webhooks (detección <100ms)
◯ PDA derivada para copiar wallets completas

---

## Limitaciones actuales

1. **Live trading no completo**
   - Falta integración real con Uniswap (firmar TX)
   - El código existe (`eth_executor.execute_eth_swap`) pero necesita:
     - Decodificar TX del watcher para extraer token real
     - Crear TX de swap en Uniswap V3 router
     - Firmar con eth_account
     - Broadcast via ETH_RPC_HTTP

2. **Watcher usa polling**
   - Ethercan: máx 5 calls/sec (rate limited)
   - Alchemy webhooks sería más rápido (<100ms vs 3s)

3. **No hay oracle de precios en-chain**
   - Usa precio implícito del swap de la wallet objetivo
   - Fallback: precio placeholder $0.0001

---

## Próximos pasos

Si quieres activar live trading:

1. Exporta clave privada de MetaMask/Trust
2. Configura en .env:
   ```
   ETH_WALLET_ADDRESS=0x...
   ETH_WALLET_PRIVKEY=0x...
   ```
3. Implementa la firma de TX en `eth_executor.execute_eth_swap()`

Para ahora, **simulación funciona perfectamente** — sirve para validar que el sistema detecta y procesa wallets ETH correctamente.
