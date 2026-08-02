# qqq-alert

Aviso al celular cuando QQQ (o el ticker que sea) cae. Corre gratis en GitHub
Actions, sin servidor y sin suscripcion.

Nota: los textos de este repo van en ASCII plano, sin tildes, a proposito.

## Que vigila

Dos reglas, porque miden cosas distintas:

1. **Caida del dia** - el precio esta N% por debajo del **cierre de la sesion
   anterior**. Dispara maximo una vez por sesion.
2. **Distancia al maximo** - el precio esta N% por debajo del cierre mas alto
   de las ultimas K sesiones. Es la senal de "que tan lejos del techo estamos",
   que suele importar mas que el movimiento de un solo dia. Va con enganche: se
   dispara al entrar en la caida, no cada 30 minutos mientras dure, y se vuelve
   a armar solo cuando el precio recupera pasando una banda de histeresis.

## Los gaps: si, van incluidos, por dos vias

**Primera: el gap esta dentro del numero.** La regla 1 mide contra el cierre
de la ultima sesion regular completa, no contra la apertura del dia. Entonces
todo lo que se movio el precio mientras el mercado estuvo cerrado ya esta
adentro. El mensaje ademas lo desglosa en "gap + intradia" para que se vea de
donde vino el movimiento.

**Segunda: se ve el gap mientras se forma.** Por defecto el precio actual sale
de datos extendidos (pre-market desde las 04:00 ET y after-hours hasta las
20:00 ET), y el cron arranca a las 11:00 UTC. O sea que un desplome que
empieza a las 07:00 ET avisa ahi, no a las 09:30 cuando abre la sesion
regular. Se apaga con `--no-extended` o con la variable `EXTENDED=0`.

La diferencia no es cosmetica. Con datos del 31 de julio de 2026:

| Fuente del precio | QQQ | Distancia al maximo |
|---|---|---|
| Cierre regular | 687.99 | -7.80% |
| Ultimo negociado (after-hours) | 684.47 | **-8.27%** |

Con umbral de -8% el segundo dispara y el primero no.

## Por que se mide contra el cierre previo y no contra la apertura

Porque medir desde la apertura del dia se pierde el hueco nocturno, y eso no es
un detalle teorico. Replicando 5 anios de QQQ (1254 sesiones, ago-2021 a
jul-2026) con un umbral de -2%:

| | dias |
|---|---|
| detectados por las dos medidas | 44 |
| **solo por "contra cierre previo"** | **44** |
| solo por "contra apertura" | 18 |

Medir desde la apertura habria perdido **la mitad** de los eventos. En las diez
peores sesiones del periodo el hueco de apertura aporto entre el 2% y el 77%
del movimiento total del dia.

Detalle: el 29 de julio de 2026 el hueco fue de +0.00% y la caida de -2.04% fue
enteramente intradia. O sea que ese dia puntual las dos medidas coincidian; el
problema del hueco es real pero no aplicaba a ese evento.

Corre `python analyze.py` para regenerar estas cifras.

## Modo minimo de sesion (USE_LOW, activado por defecto)

La regla 1 se juzga sobre el **minimo de la sesion** contra el cierre previo, no
sobre el precio del instante en que corre el chequeo. Sin esto, una caida que
baja y se recupera entre dos chequeos de 30 minutos es invisible. Con datos de
2026 la diferencia es grande:

| QQQ en 2026 | Dias |
|---|---|
| Tocaron -2% en algun momento | 24 |
| Cerraron en -2% o peor | 6 |

Costo: mas avisos, y algunos llegan cuando el precio ya reboto. El mensaje lo
dice explicitamente ("ahora va en -1.40%") para que no confundas el minimo con
el precio actual. Se apaga con `USE_LOW=0` o `--no-use-low`.

**Ojo con la frecuencia:** medido sobre el minimo, QQQ toca -2% unas 34 veces
al anio (promedio de 5 anios), no 17.7. Si eso resulta ruidoso, el umbral que
deja ~10 al anio en modo minimo es -3.0%.

### El tick fantasma

Las barras de horario extendido de Yahoo traen `Volume` en 0 y su `Low` no es
confiable. Caso real: 2026-07-31 17:32 imprimio `Low` 667.36 con su propio
`Close` en 684.98 y los dos vecinos en 684.8, un -2.4% inexistente que habria
disparado una alerta falsa. El `Close` de esas barras si sigue al precio, asi
que el minimo se calcula confiando en `Low` solo donde hubo volumen, y en
`Close` en el resto.

## Elegir el umbral

Frecuencia real medida sobre los mismos 5 anios:

