"""Descarga la propuesta SIRE de ventas y/o compras de un periodo.

Uso:
    python descargar_sire.py 202410                 # ventas y compras
    python descargar_sire.py 202410 --libro compras --formato csv
    python descargar_sire.py --periodos ventas      # lista periodos disponibles
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from sire import Credenciales, SireClient, SireError, LIBRO_COMPRAS, LIBRO_VENTAS


def credenciales_desde_env() -> Credenciales:
    load_dotenv()
    faltan = [v for v in ("SUNAT_RUC", "SUNAT_USUARIO_SOL", "SUNAT_CLAVE_SOL",
                          "SUNAT_CLIENT_ID", "SUNAT_CLIENT_SECRET") if not os.getenv(v)]
    if faltan:
        sys.exit(f"Faltan variables en .env: {', '.join(faltan)} (vea .env.example)")
    return Credenciales(
        ruc=os.environ["SUNAT_RUC"],
        usuario_sol=os.environ["SUNAT_USUARIO_SOL"],
        clave_sol=os.environ["SUNAT_CLAVE_SOL"],
        client_id=os.environ["SUNAT_CLIENT_ID"],
        client_secret=os.environ["SUNAT_CLIENT_SECRET"],
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Descarga registros de compras/ventas desde SUNAT SIRE")
    p.add_argument("periodo", nargs="?", help="Periodo tributario AAAAMM, p. ej. 202410")
    p.add_argument("--libro", choices=["ventas", "compras", "ambos"], default="ambos")
    p.add_argument("--formato", choices=["txt", "csv", "excel"], default="txt")
    p.add_argument("--destino", default="descargas")
    p.add_argument("--periodos", choices=["ventas", "compras"], help="Solo listar periodos disponibles")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cliente = SireClient(credenciales_desde_env())

    try:
        if args.periodos:
            cod = LIBRO_VENTAS if args.periodos == "ventas" else LIBRO_COMPRAS
            for ejercicio in cliente.periodos(cod):
                print(f"{ejercicio.get('numEjercicio')} ({ejercicio.get('desEstado', '')})")
                for per in ejercicio.get("lisPeriodos") or []:
                    print(f"  {per.get('perTributario')}  {per.get('desEstado', '')}")
            return
        if not args.periodo:
            p.error("indique el periodo (AAAAMM) o use --periodos")

    except (SireError, ValueError) as e:
        sys.exit(f"Error: {e}")

    libros = ["ventas", "compras"] if args.libro == "ambos" else [args.libro]
    fallos = 0
    for libro in libros:
        print(f"Descargando {libro} {args.periodo}...")
        try:
            for ruta in cliente.descargar_propuesta(libro, args.periodo, args.destino, args.formato):
                print(f"  -> {ruta}")
        except (SireError, ValueError) as e:
            fallos += 1
            print(f"  Error: {e}", file=sys.stderr)
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
