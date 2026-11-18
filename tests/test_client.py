"""Prueba del flujo completo contra una sesión HTTP simulada (no llama a SUNAT)."""

import io
import zipfile

import pytest

from sire import Credenciales, SireClient
from sire.client import _validar_periodo


class Resp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data
        self.content = content
        self.text = str(json_data)

    def json(self):
        return self._json


def zip_con(nombre, texto):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(nombre, texto)
    return buf.getvalue()


class SesionFalsa:
    def __init__(self):
        self.llamadas = []
        self.consultas_ticket = 0

    def post(self, url, data, timeout):
        self.llamadas.append(("POST", url, data))
        return Resp(json_data={"access_token": "TOK", "expires_in": 3600})

    def get(self, url, params, headers, timeout):
        self.llamadas.append(("GET", url, params))
        assert headers["Authorization"] == "Bearer TOK"
        if url.endswith("exportacioncomprobantepropuesta"):
            return Resp(json_data={"numTicket": "20240300000001"})
        if url.endswith("consultaestadotickets"):
            self.consultas_ticket += 1
            terminado = self.consultas_ticket > 1
            return Resp(json_data={"registros": [{
                "numTicket": "20240300000001",
                "perTributario": "202410",
                "codProceso": "10",
                "codEstadoProceso": "06" if terminado else "02",
                "desEstadoProceso": "Terminado" if terminado else "En proceso",
                "archivoReporte": [{"nomArchivoReporte": "LE2010_compras.zip",
                                    "codTipoAchivoReporte": "00"}] if terminado else [],
            }]})
        if url.endswith("archivoreporte"):
            return Resp(content=zip_con("LE2010_compras.txt", "linea1\nlinea2\n"))
        raise AssertionError(f"URL inesperada {url}")


def test_descarga_compras(tmp_path, monkeypatch):
    monkeypatch.setattr("sire.client.time.sleep", lambda s: None)
    sesion = SesionFalsa()
    cred = Credenciales("20123456789", "USR", "clave", "cid", "csecret")
    archivos = SireClient(cred, session=sesion).descargar_propuesta("compras", "202410", tmp_path)

    assert [a.name for a in archivos] == ["LE2010_compras.txt"]
    assert archivos[0].read_text() == "linea1\nlinea2\n"

    _, url_token, data = sesion.llamadas[0]
    assert url_token == "https://api-seguridad.sunat.gob.pe/v1/clientessol/cid/oauth2/token/"
    assert data["username"] == "20123456789USR"
    descarga = [c for c in sesion.llamadas if c[1].endswith("archivoreporte")][0][2]
    assert descarga["codTipoArchivoReporte"] == "00" and descarga["codLibro"] == "080000"
    # el token se reutiliza: un solo POST
    assert sum(1 for c in sesion.llamadas if c[0] == "POST") == 1


@pytest.mark.parametrize("periodo", ["2024", "202413", "abcdef", "2024100"])
def test_periodo_invalido(periodo):
    with pytest.raises(ValueError):
        _validar_periodo(periodo)
