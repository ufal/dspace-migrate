@echo off

if not defined DATADIR set "DATADIR=%cd%\..\input"
if not defined LOCAL_DB5_PORT set "LOCAL_DB5_PORT=5432"
if not defined DB5_CONTAINER set "DB5_CONTAINER=dspace-db5"
if not defined INIT_SCRIPT set "INIT_SCRIPT=init.dspacedb5.sh"

set "DUMP_DIR=%DATADIR%\dump"

if not exist "%DUMP_DIR%" (
	echo Missing dump directory: %DUMP_DIR%
	exit /b 1
)

docker stop %DB5_CONTAINER% >nul 2>&1

docker run --rm -it --name %DB5_CONTAINER% -v "%cd%":/dq/scripts -v "%DUMP_DIR%":/dq/dump -p 127.0.0.1:%LOCAL_DB5_PORT%:5432 -e POSTGRES_DB=empty -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=dspace postgres /bin/bash -c "cd /dq/scripts && ./%INIT_SCRIPT%"

pause
