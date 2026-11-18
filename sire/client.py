"""Cliente para la API SIRE de SUNAT (Registro de Ventas - RVIE y Registro de Compras - RCE).

Flujo de descarga de la propuesta de un periodo:
    1. Obtener token OAuth2 (grant_type=password) en api-seguridad.sunat.gob.pe
    2. Solicitar la exportación de la propuesta -> SUNAT devuelve un número de ticket
    3. Consultar el estado del ticket hasta que esté "Terminado"
    4. Descargar el archivo de reporte (ZIP con TXT o CSV) y descomprimirlo

Rutas y parámetros verificados contra:
  - Manual de servicios Web API SIRE Compras v22 (5.1, 5.31, 5.32, 5.33, 5.34)
  - Manual del servicio API del Registro de Ventas e Información Electrónica v30
    (5.1, 5.2, 5.16, 5.17, 5.18)
Si SUNAT cambia alguna ruta en una versión nueva del manual, basta con ajustar las
constantes de este módulo.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

URL_SEGURIDAD = "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/"
URL_API = "https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros"
SCOPE = "https://api-sire.sunat.gob.pe"

LIBRO_VENTAS = "140000"  # RVIE
LIBRO_COMPRAS = "080000"  # RCE

RUTA_PERIODOS = "/rvierce/padron/web/omisos/{cod_libro}/periodos"
RUTA_PROPUESTA_VENTAS = "/rvie/propuesta/web/propuesta/{periodo}/exportapropuesta"
RUTA_PROPUESTA_COMPRAS = "/rce/propuesta/web/propuesta/{periodo}/exportacioncomprobantepropuesta"
RUTA_ESTADO_TICKET = "/rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets"
RUTA_DESCARGA = "/rvierce/gestionprocesosmasivos/web/masivo/archivoreporte"

# codTipoArchivo: los manuales usan códigos distintos según el libro
#   Compras (Anexo III): 0 txt, 1 csv, 2 excel
#   Ventas  (Anexo IV):  0 txt, 1 excel, 2 csv
TIPO_ARCHIVO = {
    "compras": {"txt": "0", "csv": "1", "excel": "2"},
    "ventas": {"txt": "0", "excel": "1", "csv": "2"},
}
CODIGO_ORIGEN_API = "2"  # codOrigenEnvio: 2 = Servicio Web API

# Códigos de estado de envío del ticket (Anexo III del manual de Ventas)
ESTADOS_OK = {"04", "06"}  # 04 Procesado sin errores, 06 Terminado
ESTADOS_ERROR = {"03"}  # 03 Procesado con errores


class SireError(Exception):
    """Error devuelto por la API SIRE o del flujo de descarga."""


@dataclass
class Credenciales:
    ruc: str
    usuario_sol: str
    clave_sol: str
    client_id: str
    client_secret: str


class SireClient:
    def __init__(self, cred: Credenciales, timeout: int = 60, session: requests.Session | None = None):
        self.cred = cred
        self.timeout = timeout
        self.http = session or requests.Session()
        self._token: str | None = None
        self._token_expira = 0.0

    # ------------------------------------------------------------------ auth
    def token(self) -> str:
        """Devuelve un token vigente; lo renueva si faltan menos de 60 s para que expire."""
        if self._token and time.time() < self._token_expira - 60:
            return self._token

        url = URL_SEGURIDAD.format(client_id=self.cred.client_id)
        data = {
            "grant_type": "password",
            "scope": SCOPE,
            "client_id": self.cred.client_id,
            "client_secret": self.cred.client_secret,
            # SUNAT espera el RUC concatenado con el usuario SOL
            "username": f"{self.cred.ruc}{self.cred.usuario_sol}",
            "password": self.cred.clave_sol,
        }
        r = self.http.post(url, data=data, timeout=self.timeout)
        if r.status_code != 200:
            raise SireError(f"No se pudo obtener el token ({r.status_code}): {r.text}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expira = time.time() + int(body.get("expires_in", 3600))
        log.info("Token obtenido (expira en %s s)", body.get("expires_in"))
        return self._token

    def _get(self, ruta: str, params: dict | None = None) -> requests.Response:
        r = self.http.get(
            URL_API + ruta,
            params=params,
            headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise SireError(f"GET {ruta} -> {r.status_code}: {_mensaje_error(r)}")
        return r

    # --------------------------------------------------------------- consultas
    def periodos(self, cod_libro: str) -> list[dict]:
        """Lista los ejercicios y periodos habilitados para el libro (140000 ventas, 080000 compras).

        Respuesta: [{"numEjercicio": "2024", "desEstado": ..., "lisPeriodos":
                     [{"perTributario": "202401", "codEstado": ..., "desEstado": ...}]}]
        """
        return self._get(RUTA_PERIODOS.format(cod_libro=cod_libro)).json()

    def solicitar_propuesta(self, libro: str, periodo: str, formato: str = "txt") -> str:
        """Pide a SUNAT que genere el archivo de la propuesta. Devuelve el número de ticket."""
        _validar_periodo(periodo)
        if libro not in TIPO_ARCHIVO:
            raise ValueError("libro debe ser 'ventas' o 'compras'")
        if formato not in TIPO_ARCHIVO[libro]:
            raise ValueError(f"formato debe ser uno de {sorted(TIPO_ARCHIVO[libro])}")
        cod = TIPO_ARCHIVO[libro][formato]
        if libro == "ventas":  # Ventas 5.18
            ruta = RUTA_PROPUESTA_VENTAS.format(periodo=periodo)
            params = {"codTipoArchivo": cod}
        else:  # Compras 5.34
            ruta = RUTA_PROPUESTA_COMPRAS.format(periodo=periodo)
            params = {"codTipoArchivo": cod, "codOrigenEnvio": CODIGO_ORIGEN_API}

        body = self._get(ruta, params).json()
        ticket = body.get("numTicket") if isinstance(body, dict) else None
        if not ticket:
            raise SireError(f"La respuesta no trae numTicket: {body}")
        log.info("Ticket %s generado para %s %s", ticket, libro, periodo)
        return ticket

    def estado_ticket(self, libro: str, periodo: str, ticket: str) -> dict:
        params = {"perIni": periodo, "perFin": periodo, "page": 1, "perPage": 20, "numTicket": ticket}
        if libro == "ventas":  # el manual de Ventas (5.16) los marca como obligatorios
            params.update(codLibro=LIBRO_VENTAS, codOrigenEnvio=CODIGO_ORIGEN_API)
        body = self._get(RUTA_ESTADO_TICKET, params).json()
        for reg in body.get("registros", []):
            if str(reg.get("numTicket")) == str(ticket):
                return reg
        raise SireError(f"Ticket {ticket} no encontrado en la respuesta: {body}")

    def esperar_ticket(
        self, libro: str, periodo: str, ticket: str, intervalo: int = 5, max_espera: int = 600
    ) -> dict:
        """Consulta el ticket hasta que termine. Devuelve el registro del ticket."""
        limite = time.time() + max_espera
        while True:
            reg = self.estado_ticket(libro, periodo, ticket)
            cod = str(reg.get("codEstadoProceso", ""))
            des = str(reg.get("desEstadoProceso", ""))
            log.info("Ticket %s: %s %s", ticket, cod, des)
            if cod in ESTADOS_OK and reg.get("archivoReporte"):
                return reg
            if cod in ESTADOS_ERROR:
                raise SireError(f"El ticket {ticket} terminó con errores ({des}): {reg.get('detalleTicket')}")
            if time.time() > limite:
                raise SireError(f"Tiempo de espera agotado para el ticket {ticket} (último estado: {des})")
            time.sleep(intervalo)

    def descargar_archivo(self, reg_ticket: dict, cod_libro: str) -> list[tuple[str, bytes]]:
        """Descarga los archivos de reporte de un ticket terminado. Devuelve [(nombre, bytes)]."""
        archivos = reg_ticket.get("archivoReporte") or []
        if not archivos:
            raise SireError(f"El ticket no tiene archivos para descargar: {reg_ticket}")
        resultado = []
        for arch in archivos:
            nombre = arch.get("nomArchivoReporte")
            # SUNAT devuelve el campo con la errata "codTipoAchivoReporte". Si viene null,
            # el manual indica enviar el mismo valor: el texto "null".
            tipo = arch.get("codTipoAchivoReporte", arch.get("codTipoArchivoReporte"))
            params = {
                "nomArchivoReporte": nombre,
                "codTipoArchivoReporte": "null" if tipo is None else tipo,
                # Ventas 5.17 exige además estos campos; Compras 5.32 solo los dos primeros
                "codLibro": cod_libro,
                "perTributario": reg_ticket.get("perTributario"),
                "codProceso": reg_ticket.get("codProceso"),
                "numTicket": reg_ticket.get("numTicket"),
            }
            r = self._get(RUTA_DESCARGA, params)
            resultado.append((nombre, r.content))
        return resultado

    # ------------------------------------------------------------ alto nivel
    def descargar_propuesta(
        self, libro: str, periodo: str, destino: str | Path = "descargas", formato: str = "txt"
    ) -> list[Path]:
        """Flujo completo: solicita, espera y descarga la propuesta. Devuelve las rutas extraídas."""
        cod_libro = LIBRO_VENTAS if libro == "ventas" else LIBRO_COMPRAS
        ticket = self.solicitar_propuesta(libro, periodo, formato)
        reg = self.esperar_ticket(libro, periodo, ticket)

        carpeta = Path(destino) / libro / periodo
        carpeta.mkdir(parents=True, exist_ok=True)
        guardados: list[Path] = []
        for nombre, contenido in self.descargar_archivo(reg, cod_libro):
            if zipfile.is_zipfile(io.BytesIO(contenido)):
                with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
                    for miembro in zf.namelist():
                        ruta = carpeta / Path(miembro).name  # evita rutas con ../
                        ruta.write_bytes(zf.read(miembro))
                        guardados.append(ruta)
            else:
                ruta = carpeta / Path(nombre).name
                ruta.write_bytes(contenido)
                guardados.append(ruta)
        return guardados


def _mensaje_error(r: requests.Response) -> str:
    """Convierte la respuesta de error de SUNAT ({"cod","msg","errors":[...]}) en texto legible."""
    try:
        body = r.json()
    except ValueError:
        return r.text
    if not isinstance(body, dict):
        return r.text
    errores = "; ".join(f"[{e.get('cod')}] {e.get('msg')}" for e in body.get("errors") or [])
    return f"{body.get('msg', '')} {errores}".strip() or r.text


def _validar_periodo(periodo: str) -> None:
    if len(periodo) != 6 or not periodo.isdigit() or not 1 <= int(periodo[4:]) <= 12:
        raise ValueError(f"Periodo inválido '{periodo}': use el formato AAAAMM, p. ej. 202410")
