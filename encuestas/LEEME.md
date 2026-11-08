# Configuraciones de encuestas

Cada archivo `.json` de esta carpeta es una encuesta que el ETL va a extraer.
`ejecutar_etl.bat` procesa todos los `.json` que esten directamente aqui
(no los de `ejemplos/`).

Para agregar una encuesta, copia un archivo de `ejemplos/`, ponlo en esta carpeta
y cambia sus valores. Usa un `nombre_csv` distinto por encuesta para no sobrescribir archivos.
