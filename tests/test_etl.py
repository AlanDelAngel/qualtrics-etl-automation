"""Pruebas sin conexion: la API de Qualtrics se simula con un ZIP de datos ficticios."""
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl_qualtrics as etl  # noqa: E402

CSV_QUALTRICS = (
    "ResponseId,EndDate,Periodo,Campus,Q1\n"
    "Response ID,End Date,Periodo,Campus,Que tan satisfecho estas?\n"
    '"{""ImportId"":""_recordId""}","{""ImportId"":""endDate""}","{""ImportId"":""Periodo""}","{""ImportId"":""Campus""}","{""ImportId"":""QID1""}"\n'
    "R_001,2025-03-01,2025 Ene - May,Campus Norte,5\n"
    "R_002,2025-03-02,2025 Ene - May,Campus Sur,4\n"
    "R_003,2025-09-10,2025 Ago - Dic,Campus Norte,3\n"
)


def zip_de_prueba(csv_texto: str = CSV_QUALTRICS) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Encuesta de prueba.csv", csv_texto)
    return buf.getvalue()


def cfg(tmp_path, **cambios) -> etl.ConfigEncuesta:
    base = dict(survey_id="SV_TEST", nombre_csv="prueba", carpeta_salida=str(tmp_path))
    base.update(cambios)
    return etl.ConfigEncuesta(**base)


def test_quita_las_dos_filas_de_metadatos():
    df = etl.leer_csv_de_zip(zip_de_prueba())
    limpio = etl.quitar_metadatos(df)
    assert list(limpio["ResponseId"]) == ["R_001", "R_002", "R_003"]


def test_puede_conservar_la_fila_de_preguntas():
    df = etl.leer_csv_de_zip(zip_de_prueba())
    limpio = etl.quitar_metadatos(df, conservar_fila_preguntas=True)
    assert limpio.iloc[0]["Q1"] == "Que tan satisfecho estas?"
    assert len(limpio) == 4


def test_filtra_por_periodo_aunque_no_se_conserve_esa_columna(tmp_path):
    df = etl.leer_csv_de_zip(zip_de_prueba())
    salida = etl.transformar(df, cfg(tmp_path, periodos=["2025 Ene - May"], columnas=["ResponseId", "Q1"]))
    assert list(salida.columns) == ["ResponseId", "Q1"]
    assert list(salida["ResponseId"]) == ["R_001", "R_002"]


def test_sin_filtros_conserva_todo(tmp_path):
    df = etl.leer_csv_de_zip(zip_de_prueba())
    salida = etl.transformar(df, cfg(tmp_path))
    assert salida.shape == (3, 5)


def test_columna_inexistente_da_error_claro(tmp_path):
    df = etl.leer_csv_de_zip(zip_de_prueba())
    with pytest.raises(etl.ErrorETL, match="No existen estas columnas"):
        etl.transformar(df, cfg(tmp_path, columnas=["NoExiste"]))


@pytest.mark.parametrize("valor,esperado", [
    ("", []), (None, []), ("2025 Ene - May", ["2025 Ene - May"]),
    (["  A ", "B", ""], ["A", "B"]),
])
def test_normalizar_lista(valor, esperado):
    assert etl.normalizar_lista(valor, "periodos") == esperado


def test_normalizar_lista_rechaza_valores_que_no_son_texto():
    with pytest.raises(etl.ErrorETL):
        etl.normalizar_lista(["A", 3], "periodos")


def test_nombre_del_archivo_refleja_los_periodos():
    assert etl.sufijo_periodos([]) == "todos"
    assert etl.sufijo_periodos(["2025 Ene - May"]) == "2025Ene-May"


def test_config_desde_json_valida_campos(tmp_path):
    ruta = tmp_path / "enc.json"
    ruta.write_text(json.dumps({"survey_id": "SV_1"}), encoding="utf-8")
    with pytest.raises(etl.ErrorETL, match="Faltan campos obligatorios"):
        etl.ConfigEncuesta.desde_json(ruta)


def test_flujo_completo_con_api_simulada(tmp_path):
    sesion = MagicMock()
    sesion.headers = {}
    respuestas = iter([
        MagicMock(ok=True, json=lambda: {"result": {"progressId": "ES_1"}}),
    ])
    sesion.post.side_effect = lambda *a, **k: next(respuestas)
    estados = iter([
        MagicMock(ok=True, json=lambda: {"result": {"status": "inProgress"}}),
        MagicMock(ok=True, json=lambda: {"result": {"status": "complete", "fileId": "F_1"}}),
        MagicMock(ok=True, content=zip_de_prueba()),
    ])
    sesion.get.side_effect = lambda *a, **k: next(estados)
    etl.INTERVALO_CONSULTA_S = 0

    cliente = etl.ClienteQualtrics("token", "dc", sesion=sesion)
    ruta = etl.ejecutar(cfg(tmp_path, periodos=["2025 Ago - Dic"]), cliente)

    assert ruta.name == "qualtrics_prueba_2025Ago-Dic.csv"
    salida = pd.read_csv(ruta, dtype=str)
    assert list(salida["ResponseId"]) == ["R_003"]
    assert sesion.headers["x-api-token"] == "token"


def test_credenciales_faltantes(monkeypatch):
    monkeypatch.delenv("QUALTRICS_API_TOKEN", raising=False)
    monkeypatch.delenv("QUALTRICS_DATA_CENTER", raising=False)
    with pytest.raises(etl.ErrorETL, match="Faltan credenciales"):
        etl.credenciales()
