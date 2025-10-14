import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.registrationdata")


class registrationdatas:
    """
        SQL:
            delete from registrationdata ;
    """
    validate_table = [
        ["registrationdata", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["email"],
        }],
    ]

    def __init__(self, col_rd_str: str):
        self._rd = read_json(col_rd_str) or []
        if not self._rd:
            _logger.info(f"Empty input: [{col_rd_str}].")
        self._imported = {
            "rd": 0,
            "missing_email": 0,
        }

        if not self._rd:
            _logger.info(f"Empty input: [{col_rd_str}].")
            return

    def __len__(self):
        return len(self._rd or {})

    @property
    def imported(self):
        return self._imported['rd']

    @time_method
    def import_to(self, dspace):
        expected = len(self)
        log_key = "registrationdata"
        log_before_import(log_key, expected)

        for rd in progress_bar(self._rd):
            email = rd['email']
            if email == '':
                _logger.debug(f"Registration data [{rd}] ignored because of empty email.")
                self._imported["missing_email"] += 1
                continue
            data = {'email': email}
            params = {'accountRequestType': 'register'}
            try:
                resp = dspace.put_registrationdata(params, data)
                self._imported["rd"] += 1
            except Exception as e:
                _logger.error(
                    f'put_registrationdata [{rd["email"]}]: failed. Exception: [{str(e)}]')

        log_after_import(f'{log_key} missing_email:[{self._imported["missing_email"]}]',
                         expected, self.imported + self._imported["missing_email"])

    # =============

    def serialize(self, file_str: str):
        data = {
            "rd": self._rd,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._rd = data["rd"]
        self._imported = data["imported"]
