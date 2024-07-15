docker run --rm -it --name dspace-db-comp -v %cd%:/dq/scripts -v %cd%/../../../input/dump:/dq/dump -p 5432:5432 -e POSTGRES_DB=empty -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=dspace postgres /bin/bash -c "cd /dq/scripts && ./init.db.v6.sh"
pause
