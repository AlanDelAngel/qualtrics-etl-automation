# Qualtrics ETL Automation

**Automatización que extrae encuestas de Qualtrics, las limpia y las deja listas para Power BI, sin trabajo manual.**

![Arquitectura del ETL](docs/arquitectura.png)

## El problema

En el área de Business Intelligence de una universidad, las respuestas de las encuestas institucionales se descargaban a mano desde Qualtrics, se limpiaban en hojas de cálculo y se copiaban a la carpeta que alimentaba los tableros de Power BI. Esto tenía tres problemas:

- **Más de 10 horas a la semana** de trabajo manual de extracción y limpieza.
- **Varias versiones del mismo archivo** circulando, sin un estándar que garantizara la integridad de los datos.
- Tableros que se actualizaban tarde porque dependían de que alguien hiciera la descarga.

## La solución

Un proceso ETL en Python, sin costo de licencias, que:

1. **Extrae** las respuestas con la API REST de Qualtrics (exportación asíncrona: iniciar, consultar estado y descargar el ZIP).
2. **Transforma** el CSV: quita las filas de metadatos de Qualtrics, filtra por periodo y conserva solo las columnas necesarias.
3. **Carga** un CSV por encuesta en una carpeta de OneDrive, desde donde Power BI actualiza los tableros.
4. Se **ejecuta solo** con el Programador de tareas de Windows, y cada encuesta se define en un archivo JSON, así que agregar una no requiere tocar el código.

## Resultados

| Indicador | Resultado |
| --- | --- |
| Tiempo de extracción y limpieza | **+10 horas ahorradas por semana** |
| Productividad del área en reportes de encuestas | **+30%** |
| Costo de licencias | **$0** (Python, Task Scheduler y OneDrive) |
| Tableros alimentados | Participación en encuesta institucional, encuesta de indagación y rediseño del tablero de eficiencia terminal y continuidad |

## Estructura

```
etl_qualtrics.py          Script principal (extracción, transformación y carga)
ejecutar_etl.bat          Ejecuta el ETL para cada encuesta; lo llama el Programador de tareas
encuestas/                Una configuración .json por encuesta
  ejemplos/               Plantillas de configuración
.env.example              Plantilla de credenciales (el .env real nunca se sube a Git)
tests/test_etl.py         Pruebas sin conexión con una API simulada
ejemplos/                 CSV de salida de ejemplo (datos ficticios)
docs/                     Diagrama de arquitectura y réplicas de los tableros
```

## Instalación

Requisitos: Windows, Python 3.10 o superior y la carpeta de OneDrive sincronizada en el equipo.

```bash
git clone https://github.com/AlanDelAngel/qualtrics-etl-automation.git
cd qualtrics-etl-automation
pip install -r requirements.txt
```

> Si el equipo tiene varias versiones de Python, usa `where python` en la terminal para saber cuál usar e instala las dependencias con esa misma.

### 1. Credenciales

Copia `.env.example` como `.env` y llena tus datos:

```env
QUALTRICS_API_TOKEN=tu_token
QUALTRICS_DATA_CENTER=tu_data_center
```

El token está en Qualtrics, en **Configuración de la cuenta > Qualtrics IDs**. El data center es la primera parte de la URL de tu cuenta.

### 2. Configurar una encuesta

Copia una plantilla de `encuestas/ejemplos/` a `encuestas/` y edítala:

```json
{
  "survey_id": "SV_XXXXXXXXXXXXXXX",
  "nombre_csv": "participacion",
  "carpeta_salida": "C:\\Users\\TU_USUARIO\\OneDrive\\Datos\\Encuestas",
  "columna_periodo": "Periodo",
  "periodos": ["2025 Ene - May", "2025 Ago - Dic"],
  "columnas": ["ResponseId", "EndDate", "Periodo", "Campus", "Q1", "Q2"]
}
```

| Campo | Obligatorio | Descripción |
| --- | --- | --- |
| `survey_id` | Sí | ID de la encuesta en Qualtrics (empieza con `SV_`) |
| `nombre_csv` | Sí | Nombre del archivo de salida; usa uno distinto por encuesta |
| `carpeta_salida` | Sí | Carpeta local (por ejemplo, la de OneDrive que lee Power BI) |
| `columna_periodo` | No | Columna usada para filtrar periodos (por defecto `Periodo`) |
| `periodos` | No | Periodos a conservar, escritos igual que en Qualtrics. Lista vacía = todos |
| `columnas` | No | IDs de columna a conservar. Lista vacía = todas |
| `conservar_fila_preguntas` | No | `true` para mantener la fila con el texto de cada pregunta |

### 3. Ejecutar

```bash
python etl_qualtrics.py --config encuestas/participacion.json
```

O ejecuta `ejecutar_etl.bat`, que procesa todas las encuestas de la carpeta `encuestas/`. Cada ejecución queda registrada en `logs/etl.log`.

### 4. Programar la ejecución automática

1. Presiona `Win + R`, escribe `taskschd.msc` y abre **Crear tarea básica**.
2. Ponle un nombre único y elige la frecuencia (la tarea solo corre si el equipo está encendido).
3. En **Acción**, elige **Iniciar un programa** y selecciona `ejecutar_etl.bat`.
4. En la pestaña **Configuración**, activa *Permitir que la tarea se ejecute a petición*, *Ejecutar la tarea lo antes posible si no se ejecutó a la hora programada* y *Detener la tarea si se ejecuta durante más de 2 horas*.
5. Verifica que OneDrive inicie con Windows para que los archivos se sincronicen.

## Manejo de errores

El ETL se detiene con un mensaje claro, y **sin sobrescribir el CSV anterior**, cuando:

- faltan credenciales o la configuración tiene campos vacíos o un JSON inválido;
- una columna de `columnas` o la `columna_periodo` no existe en la encuesta;
- Qualtrics rechaza la solicitud, falla al generar el archivo o tarda más de 15 minutos.

Los filtros son sensibles a mayúsculas, espacios y acentos: los periodos y columnas deben escribirse exactamente como aparecen en Qualtrics. Si ningún registro coincide con los periodos, se genera un CSV vacío y queda una advertencia en el log.

## Pruebas

Las pruebas simulan la API de Qualtrics con un ZIP de datos ficticios, así que no necesitan conexión ni credenciales:

```bash
pip install -r requirements-dev.txt
pytest
```

## Siguientes pasos

- Interfaz sencilla para que usuarios sin conocimientos de programación configuren y ejecuten extracciones.
- Migrar los repositorios de encuestas a un Data Warehouse con flujos en la nube, como fuente única de la verdad.

## Autor

**Alan Adelfo Del Angel Figueroa**, desarrollado en el área de Business Intelligence de Universidad Tecmilenio (2025). Publicado con autorización, sin datos ni configuraciones de la institución.

[github.com/AlanDelAngel](https://github.com/AlanDelAngel)

---

### English summary

Python ETL that exports survey responses from the Qualtrics REST API, removes Qualtrics metadata rows, filters by period and columns, and writes one CSV per survey to a OneDrive folder that feeds Power BI dashboards. It runs unattended through Windows Task Scheduler, keeps credentials in a local `.env`, defines each survey in a JSON file, logs every run and includes offline tests with a mocked API. In production it saved **10+ hours per week** of manual extraction and cleaning and increased the team's reporting productivity by **30%**, at no licensing cost.
