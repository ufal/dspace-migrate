[![Test dspace on dev-5](https://github.com/dataquest-dev/dspace-blackbox-testing/actions/workflows/test.yml/badge.svg)](https://github.com/dataquest-dev/dspace-blackbox-testing/actions/workflows/test.yml)

# DSpace-python-api
Used for blackbox testing and data-ingestion procedures.

# How to migrate CLARIN-DSpace5.* to CLARIN-DSpace7.*

### Important:
Make sure that your email server is NOT running because some of the endpoints that are used
send emails to the input email addresses. 
For example, when using the endpoint for creating new registration data, 
an automatic function exists that sends emails, which we don't want
because we use this endpoint for importing existing data.
```sh
grep mail.server.disabled local.cfg
mail.server.disabled=true

docker compose -p d7ws exec dspace /dspace/bin/dspace dsprop -p mail.server.disabled
true
```

### Prerequisites:
1. **Python 3.8+** (tested with 3.8.10 and 3.11)

2. Install CLARIN-DSpace7.*. (PostgreSQL, Solr, DSpace backend)
   2.1. Clone python-api: https://github.com/ufal/dspace-python-api (branch `main`)
   2.2. Clone submodules: `git submodule update --init libs/dspace-rest-python/`

3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r libs/dspace-rest-python/requirements.txt
   ```
   
***
4. Get database dump (old CLARIN-DSpace) and unzip it into `input/dump` directory in `dspace-python-api` project.

5. Prepare `dspace-python-api` project for migration: copy the files used during migration into `input/` directory:
```
> ls -R ./input
input:
dump

input/dump:
clarin-dspace.sql  clarin-utilities.sql

```
**Note:** `input/icon/` contains license icons (PNG files).

6. Copy `assetstore` from dspace5 to dspace7 (for bitstream import). `assetstore` is in the folder where you have installed DSpace `dspace/assetstore`.

7. Create `dspace` database with extension `pgcrypto`.

8. Go to the `dspace/bin` in DSpace 7 installation and run the command `dspace database migrate force` (force because of local types).
**NOTE:** `dspace database migrate force` creates default database data that may not be in the database dump, so after migration, some tables may have more data than the database dump. Data from the database dump that already exists in the database is not migrated.

9. Create an admin by running the command `dspace create-administrator` in the `dspace/bin`

10. Create CLARIN-DSpace5.* databases (dspace, utilities) from dump.
Run `scripts/start.local.dspace.db.bat` or use `scripts/init.dspacedb5.sh` directly with your database.

- Manual import alternative:
  ```sh
  docker compose -p d7ws exec dspacedb createdb -p 5430 --username=dspace --owner=dspace --encoding=UNICODE clarin-dspace
  docker compose -p d7ws exec dspacedb createdb -p 5430 --username=dspace --owner=dspace --encoding=UNICODE clarin-utilities
  cat input/dump/clarin-utilities.sql | docker compose -p d7ws exec -T dspacedb psql -p 5430 --username=dspace clarin-utilities
  cat input/dump/clarin-dspace.sql | docker compose -p d7ws exec -T dspacedb psql -p 5430 --username=dspace clarin-dspace
  ```

***
11. Update `project_settings.py`

## Configuration Options

### Ignore Settings
Configure items to skip during migration in the `"ignore"` section of `project_settings.py`:

- **Missing license icons**: Add license labels to `"missing-icons"` array to ignore missing icon files during license import
  ```python
  "missing-icons": ["Inf", "OSI", "ND"]
  ```

- **Empty persons**: Add person IDs to `"epersons"` array to ignore empty/invalid person records
  ```python  
  "epersons": [
      # ignore - empty person
      198
  ]
  ```

- **Metadata fields**: Add field names to `"fields"` array to ignore specific metadata fields during import
  ```python
  "fields": ['local.bitstream.file', 'local.bitstream.redirectToURL']
  ```

Additional practical notes:
- You can use an internal backend IP for backend connection details.
- With `"testing": True`, the mechanism described in https://github.com/ufal/dspace-migrate/issues/4#issuecomment-3052818633 can be used (if the mentioned file is present), but do not keep this in final production migration runs.
  ```sh
  docker compose -p d7ws exec dspace bash -c "mkdir -p /tmp/asset && pushd /tmp/asset && curl -LJO https://github.com/user-attachments/files/21145749/57024294293009067626820405177604023574.zip && mkdir -p /dspace/assetstore/57/02/42 && zcat 570242* > /dspace/assetstore/57/02/42/57024294293009067626820405177604023574 && popd && rm -rf /tmp/asset"
  ```
- If you use many custom licenses and their text was under `xmlui/page`, you can use the `licenses` mapping to automatically update URLs.

12. Make sure that handle prefixes are configured in the backend configuration (`dspace.cfg`):
   - Set your main handle prefix in `handle.prefix`
   - Add all other handle prefixes to `handle.additional.prefixes`
   - **Note:** The main prefix should NOT be included in `handle.additional.prefixes`
   - **Example:** 
     ```
     handle.prefix = 123456789
     handle.additional.prefixes = 11858, 11234, 11372, 11346, 20.500.12801, 20.500.12800
     ```

- You can list existing prefixes from the old database with:
  ```sql
  select distinct(split_part(handle, '/', 1)) as prefix from handle;
  ```

## Version Date Fields Configuration

**REQUIRED:** Configure version date fields in `project_settings.py` for version migration. This configuration is mandatory and must be explicitly set.

Add the following to your `project_settings.py`:
```python
"version_date_fields": ["dc.date.issued", "dc.date.accessioned", "dc.date.created"]
```

### How it works:
- **Purpose**: When migrating item versions, the system needs a date field to set the version date
- **Fallback mechanism**: Fields are tried in order until one with a value is found
- **Supported formats**: 
  - `"dc.element.qualifier"` (e.g., `"dc.date.issued"`)
  - `"dc.element"` (e.g., `"dc.date"`)
- **Error handling**: If no configured field contains a date value for an item, that item's version migration is skipped with a critical error

### Common configuration examples:
```python
"version_date_fields": ["dc.date.issued", "dc.date.accessioned"]
```

***
13. Import: Run command `cd ./src && python repo_import.py`
- **NOTE:** database must be up to date (`dspace database migrate force` must be called in the `dspace/bin`)
- **NOTE:** dspace server must be running
- Check logs in `__logs` for CRITICAL/ERROR/WARNING and inspect backend `/dspace/log/dspace.log` when encountering 500 errors.

If you need to rerun migration:
- Recreate environment/database and admin as needed:
  ```sh
  docker compose -p d7ws down --volumes
  docker compose --env-file .env -p d7ws -f docker/docker-compose.yml -f docker/docker-compose-rest.yml up -d
  docker compose --env-file .env -p d7ws -f docker/docker-compose.yml -f docker/docker-compose-rest.yml -f docker/cli.yml run --rm dspace-cli create-administrator -e test@test.edu -f Sys -l Admin -p password -c en -o UFAL
  docker compose -p d7ws exec dspace bash -c "mkdir -p /tmp/asset && pushd /tmp/asset && curl -LJO https://github.com/user-attachments/files/21145749/57024294293009067626820405177604023574.zip && mkdir -p /dspace/assetstore/57/02/42 && zcat 570242* > /dspace/assetstore/57/02/42/57024294293009067626820405177604023574 && popd && rm -rf /tmp/asset"
  ```
- Clean migration logs/temp artifacts:
  ```sh
  rm -rf __logs/ src/__temp/ input/tempdbexport_v*
  ```

## Database Connection Improvements

For long-running imports, the system includes automatic connection management:

- **Connection reliability**: TCP keepalive prevents timeouts, automatic reconnection on failures
- **Large dataset handling**: Tables >100k rows processed in 50k row chunks to prevent memory issues  
- **Retry logic**: All operations retry up to 3 times with exponential backoff

### Configuration

Database settings in `src/pump/_db_config.py`:
- `DB_CHUNK_SIZE = 50000` - Rows per chunk for large tables
- `DB_MAX_RETRIES = 3` - Retry attempts on failure
- `DB_CONNECT_TIMEOUT = 30` - Connection timeout in seconds

## !!!Migration Notes:!!!
- The values of table attributes that describe the last modification time of DSpace objects (for example attribute `last_modified` in table `Item`) have a value that represents the time when that object was migrated and not the value from the migrated database dump.
- If you don't have valid and complete data, not all data will be imported.
- Check if license link contains XXX. This is of course unsuitable for production runs!

## Check import consistency

Use `tools/repo_diff` utility, see [README](tools/repo_diff/README.md).

## Testing with Empty Tables

The migration script supports testing functionality with empty tables to verify the import process without actual data. 

### Setup

Before using the `--test` option, you need to create the test JSON file:

1. **Create the test JSON file**: Create a file named `test.json` in the `input/test/` directory with the following content:
   ```json
   null
   ```

2. **Configure the test settings**: The test configuration is set in `src/project_settings.py`:
   ```python
   "input": {
       "test": os.path.join(_this_dir, "../input/test"),
       "test_json_filename": "test.json",
   }
   ```
   
   You can change the `test_json_filename` to use a different filename if needed.

### Usage

To run the migration with empty table testing, use the `--test` option followed by the table names you want to test with empty data.

### Examples

```bash
cd ./src && python repo_import.py --test usermetadatas
```

```bash
cd ./src && python repo_import.py --test usermetadatas resourcepolicies
```

### How it Works

When the `--test` option is specified with table names:
1. Instead of loading actual data from database exports, the system loads the configured test JSON file (default: `test.json`) which contains `null`
2. This simulates empty tables during the import process
3. The migration logic is tested without requiring actual data
4. The test JSON filename can be customized in `project_settings.py` under `"input"["test_json_filename"]`