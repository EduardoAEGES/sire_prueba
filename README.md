# sire_prueba — descarga de registros SIRE (SUNAT)

Cliente en Python para la API SIRE de SUNAT que descarga la **propuesta** del
Registro de Ventas (RVIE, libro `140000`) y del Registro de Compras (RCE, libro
`080000`) de un periodo específico.

## 1. Obtener credenciales de API

1. Ingresa a **SUNAT Operaciones en Línea** con tu clave SOL (usuario principal o
   secundario con permisos de SIRE).
2. Ve a *Empresas → Credenciales de API SUNAT* y registra una aplicación.
3. Copia el **client_id** y el **client_secret** que se generan.

## 2. Configuración

```bash
pip install -r requirements.txt
cp .env.example .env   # y completa tus datos
```

`.env` está en `.gitignore`: no subas tus credenciales al repositorio.

## 3. Uso

```bash
# Ventas y compras del periodo octubre 2024 (TXT)
python descargar_sire.py 202410 -v

# Solo compras, en CSV (también: --formato excel)
python descargar_sire.py 202410 --libro compras --formato csv

# Ver los periodos disponibles
python descargar_sire.py --periodos ventas
```

Los archivos quedan en `descargas/<libro>/<periodo>/`.

Desde código:

```python
from sire import Credenciales, SireClient

cliente = SireClient(Credenciales(ruc, usuario_sol, clave_sol, client_id, client_secret))
archivos = cliente.descargar_propuesta("ventas", "202410", destino="descargas", formato="txt")
```

## Cómo funciona

| Paso | Servicio |
|------|----------|
| Token | `POST https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/` (`grant_type=password`, `username = RUC + usuario SOL`) |
| Propuesta ventas | `GET …/libros/rvie/propuesta/web/propuesta/{AAAAMM}/exportapropuesta?codTipoArchivo=0\|1` |
| Propuesta compras | `GET …/libros/rce/propuesta/web/propuesta/{AAAAMM}/exportacioncomprobantepropuesta?codTipoArchivo=0\|1&codOrigenEnvio=2` |
| Estado del ticket | `GET …/libros/rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets` (en ventas además `codLibro=140000&codOrigenEnvio=2`) |
| Descarga | `GET …/libros/rvierce/gestionprocesosmasivos/web/masivo/archivoreporte` |

Base: `https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv`.

Verificado contra el *Manual de servicios Web API SIRE Compras v22* (5.1, 5.31–5.34)
y el *Manual del servicio API del RVIE v30* (5.1, 5.2, 5.16–5.18). Detalles a tener en cuenta:

- `codTipoArchivo` cambia según el libro: compras `0 txt, 1 csv, 2 excel`; ventas
  `0 txt, 1 excel, 2 csv`. El cliente hace la conversión.
- Estados del ticket: `01` cargado, `02` validando, `03` procesado con errores,
  `04` procesado sin errores, `05` en proceso, `06` terminado.
- Si el ticket devuelve `codTipoAchivoReporte` en null, se envía el texto `null` al descargar.
- El error `1070` significa que el periodo no tiene comprobantes en la propuesta.

Las rutas están como constantes al inicio de `sire/client.py` por si SUNAT las
cambia en una nueva versión del manual.

## Pruebas

```bash
python -m pytest -q
```

Las pruebas usan una sesión HTTP simulada; no se conectan a SUNAT.
