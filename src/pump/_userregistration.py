import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.userregistration")


class userregistrations:
    validate_table = [
        # ["userregistration", {
        #     # do not use compare because of email field (GDPR)
        #     "compare": ["email", "netid"],
        # }],
    ]

    def __init__(self, ur_file_str: str):
        self._ur = read_json(ur_file_str) or []
        self._imported = {
            "users": 0,
        }

        self._id2uuid = {}

        if not self._ur:
            _logger.info(f"Empty input: [{ur_file_str}].")
            return

    def __len__(self):
        return len(self._ur or {})

    def uuid(self, e_id: int):
        assert isinstance(list(self._id2uuid.keys() or [""])[0], str)
        return self._id2uuid[str(e_id)]

    @property
    def imported(self):
        return self._imported['users']

    @time_method
    def import_to(self, dspace, epersons):
        """
            Import data into database.
            Mapped tables: user_registration
        """
        expected = len(self)
        log_key = "userregistration"
        log_before_import(log_key, expected)

        for ur in progress_bar(self._ur):
            data = {
                'email': ur['email'],
                'organization': ur['organization'],
                'confirmation': ur['confirmation']
            }
            e_id = ur['eperson_id']
            e_id_by_email = epersons.by_email(ur['email'])
            data['ePersonID'] = epersons.uuid(
                e_id_by_email) if e_id_by_email is not None else None
            try:
                resp = dspace.put_userregistration(data)
                self._id2uuid[str(e_id)] = resp['id']
                self._imported['users'] += 1
            except Exception as e:
                _logger.error(f'put_userregistration: [{e_id}] failed [{str(e)}]')

        log_after_import(log_key, expected, self.imported)

    # =============

    def serialize(self, file_str: str):
        data = {
            "ur": self._ur,
            "id2uuid": self._id2uuid,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._ur = data["ur"]
        self._id2uuid = data["id2uuid"]
        self._imported = data["imported"]
