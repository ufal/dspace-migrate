import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.resourcepolicy")


class resourcepolicies:
    """
        SQL:
            delete from resourcepolicy ;
    """

    test_table = [
        {
            "name": "res_policy_bitstream_embargo",
            "left": ["sql", "db7", "one", "select count(*) from resourcepolicy "
                                          "where action_id = 0 and epersongroup_id in "
                                          "(select uuid from epersongroup where name = 'Anonymous') "
                                          "and start_date is not null and resource_type_id = 0"],
            "right": ["sql", "dspace5", "one", "select count(*) from resourcepolicy where action_id = 0 and "
                                               "epersongroup_id in (select resource_id from metadatavalue where "
                                               "text_value = 'Anonymous' and resource_type_id = 6) "
                                               "and start_date is not null and resource_type_id = 0"]
        },
        {
            "name": "res_policy_item_embargo",
            "left": ["sql", "db7", "one", "select count(*) from resourcepolicy "
                                          "where action_id = 0 and epersongroup_id in "
                                          "(select uuid from epersongroup where name = 'Anonymous') "
                                          "and start_date is not null and resource_type_id = 2"],
            "right": ["sql", "dspace5", "one", "select count(*) from resourcepolicy where action_id = 0 and "
                                               "epersongroup_id in (select resource_id from metadatavalue where "
                                               "text_value = 'Anonymous' and resource_type_id = 6) "
                                               "and start_date is not null and resource_type_id = 2"]
        }
    ]

    def __init__(self, resourcepolicy_file_str: str):
        self._respol = read_json(resourcepolicy_file_str) or []

        if not self._respol:
            _logger.info(f"Empty input: [{resourcepolicy_file_str}].")
        self._id2uuid = {}
        self._imported = {
            "respol": 0,
        }

    DEFAULT_BITSTREAM_READ = "DEFAULT_BITSTREAM_READ"

    def __len__(self):
        return len(self._respol or {})

    def uuid(self, b_id: int):
        assert isinstance(list(self._id2uuid.keys() or [""])[0], str)
        return self._id2uuid[str(b_id)]

    @property
    def imported(self):
        return self._imported['respol']

    @time_method
    def import_to(self, env, dspace, repo):
        expected = len(self)
        log_key = "resourcepolicies"
        log_before_import(log_key, expected)

        dspace_actions = env["dspace"]["actions"]
        failed = 0

        for res_policy in progress_bar(self._respol):
            res_id = res_policy['resource_id']
            res_type_id = res_policy['resource_type_id']
            # If resourcepolicy belongs to some Item or Bundle, check if that Item/Bundle wasn't removed from the table.
            # Somehow, the resourcepolicy table could still have a reference to deleted items/bundles.
            if res_type_id in [repo.items.TYPE, repo.bundles.TYPE]:
                if repo.uuid(res_type_id, res_id) is None:
                    _logger.info(
                        f"Cannot import resource policy [{res_id}] for the record with type [{res_type_id}] that has already been deleted.")
                    continue

            res_uuid = repo.uuid(res_type_id, res_id)
            if res_uuid is None:
                _logger.critical(
                    f"Cannot find uuid for [{res_type_id}] [{res_id}] [{str(res_policy)}]")
                continue
            params = {}
            if res_uuid is not None:
                params['resource'] = res_uuid
            # in resource there is action as id, but we need action as text
            actionId = res_policy['action_id']

            # control, if action is entered correctly
            if not dspace_actions:
                _logger.error(
                    "dspace_actions is None or empty. Cannot validate actionId.")
                failed += 1
                continue
            if actionId is None or actionId < 0 or actionId >= len(dspace_actions):
                _logger.error(
                    f"Invalid actionId: {actionId}. Must be in range 0 to {len(dspace_actions) - 1}")
                failed += 1
                continue

            # create object for request
            data = {
                'action': dspace_actions[actionId],
                'startDate': res_policy['start_date'],
                'endDate': res_policy['end_date'],
                'name': res_policy['rpname'],
                'policyType': res_policy['rptype'],
                'description': res_policy['rpdescription']
            }

            # resource policy has defined eperson or group, not the both
            # get eperson if it is not none
            if res_policy['eperson_id'] is not None:
                params['eperson'] = repo.epersons.uuid(res_policy['eperson_id'])
                try:
                    resp = dspace.put_resourcepolicy(params, data)
                    self._imported["respol"] += 1
                except Exception as e:
                    _logger.error(
                        f'put_resourcepolicy: [{res_policy["policy_id"]}] failed [{str(e)}]')
                continue

            # get group if it is not none
            eg_id = res_policy['epersongroup_id']
            if eg_id is not None:
                # groups created with coll and comm are already in the group
                group_list = repo.groups.uuid(eg_id)
                if not group_list:
                    continue
                if len(group_list) > 1:
                    if len(group_list) != 2:
                        raise RuntimeError(
                            f'Unexpected size of mapped groups to group [{eg_id}]: {len(group_list)}. '
                            f'Expected size: 2.')
                    group_types = repo.collections.groups_uuid2type
                    # Determine the target type based on the action
                    target_type = (
                        repo.collections.BITSTREAM
                        if dspace_actions[actionId] == resourcepolicies.DEFAULT_BITSTREAM_READ
                        else repo.collections.ITEM
                    )
                    # Filter group_list to find the appropriate group based on type using list comprehension
                    group_type_list = [
                        group for group in group_list
                        if group in group_types and group_types[group] == target_type
                    ]

                    if len(group_type_list) != 1:
                        raise RuntimeError(
                            f'Unexpected size of filtered groups for group [{eg_id}] '
                            f'of type [{target_type}]: {len(group_type_list)}. Expected size: 1.'
                        )

                    group_list = group_type_list

                imported_groups = 0
                for group in group_list:
                    params['group'] = group
                    try:
                        resp = dspace.put_resourcepolicy(params, data)
                        imported_groups += 1
                    except Exception as e:
                        _logger.error(
                            f'put_resourcepolicy: [{res_policy["policy_id"]}] failed [{str(e)}]')
                if imported_groups > 0:
                    self._imported["respol"] += 1
                continue

            _logger.error(f"Cannot import resource policy {res_policy['policy_id']} "
                          f"because neither eperson nor group is defined")
            failed += 1

        log_after_import(f"{log_key}, failed:[{failed}]", expected, self.imported)

    # =============

    def serialize(self, file_str: str):
        data = {
            "respol": self._respol,
            "id2uuid": self._id2uuid,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._respol = data["respol"]
        self._id2uuid = data["id2uuid"]
        self._imported = data["imported"]
