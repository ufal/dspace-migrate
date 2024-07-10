import os
from datetime import datetime
_this_dir = os.path.dirname(os.path.abspath(__file__))
ts = datetime.now().strftime("%Y_%m_%d__%H.%M.%S")

settings = {
    "log_file": os.path.join(_this_dir, "../__logs", f"{ts}.txt"),

    "resume_dir": "__temp/resume/",

    "backend": {
        "endpoint": "http://dev-5.pc:85/server/api/",
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
        "datadir": os.path.join(_this_dir, "../input/data"),
        "icondir": os.path.join(_this_dir, "../input/icon"),
    },

    "licenses": {
        "to_replace_def_url": "https://lindat.mff.cuni.cz/repository/xmlui/page/",
        # TODO(jm): replace with correct url
        "replace_with_def_url": "http://dev-5.pc:85/XXX/static/",
    }
}
