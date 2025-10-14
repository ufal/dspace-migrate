import logging
from ._utils import read_json, time_method, serialize, deserialize, log_before_import, log_after_import

_logger = logging.getLogger("pump.community")


class communities:
    """
        SQL:
            delete from community2community ; delete from community2collection ; delete from community ;
    """
    validate_table = [
        ["community", {
        }],
        ["community2community", {
        }],
    ]

    TYPE = 4

    def __init__(self, com_file_str: str, com2com_file_str: str):
        self._com = read_json(com_file_str) or []
        self._com2com = read_json(com2com_file_str) or []
        self._imported = {
            "com": 0,
            "group": 0,
            "com2com": 0,
        }

        self._id2uuid = {}

        self._logos = {}
        self._groups = {}

        if not self._com:
            _logger.info(f"Empty input: [{com_file_str}].")
        if not self._com2com:
            _logger.info(f"Empty input: [{com2com_file_str}].")

    def __len__(self):
        return len(self._com or {})

    @property
    def logos(self):
        return self._logos

    @property
    def imported_coms(self):
        return self._imported['com']

    @property
    def imported_com2coms(self):
        return self._imported['com2com']

    @property
    def imported_groups(self):
        return self._groups

    def uuid(self, com_id: int):
        assert isinstance(list(self._id2uuid.keys() or [""])[0], str)
        return self._id2uuid.get(str(com_id), None)

    @time_method
    def import_to(self, dspace, handles, metadata):
        """
            Import data into database.
            Mapped tables: community, community2community, metadatavalue, handle
        """
        if len(self) == 0:
            _logger.info("Community JSON is empty.")
            return

        expected = len(self)
        log_key = "communities"
        log_before_import(log_key, expected)

        parents = {}
        childs = {}
        for comm2comm in (self._com2com or []):
            parent_id = comm2comm['parent_comm_id']
            child_id = comm2comm['child_comm_id']
            parents.setdefault(parent_id, []).append(child_id)
            childs.setdefault(child_id, []).append(parent_id)

        for arr in childs.values():
            if len(arr) != 1:
                _logger.critical(f"Strange child array: [{arr}]")

        coms = self._com.copy()

        counter = 0

        i = 0
        while coms:
            counter += 1

            if counter > 200:
                _logger.critical(
                    "Very likely in the process of infinite loop because importing to existing db.")
                break

            data = {}
            # process community only when:
            # comm is not parent and child
            # comm is parent and not child
            # parent comm exists
            # else process it later
            com = coms[i]
            com_id = com['community_id']

            not_child = com_id not in childs
            not_child_nor_parent = (com_id not in parents and not_child)
            com_child = childs[com_id][0] if com_id in childs else None
            com_child_uuid = self.uuid(com_child)
            if not_child_nor_parent or not_child or com_child_uuid is not None:

                # resource_type_id for community is 4
                handle_com = handles.get(communities.TYPE, com_id)
                if handle_com is None:
                    _logger.critical(f"Cannot find handle for com [{com_id}]")
                    continue

                data['handle'] = handle_com

                metadata_com = metadata.value(communities.TYPE, com_id)

                if metadata_com:
                    data['metadata'] = metadata_com

                # create community
                parent_d = None
                if com_id in childs:
                    parent_d = {'parent': self.uuid(com_child)}

                try:
                    new_com_id = dspace.put_community(parent_d, data)
                    # error
                    if new_com_id is None:
                        i += 1
                        if i == len(coms):
                            i = 0
                        continue
                    # make sure the indices are str
                    self._id2uuid[str(com_id)] = new_com_id['id']
                    self._imported["com"] += 1
                except Exception as e:
                    _logger.error(
                        f'put_community: [{com_id}] failed. Exception: [{str(e)}]')
                    continue

                # add to community2logo, if community has logo
                if com['logo_bitstream_id'] is not None:
                    self._logos[str(com_id)] = com["logo_bitstream_id"]

                # create admingroup
                if com['admin'] is not None:
                    try:
                        resp = dspace.put_community_admin_group(new_com_id['id'])
                        self._groups[str(com['admin'])] = [resp['id']]
                        self._imported["group"] += 1
                    except Exception as e:
                        _logger.error(
                            f'put_community_admin_group: [{new_com_id["id"]}] failed. Exception: [{str(e)}]')
                del coms[i]
            else:
                i += 1

            if i == len(coms):
                i = 0

        log_after_import(log_key, expected, self.imported_coms)

    # =============

    def serialize(self, file_str: str):
        data = {
            "com_created": self._id2uuid,
            "logos": self._logos,
            "groups": self._groups,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._id2uuid = data["com_created"]
        self._logos = data["logos"]
        self._groups = data["groups"]
        self._imported = data["imported"]
