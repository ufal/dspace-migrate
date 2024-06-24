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

    "db_tul": {
        "name": "tul",
        "host": "localhost",
        "user": "postgres",
        "password": "dspace",
        "port": 5432,
    }
}
