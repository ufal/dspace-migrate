import os
from datetime import datetime
_this_dir = os.path.dirname(os.path.abspath(__file__))
ts = datetime.now().strftime("%Y_%m_%d__%H.%M.%S")

settings = {
    "log_file": os.path.join(_this_dir, "../__logs", f"{ts}.txt"),

    "resume_dir": "__temp/resume/",

    "backend": {
        "endpoint": "http://dev-5.pc:85/server/api",  # without trailing slash
        "user": "test@test.edu",
        "password": "admin",
        "authentication": True,
        "testing": True,
    },

    "ignore": {
        "missing-icons": ["PUB", "RES", "ReD", "Inf"],
        "epersons": [
            # ignore - empty person
            198
        ],
        # clarin-dspace=# select * from metadatafieldregistry  where metadata_field_id=176 ;
        #  metadata_field_id | metadata_schema_id |   element   |   qualifier   |               scope_note
        # -------------------+--------------------+-------------+---------------+----------------------------------------
        #                176 |                  3 |  bitstream  |     file      | Files inside a bitstream if an archive
        # clarin-dspace=# select * from metadatafieldregistry  where metadata_field_id=178 ;
        # -------------------+--------------------+-------------+---------------+----------------------------------------
        #                178 |                  3 |  bitstream  | redirectToURL |    Get the bitstream from this URL.
        "fields": ['local.bitstream.file', 'local.bitstream.redirectToURL'],
    },

    "replaced": {
        # fields which will be replaced in metadata
        # if we want to ignore the metadata field, we must replace field when metadata is imported!
        #  metadata_field_id | metadata_schema_id |   element   |   qualifier   |               scope_note
        # -------------------+--------------------+-------------+---------------+----------------------------------------
        #                98  |                  3 | hasMetadata |     null      |       Indicates uploaded cmdi file
        "fields": ['local.hasMetadata'],
    },

    "db_dspace_7": {
        # CLARIN-DSpace 7 database
        "name": "dspace",
        "host": "localhost",
        # careful - NON standard port
        "port": 5435,
        "user": "dspace",
        "password": "dspace",
    },

    "db_dspace_5": {
        "name": "clarin-dspace",
        "host": "localhost",
        "user": "postgres",
        "password": "dspace",
        "port": 5432,
    },

    "db_utilities_5": {
        "name": "clarin-utilities",
        "host": "localhost",
        "user": "postgres",
        "password": "dspace",
        "port": 5432,
    },

    "input": {
        "tempdbexport_v5": os.path.join(_this_dir, "../input/tempdbexport_v5"),
        "tempdbexport_v7": os.path.join(_this_dir, "../input/tempdbexport_v7"),
        "icondir": os.path.join(_this_dir, "../assets/icon/"),
        "test": os.path.join(_this_dir, "../input/test"),
        "test_json_filename": "test.json",
    },

    "licenses": {
        "to_replace_def_url": "https://lindat.mff.cuni.cz/repository/xmlui/page/",
        # TODO(jm): replace with correct url
        "replace_with_def_url": "http://dev-5.pc:85/XXX/static/",
    }
}
