# BotSolana

Bot de *copy trading* en Solana: sigue en tiempo real las operaciones de un
conjunto de carteras seleccionadas y replica las que superan sus filtros.

> El problema no es detectar que una cartera compró algo. Es decidir, en
> segundos y con información incompleta, si esa compra merece replicarse —
> y asumir que la mayoría no.

## Cómo funciona

1. **Escucha** — conexión WebSocket a la red para recibir las transacciones de
   las carteras vigiladas sin *polling*.
2. **Puntuación** — cada cartera acumula un historial que determina cuánto
   crédito merece (`utils/wallet_scoring.py`).
3. **Filtrado** — una operación detectada no se replica por defecto: tiene que
   pasar los criterios de riesgo configurados.
4. **Ejecución** — enrutado por Jupiter y Raydium.

## Modo sombra

El bot puede operar en *shadow mode*: registra cada decisión que habría tomado,
con su resultado real, **sin mover fondos**. Sirve para medir la estrategia
contra el mercado antes de arriesgar capital — que es la única forma honesta de
saber si funciona.

## Prospección de carteras

`copytrade/root_wallet_miner.py` y `gmgn_wallet_scout.py` buscan y evalúan
carteras candidatas por su historial, en lugar de partir de una lista fija.

## Stack

Python · WebSocket · Jupiter · Raydium · SQLite

## Configuración

Las credenciales se leen del entorno; `.env.example` documenta las variables
necesarias. **Ninguna clave se versiona.**

## Aviso

Proyecto de investigación sobre datos de mercado y ejecución automatizada. No
es asesoramiento financiero. Operar con criptomonedas conlleva riesgo de
pérdida total.

## Licencia

Propietario — todos los derechos reservados. Visible para evaluación técnica;
no se autoriza su uso, copia ni distribución. Ver [LICENSE](LICENSE).
