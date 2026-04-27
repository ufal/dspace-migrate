Copy SQL dumps to `../input/`:

```
ls ../input/dump
clarin-dspace-8.8.23.sql  clarin-utilities-8.8.23.sql
```


# Mock OXFORD

1. download dump
```
scp -r jm@dev-5.pc:/opt/dspace-data/clarin-dspace-oxford/ __data-oxford
```
2. create and run OXFORD.start.bat

```
set "INSTANCE=5"
set "REMOTE_HOST=dev-5.pc"
set "SSH_USER=jm"
set "DATADIR=%cd%\__data-oxford"
set "LOCAL_DB5_PORT=5432"
set "LOCAL_DB7_PORT=543%INSTANCE%"
set "REMOTE_DB7_PORT=543%INSTANCE%"
set "DB5_CONTAINER=dspace-import-db5"
set "INIT_SCRIPT=init.dspacedb5.sh"
set "DETACH=true"

call "%cd%\start.local.dspace.db.bat"
if errorlevel 1 exit /b 1

start "db%INSTANCE%-tunnel" cmd /k "ssh -N -L 127.0.0.1:%LOCAL_DB7_PORT%:127.0.0.1:%REMOTE_DB7_PORT% %SSH_USER%@%REMOTE_HOST%"

echo Local DB5: 127.0.0.1:%LOCAL_DB5_PORT%
echo Forwarded DB7: 127.0.0.1:%LOCAL_DB7_PORT% -> %REMOTE_HOST%:%REMOTE_DB7_PORT%
```

### Testing bitstream prerequisite

If `backend.testing=true`, the fallback testing bitstream must exist in the server assetstore with the proper internal-id path.
If it is missing, you can get many `put_bitstream` errors.

Check assetstore files in the server container:

```bash
docker exec -it dspace5 /bin/bash -c "ls -lahR /dspace/assetstore"
```

On `dev-5.pc`, if the testing instance is `dspace5`, copy Oxford assetstore files to the docker volume:

```bash
root@dev-5:/var/lib/docker/volumes/dspace-5_assetstore/_data# cp -R /opt/dspace-data/clarin-dspace-oxford/assetstore/* ./
```

3. execute

```
python repo.py --assetstore=../scripts/__data-oxford/assetstore --config=backend.endpoint=http://dev-5.pc:85/repository/server/api
```