| Umbral diario | Alertas por anio |
|---|---|
| -1.5% | 30.9 |
| **-2.0%** | **17.7** |
| -2.5% | 10.0 |
| -3.0% | 6.0 |
| -4.0% | 2.4 |

| Distancia al maximo (60 sesiones) | Alertas por anio |
|---|---|
| -5% | 8.4 |
| **-8%** | **4.9** |
| -10% | 3.8 |
| -15% | 2.3 |

Los valores por defecto son -2.0% y -8%, o sea unas 23 notificaciones al anio
entre las dos reglas. Un umbral de -3% habria dejado pasar el 29 de julio de
2026, que cayo -2.04%.

**El umbral no se puede reciclar entre tickers.** El mismo -2% diario es un
evento muy distinto segun el papel:

| Umbral diario | QQQ | SPY |
|---|---|---|
| -1.5% | 30.9/anio | 17.9/anio |
| -2.0% | 17.7/anio | 8.0/anio |
| -3.0% | 6.0/anio | 2.2/anio |

Por eso cada simbolo lleva los suyos: `SYMBOLS=QQQ:2.0:8.0,SPY:1.5:5.0`. El
formato es `TICKER[:caida_diaria[:distancia_al_maximo]]`, y lo que se omite
toma el default. Sirve cualquier ticker de Yahoo Finance, no solo ETFs:
acciones, indices, divisas, cripto.

## Uso local

```
pip install -r requirements.txt
python monitor.py --dry-run
python analyze.py --symbol QQQ --years 5 --highlight 2026-07-29
python candidates.py 5
```

`candidates.py` mide que tickers vale la pena sumar al monitor: el umbral que
da ~1 alerta al mes en cada uno, y sobre todo cuantas de esas alertas caen en
un dia en que QQQ ya toco -2%. Un papel con 90% de solapamiento no te esta
diciendo nada nuevo. No ordena nada como inversion, solo mide valor de senal.

En Windows, si las llamadas HTTPS fallan con `unable to get local issuer
certificate`, es el antivirus interceptando TLS. Se arregla una sola vez:

```
powershell -File tools\make_ca_bundle.ps1
```

Los scripts detectan el bundle generado y lo usan solos. El archivo esta en
.gitignore: CI corre en Linux, no tiene interceptacion y no debe confiar en esa
raiz.

## Desplegar en GitHub Actions

1. Crear el repo y subir esto.
2. Elegir un topic de [ntfy.sh](https://ntfy.sh). El nombre del topic **es** la
   direccion y cualquiera que lo adivine puede leerlo o escribirle, asi que
   usar algo largo y aleatorio, no "qqq-alerta".
3. Instalar la app ntfy en el celular y suscribirse a ese topic.
4. En el repo: Settings > Secrets and variables > Actions
   - Secret `NTFY_TOPIC` con el nombre del topic.
   - Opcional, como Variables: `SYMBOLS` (default `QQQ`), `DAILY_DROP`
     (`2.0`), `DRAWDOWN` (`8.0`), `PEAK_DAYS` (`60`), `EXTENDED` (`1`).
5. Probar a mano en la pestania Actions con "Run workflow", marcando dry run.

El workflow corre cada 30 minutos, de lunes a viernes, 11:00-23:00 UTC: 26
corridas por dia habil, unas 570 al mes. En repo publico los minutos de
Actions son ilimitados. En repo privado salen de la cuota gratis de 2000
minutos al mes, y esto consume del orden de 600, asi que cabe pero deja menos
margen para otras cosas.

Si el paso que guarda el estado falla con un error de permisos, revisar
Settings > Actions > General > Workflow permissions y dejarlo en "Read and
write permissions".

## Limitaciones que conviene saber

- **Los cron de GitHub Actions son "best effort".** Bajo carga se retrasan, a
  veces 15 minutos o mas. Para una alerta de 30 minutos es tolerable, pero no
  es un reloj.
- **El estado vive en `state.json`, commiteado por el propio workflow.** Es lo
  que evita que la misma alerta llegue cada media hora. Si se borra, la
  siguiente caida vuelve a notificar.
- GitHub desactiva los workflows programados tras 60 dias sin actividad en el
  repo. El script escribe la fecha de la ultima corrida en `state.json`, lo que
  produce un commit diario y mantiene el repo activo.
- **Los datos son de Yahoo Finance via yfinance**, que no es una API oficial ni
  tiene SLA. Para avisar de una caida sirve de sobra; para operar no.
- El precio durante la sesion viene de la barra diaria en curso, que Yahoo
  actualiza con retraso de algunos minutos.
