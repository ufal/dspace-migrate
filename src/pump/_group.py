import re
import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.groups")


def _epersongroup_process(repo, v5data: list, v7data: list):
    """
        v5: ['COLLECTION_17_DEFAULT_READ', 'COLLECTION_20_WORKFLOW_STEP_2']
        v7: ['COLLECTION_f3c65f29-355e-4ca2-a05b-f3e30883e09f_BITSTREAM_DEFAULT_READ']
    """
    rec = re.compile("(COLLECTION|COMMUNITY)_(\d+)_(.*)")
    v5data_new = []
    for val in v5data:
        m = rec.match(val)
        if m is None:
            v5data_new.append(val)
            continue
        c, c_id, role = m.groups()
        uuid = repo.collections.uuid(c_id) if c == "COLLECTION" \
            else repo.communities.uuid(c_id)
        if role == "WORKFLOW_STEP_2":
            role = "WORKFLOW_ROLE_editor"
        if role == "DEFAULT_READ":
            v5data_new.append(f"{m.group(1)}_{uuid}_BITSTREAM_DEFAULT_READ")
            v5data_new.append(f"{m.group(1)}_{uuid}_ITEM_DEFAULT_READ")
        else:
            v5data_new.append(f"{m.group(1)}_{uuid}_{role}")
    _logger.info(
        f"Changing v5 groups to uuid version and adding bitstream/item reads: {len(v5data)} -> {len(v5data_new)}")

    return v5data_new, v7data


class groups:

    validate_table = [
        ["epersongroup", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["eperson_group_id"],
        }],

        ["epersongroup", {
            "sql": {
                "5": "select metadatavalue.text_value from epersongroup inner join metadatavalue ON metadatavalue.resource_id=epersongroup.eperson_group_id and metadatavalue.resource_type_id=6",
                "7": "select name from epersongroup",
                "compare": 0,
                "process": _epersongroup_process,
            }
        }],

        ["group2group", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["parent_id", "child_id"],
        }],
        ["epersongroup2eperson", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["eperson_group_id", "eperson_id"],
        }],
    ]

    TYPE = 6
    DEF_GID_ANON = "0"
    DEF_GID_ADMIN = "1"

    def __init__(self, eperson_file_str: str, g2g_file_str: str):
        self._eperson = read_json(eperson_file_str)
        self._g2g = read_json(g2g_file_str)
        self._imported = {
            "eperson": 0,
            "group": 0,
            "g2g": 0,
            "default_groups": 0,
            "coll_groups": 0,
        }

        # created during import

        # all imported group
        self._id2uuid = {}

        if len(self._eperson) == 0:
            _logger.info(f"Empty input collections: [{eperson_file_str}].")

        if len(self._g2g) == 0:
            _logger.info(f"Empty input collections: [{g2g_file_str}].")

    @property
    def imported_eperson(self):
        return self._imported['eperson']

    @property
    def imported_g2g(self):
        return self._imported['g2g']

    @property
    def anonymous(self):
        return self.uuid(groups.DEF_GID_ANON)

    @property
    def admins(self):
        return self.uuid(groups.DEF_GID_ADMIN)

    def from_rest(self, dspace, ignore_other=False):
        """
            Load Administrator and Anonymous groups into dict.
            This data already exists in database.
            Remember its id.
        """
        res = dspace.fetch_existing_epersongroups()
        if res is None:
            return self

        other_groups = []
        for group in res:
            if group['name'] == 'Anonymous':
                self._id2uuid[groups.DEF_GID_ANON] = [group['id']]
                continue

            if group['name'] == 'Administrator':
                self._id2uuid[groups.DEF_GID_ADMIN] = [group['id']]
                continue

            other_groups.append(group)
        _logger.debug(
            f"Loaded groups [{self._id2uuid}], other groups:[{len(other_groups)}]")
        return self

    def uuid(self, gid: int):
        assert isinstance(list(self._id2uuid.keys())[0], str)
        return self._id2uuid.get(str(gid), None)

    @time_method
    def import_to(self, dspace, metadatas, coll_groups, comm_groups):
        # Do not import groups which are already imported
        self._id2uuid.update(coll_groups)
        self._id2uuid.update(comm_groups)
        self._import_eperson(dspace, metadatas)
        self._import_group2group(dspace)

    def _import_eperson(self, dspace, metadatas):
        """
            Import data into database.
            Mapped tables: epersongroup
        """
        expected = len(self._eperson)
        log_key = "epersongroup"
        log_before_import(log_key, expected)

        grps = []

        for eg in progress_bar(self._eperson):
            g_id = eg['eperson_group_id']

            # group Administrator and Anonymous already exist
            # group is created with dspace object too
            if str(g_id) in (groups.DEF_GID_ADMIN, groups.DEF_GID_ANON):
                self._imported["default_groups"] += 1
                continue

            g_uuid = self.uuid(g_id)
            if g_uuid is not None:
                # TODO(jm) what is this?
                self._imported["coll_groups"] += 1
                continue

            # get group metadata
            g_meta = metadatas.value(groups.TYPE, g_id)
            if 'dc.title' not in g_meta:
                _logger.error(f'Metadata for group [{g_id}] does not contain dc.title!')
                continue

            name = g_meta['dc.title'][0]['value']
            del g_meta['dc.title']

            # the group_metadata contains the name of the group
            data = {'name': name, 'metadata': g_meta}
            grps.append(name)
            try:
                # continue
                resp = dspace.put_eperson_group({}, data)
                self._id2uuid[str(g_id)] = [resp['id']]
                self._imported["eperson"] += 1
            except Exception as e:
                _logger.error(f'put_eperson_group: [{g_id}] failed [{str(e)}]')

        # sql_del = "delete from epersongroup where name='" + "' or name='".join(grps) + "' ;"
        # _logger.info(sql_del)

        log_after_import(f'{log_key} [known existing:{self._imported["default_groups"]}]',
                         expected, self.imported_eperson + self._imported["default_groups"])

    def _import_group2group(self, dspace):
        """
            Import data into database.
            Mapped tables: group2group
        """
        expected = len(self._g2g)
        log_key = "epersons g2g (could have children)"
        log_before_import(log_key, expected)

        for g2g in progress_bar(self._g2g):
            parent_a = self.uuid(g2g['parent_id'])
            child_a = self.uuid(g2g['child_id'])
            if parent_a is None or child_a is None:
                _logger.critical(
                    f"Invalid uuid for [{g2g['parent_id']}] or [{g2g['child_id']}]")
                continue

            for parent in parent_a:
                for child in child_a:
                    try:
                        dspace.put_group2group(parent, child)
                        # TODO Update statistics when the collection has more group relations.
                        self._imported["g2g"] += 1
                    except Exception as e:
                        _logger.error(
                            f'put_group2group: [{parent}][{child}] failed [{str(e)}]')

        log_after_import(log_key, expected, self.imported_g2g)

    # =============

    def serialize(self, file_str: str):
        data = {
            "eperson": self._eperson,
            "id2uuid": self._id2uuid,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._eperson = data["eperson"]
        self._id2uuid = data["id2uuid"]
        self._imported = data["imported"]
