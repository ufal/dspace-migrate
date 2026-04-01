# How to Write New Tests
Check the test.example package. Everything necessary should be there.

Test data are in `test/data` folder.
If your test data contains special characters such as čřšáý, it is recommended
to make `.stripped` variation of the file. 
E.g. `my_format.json` and `my_format.stripped.json` for loading data
and `my_format.test.xml` and `my_format.test.stripped.xml` for testing.

If not on dev-5 (e.g., when run on localhost), the `.stripped` version of files will be loaded.
The reason for this is that when DSpace runs on Windows, it has trouble with special characters.


## Settings
See const.py for constants used in testing.

To set up logs, navigate to support.logs.py and modify the method set_up_logging.

## Run

In order to run tests, use command
`python -m unittest`

Recommended variation is
`python -m unittest -v 2> output.txt`
which leaves result in output.txt

Before running for the first time, requirements must be installed with the following command:
`pip install -r requirements.txt`

It is possible to run in Pycharm with configuration like so:

![image](https://user-images.githubusercontent.com/88670521/186934112-d0f828fd-a809-4ed8-bbfd-4457b734d8fd.png)


# How to Re-initialize DSpace 7 Database

Recreate your local CLARIN-DSpace 7.* database **NOTE: all data will be deleted**

- Install again the database following the official tutorial steps: https://wiki.lyrasis.org/display/DSDOC7x/Installing+DSpace#InstallingDSpace-PostgreSQL11.x,12.xor13.x(withpgcryptoinstalled)
- Or try to run these commands in the <PSQL_PATH>/bin:
> - `createdb --username=postgres --owner=dspace --encoding=UNICODE dspace` // create database
> - `psql --username=postgres dspace -c "CREATE EXTENSION pgcrypto;"` // Add pgcrypto extension
> > If it throws a warning that the `-c` parameter was ignored, just write a `CREATE EXTENSION pgcrypto;` command in the database cmd.
> > CREATE EXTENSION pgcrypto;
![image](https://user-images.githubusercontent.com/90026355/228528044-f6ad178c-f525-4b15-b6cc-03d8d94c8ccc.png)
