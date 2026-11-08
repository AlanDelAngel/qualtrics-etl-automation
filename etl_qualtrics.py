"""
ETL de encuestas de Qualtrics a CSV
===================================

Exporta las respuestas de una encuesta de Qualtrics por su API REST (v3),
aplica filtros opcionales de periodo y columnas, limpia las filas de metadatos
y guarda un CSV en una carpeta local (por ejemplo, una carpeta de OneDrive que
alimenta un tablero de Power BI).

Uso:
    python etl_qualtrics.py --config encuestas/ejemplo_participacion.json

Las credenciales (token y data center) se leen de variables de entorno o de un
archivo .env junto a este script. Nunca se escriben en el codigo.

Autor: Alan Adelfo Del Angel Figueroa
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

import pandas as pd
import requests

API_VERSION = "v3"
INTERVALO_CONSULTA_S = 2          # segundos entre consultas del estado de la exportacion
ESPERA_MAXIMA_S = 15 * 60         # tiempo maximo para que Qualtrics genere el archivo
FILAS_METADATOS_QUALTRICS = 2     # texto de la pregunta + fila ImportId

log = logging.getLogger("etl_qualtrics")


class ErrorETL(RuntimeError):
    """Error controlado del ETL con un mensaje claro para el usuario."""


# ----------------------------------------------------------------------------
# Configuracion
# ----------------------------------------------------------------------------
@dataclass
class ConfigEncuesta:
    #Configuracion de una encuesta a extraer (se carga desde un JSON).

    survey_id: str
    nombre_csv: str
    carpeta_salida: str
    columna_periodo: str = "Periodo"
    periodos: List[str] = field(default_factory=list)
    columnas: List[str] = field(default_factory=list)
    conservar_fila_preguntas: bool = False

    @classmethod
    def desde_json(cls, ruta: Path) -> "ConfigEncuesta":
        try:
            datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ErrorETL(f"No existe el archivo de configuracion: {ruta}")
        except json.JSONDecodeError as e:
            raise ErrorETL(f"El archivo {ruta} no es un JSON valido: {e}")

        faltantes = [k for k in ("survey_id", "nombre_csv", "carpeta_salida") if not datos.get(k)]
        if faltantes:
            raise ErrorETL(f"Faltan campos obligatorios en {ruta}: {', '.join(faltantes)}")

        return cls(
            survey_id=datos["survey_id"],
            nombre_csv=datos["nombre_csv"],
            carpeta_salida=datos["carpeta_salida"],
            columna_periodo=datos.get("columna_periodo", "Periodo"),
            periodos=normalizar_lista(datos.get("periodos"), "periodos"),
            columnas=normalizar_lista(datos.get("columnas"), "columnas"),
            conservar_fila_preguntas=bool(datos.get("conservar_fila_preguntas", False)),
        )


def cargar_env(ruta: Path) -> None:
    #Carga variables CLAVE=valor de un archivo .env sin dependencias externas.
    #Las variables que ya existen en el entorno tienen prioridad.
    
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def credenciales() -> tuple[str, str]:
    token = os.environ.get("QUALTRICS_API_TOKEN", "")
    data_center = os.environ.get("QUALTRICS_DATA_CENTER", "")
    if not token or not data_center:
        raise ErrorETL(
            "Faltan credenciales. Define QUALTRICS_API_TOKEN y QUALTRICS_DATA_CENTER "
            "en el archivo .env (usa .env.example como guia)."
        )
    return token, data_center


def normalizar_lista(valor, nombre: str) -> List[str]:
    #Convierte "", None, un string o una lista de strings en una lista limpia.

    #Una lista vacia significa "sin filtro".
    
    if valor is None or valor == "":
        return []
    if isinstance(valor, str):
        return [valor.strip()] if valor.strip() else []
    if isinstance(valor, (list, tuple, set)):
        no_texto = [v for v in valor if not isinstance(v, str)]
        if no_texto:
            raise ErrorETL(f"'{nombre}' solo puede contener textos; revisa: {no_texto}")
        return [v.strip() for v in valor if v.strip()]
    raise ErrorETL(f"'{nombre}' debe ser un texto o una lista de textos.")


# ----------------------------------------------------------------------------
# Extraccion (API de Qualtrics)
# ----------------------------------------------------------------------------
class ClienteQualtrics:
    #Cliente minimo para el flujo de exportacion de respuestas de la API v3.

    def __init__(self, token: str, data_center: str, sesion: requests.Session | None = None):
        self.base = f"https://{data_center}.qualtrics.com/API/{API_VERSION}"
        self.sesion = sesion or requests.Session()
        self.sesion.headers.update({"x-api-token": token, "content-type": "application/json"})

    def _url(self, survey_id: str, sufijo: str = "") -> str:
        return f"{self.base}/surveys/{survey_id}/export-responses{sufijo}"

    @staticmethod
    def _revisar(resp: requests.Response, paso: str) -> dict:
        if not resp.ok:
            raise ErrorETL(f"{paso}: HTTP {resp.status_code} - {resp.text[:300]}")
        return resp.json()

    def iniciar_exportacion(self, survey_id: str) -> str:
        log.info("Iniciando exportacion de respuestas...")
        datos = self._revisar(
            self.sesion.post(self._url(survey_id), data=json.dumps({"format": "csv"}), timeout=60),
            "No se pudo iniciar la exportacion",
        )
        try:
            return datos["result"]["progressId"]
        except (KeyError, TypeError):
            raise ErrorETL(f"Respuesta inesperada al iniciar la exportacion: {datos}")

    def esperar_archivo(self, survey_id: str, progress_id: str) -> str:
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_MAXIMA_S:
            datos = self._revisar(
                self.sesion.get(self._url(survey_id, f"/{progress_id}"), timeout=60),
                "No se pudo consultar el estado de la exportacion",
            )
            estado = datos.get("result", {}).get("status")
            log.info("Estado de la exportacion: %s", estado)
            if estado == "complete":
                return datos["result"]["fileId"]
            if estado == "failed":
                raise ErrorETL("Qualtrics reporto un error al generar el archivo.")
            time.sleep(INTERVALO_CONSULTA_S)
        raise ErrorETL(f"La exportacion tardo mas de {ESPERA_MAXIMA_S // 60} minutos.")

    def descargar_zip(self, survey_id: str, file_id: str) -> bytes:
        resp = self.sesion.get(self._url(survey_id, f"/{file_id}/file"), timeout=300)
        if not resp.ok:
            raise ErrorETL(f"No se pudo descargar el archivo: HTTP {resp.status_code}")
        return resp.content

    def exportar(self, survey_id: str) -> bytes:
        progress_id = self.iniciar_exportacion(survey_id)
        file_id = self.esperar_archivo(survey_id, progress_id)
        return self.descargar_zip(survey_id, file_id)


# ----------------------------------------------------------------------------
# Transformacion
# ----------------------------------------------------------------------------
def leer_csv_de_zip(zip_bytes: bytes) -> pd.DataFrame:
    #Lee el primer CSV del ZIP de Qualtrics. Todo se lee como texto.
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise ErrorETL("El ZIP descargado no contiene ningun CSV.")
        with zf.open(csvs[0]) as f:
            return pd.read_csv(f, header=0, dtype=str)


def quitar_metadatos(df: pd.DataFrame, conservar_fila_preguntas: bool = False) -> pd.DataFrame:
    """Quita las filas de metadatos que Qualtrics agrega debajo del encabezado.

    El CSV de Qualtrics trae, despues de los IDs de columna, una fila con el texto
    de cada pregunta y otra con un JSON "ImportId". Se eliminan por posicion y
    ANTES de filtrar, para no borrar respuestas reales por error.
    """

    filas = [1] if conservar_fila_preguntas else list(range(FILAS_METADATOS_QUALTRICS))
    filas = [i for i in filas if i < len(df)]
    return df.drop(df.index[filas]).reset_index(drop=True)


def filtrar_periodos(df: pd.DataFrame, columna: str, periodos: Sequence[str]) -> pd.DataFrame:
    if not periodos:
        log.info("Sin filtro de periodo: se conservan todas las filas.")
        return df
    if columna not in df.columns:
        raise ErrorETL(f"La columna de periodo '{columna}' no existe en la encuesta.")
    salida = df[df[columna].isin(periodos)].reset_index(drop=True)
    log.info("Filtro por %d periodo(s) %s: %d filas", len(periodos), list(periodos), len(salida))
    if salida.empty:
        log.warning("Ningun registro coincide con los periodos indicados.")
    return salida


def conservar_columnas(df: pd.DataFrame, columnas: Sequence[str]) -> pd.DataFrame:
    if not columnas:
        log.info("Sin filtro de columnas: se conservan todas.")
        return df
    faltantes = [c for c in columnas if c not in df.columns]
    if faltantes:
        muestra = ", ".join(df.columns[:30])
        raise ErrorETL(f"No existen estas columnas: {faltantes}. Algunas disponibles: {muestra}")
    return df[list(columnas)].copy()


def sufijo_periodos(periodos: Sequence[str]) -> str:
    if not periodos:
        return "todos"
    sufijo = "_".join(p.replace(" ", "").replace("/", "_") for p in periodos)
    return sufijo if len(sufijo) <= 80 else f"{len(periodos)}_periodos"


def transformar(df: pd.DataFrame, cfg: ConfigEncuesta) -> pd.DataFrame:
    #Orden importante: metadatos -> periodos -> columnas.

    df = quitar_metadatos(df, cfg.conservar_fila_preguntas)
    df = filtrar_periodos(df, cfg.columna_periodo, cfg.periodos)
    return conservar_columnas(df, cfg.columnas)


# ----------------------------------------------------------------------------
# Carga
# ----------------------------------------------------------------------------
def guardar_csv(df: pd.DataFrame, cfg: ConfigEncuesta) -> Path:
    carpeta = Path(cfg.carpeta_salida)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"qualtrics_{cfg.nombre_csv}_{sufijo_periodos(cfg.periodos)}.csv"
    temporal = ruta.with_suffix(".tmp")
    # Se escribe primero un archivo temporal y luego se reemplaza, para que
    # Power BI nunca lea un CSV a medio escribir.
    df.to_csv(temporal, index=False, encoding="utf-8-sig")
    temporal.replace(ruta)
    return ruta


# ----------------------------------------------------------------------------
# Flujo principal
# ----------------------------------------------------------------------------
def ejecutar(cfg: ConfigEncuesta, cliente: ClienteQualtrics) -> Path:
    zip_bytes = cliente.exportar(cfg.survey_id)
    df = transformar(leer_csv_de_zip(zip_bytes), cfg)
    ruta = guardar_csv(df, cfg)
    log.info("CSV guardado (%d filas, %d columnas): %s", len(df), df.shape[1], ruta)
    return ruta


def configurar_logs(carpeta: Path) -> None:
    carpeta.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(carpeta / "etl.log", encoding="utf-8"),
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exporta una encuesta de Qualtrics a CSV.")
    parser.add_argument("--config", required=True, help="Ruta al JSON de la encuesta")
    args = parser.parse_args(argv)

    base = Path(__file__).resolve().parent
    configurar_logs(base / "logs")
    cargar_env(base / ".env")

    try:
        cfg = ConfigEncuesta.desde_json(Path(args.config))
        token, data_center = credenciales()
        log.info("Encuesta: %s", cfg.nombre_csv)
        ejecutar(cfg, ClienteQualtrics(token, data_center))
    except ErrorETL as e:
        log.error("%s", e)
        log.error("El CSV NO se actualizo. Corrige el error y vuelve a ejecutar.")
        return 1
    except requests.RequestException as e:
        log.error("Error de red al conectar con Qualtrics: %s", e)
        return 1
    log.info("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
