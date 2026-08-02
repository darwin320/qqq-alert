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

## Uso local

```
pip install -r requirements.txt
python monitor.py --dry-run
python analyze.py --symbol QQQ --years 5 --highlight 2026-07-29
```

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
     (`2.0`), `DRAWDOWN` (`8.0`), `PEAK_DAYS` (`60`).
5. Probar a mano en la pestania Actions con "Run workflow", marcando dry run.

El workflow corre cada 30 minutos, de lunes a viernes, 13:00-21:00 UTC, que
cubre la sesion de EEUU tanto en horario de verano como de invierno.

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
