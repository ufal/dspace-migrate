import logging

_logger = logging.getLogger("pump.item")


class sequences:
    def __init__(self):
        pass

    def migrate(self, env, db7, db5_dspace, db5_utilities):
        """
            Migrate sequences from clarin 5 database to clarin 7 database.
        """
        _logger.info("Sequence migration started.")

        # get all sequences from clarin-dspace database
        dspace5_seqs = db5_dspace.fetch_all("SELECT * FROM information_schema.sequences")

        key_db_idx = 0
        key_name_idx = 2

        # Do not import `clarin-utilities` sequences because of this issue:
        # https://github.com/dataquest-dev/dspace-python-api/issues/114
        # utilities5_seq = db5_utilities.fetchall("SELECT * FROM information_schema.sequences")

        db7_seqs = db7.fetch_all("SELECT * FROM information_schema.sequences")
        db7_seqs_names = [seq[key_name_idx] for seq in db7_seqs]

        # check if all sequences from clarin 5 are already present in clarin 7
        for dspace5_seq in dspace5_seqs:

            dspace5_seq_db = dspace5_seq[key_db_idx]
            dspace5_seq_name = dspace5_seq[key_name_idx]

            if dspace5_seq_name not in db7_seqs_names:
                continue

            if dspace5_seq_name in ["versionhistory_seq", "versionitem_seq"]:
                continue

            # use cursor according to database to which sequence belongs
            if dspace5_seq_db == "clarin-dspace":
                db = db5_dspace
            else:
                db = db5_utilities

            # get current value of given sequence
            seq_val = db.fetch_one(f"SELECT last_value FROM {dspace5_seq_name}")

            # set value of the sequence in clarin 7 dspace database
            db7.exe_sql(f"SELECT setval('{dspace5_seq_name}', {seq_val})")

            # check value of the sequence in clarin7 database
            db7_seq_val = db7.fetch_one(f"SELECT last_value FROM {dspace5_seq_name}")
            if seq_val != db7_seq_val:
                _logger.error(
                    f"{dspace5_seq_name}   --> [{seq_val}] does not match expected [{db7_seq_val}].")

        _logger.info("Sequence migration is complete.")
