import logging
import threading
from ._utils import read_json, time_method, serialize, deserialize, log_before_import, log_after_import, run_tasks

_logger = logging.getLogger("pump.resourcepolicy")


class resourcepolicies:
    """
        SQL:
            delete from resourcepolicy ;
    """

    validate_table = [
        ["resourcepolicy", {
            "sql": {
                "5": "select count(*) from resourcepolicy where action_id = 0 and "
                     "epersongroup_id in (select resource_id from metadatavalue where "
                     "text_value = 'Anonymous' and resource_type_id = 6) "
                     "and start_date is not null and resource_type_id = 0",
                "7": "select count(*) from resourcepolicy "
                     "where action_id = 0 and epersongroup_id in "
                     "(select uuid from epersongroup where name = 'Anonymous') "
                     "and start_date is not null and resource_type_id = 0",
                "compare": 0,
            }
        }],
        ["resourcepolicy", {
            "sql": {
                "5": "select count(*) from resourcepolicy where action_id = 0 and "
                     "epersongroup_id in (select resource_id from metadatavalue where "
                     "text_value = 'Anonymous' and resource_type_id = 6) "
                     "and start_date is not null and resource_type_id = 2",
                "7": "select count(*) from resourcepolicy "
                     "where action_id = 0 and epersongroup_id in "
                     "(select uuid from epersongroup where name = 'Anonymous') "
                     "and start_date is not null and resource_type_id = 2",
                "compare": 0,
            }
        }],
    ]

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
        backend = env.get("backend", {})
        workers = max(1, int(backend.get("import_workers", 1) or 1))
        failed = 0
        skipped_missing_bitstream = 0
        skipped_deleted_target = 0

        if workers > 1 and expected > 1:
            _logger.info(
                f"Parallel resourcepolicy import enabled with workers:[{workers}]")

        local = threading.local()

        def worker(res_policy):
            res_id = res_policy['resource_id']
            res_type_id = res_policy['resource_type_id']
            # If resourcepolicy belongs to some Item or Bundle, check if that Item/Bundle wasn't removed from the table.
            # Somehow, the resourcepolicy table could still have a reference to deleted items/bundles.
            if res_type_id in [repo.items.TYPE, repo.bundles.TYPE]:
                if repo.uuid(res_type_id, res_id) is None:
                    return {
                        "imported": False,
                        "failed": 0,
                        "skipped_deleted_target": 1,
                        "skipped_missing_bitstream": 0,
                    }

            res_uuid = repo.uuid(res_type_id, res_id)
            if res_type_id == repo.bitstreams.TYPE and res_uuid is None:
                return {
                    "imported": False,
                    "failed": 0,
                    "skipped_missing_bitstream": 1,
                }
            if res_uuid is None:
                return {
                    "imported": False,
                    "failed": 0,
                    "skipped_missing_bitstream": 0,
                    "log_critical": f"Cannot find uuid for [{res_type_id}] [{res_id}] [{str(res_policy)}]",
                }
            params = {'resource': res_uuid}
            # in resource there is action as id, but we need action as text
            actionId = res_policy['action_id']

            # control, if action is entered correctly
            if not dspace_actions:
                return {
                    "imported": False,
                    "failed": 1,
                    "skipped_missing_bitstream": 0,
                    "log_error": "dspace_actions is None or empty. Cannot validate actionId.",
                }
            if actionId is None or actionId < 0 or actionId >= len(dspace_actions):
                return {
                    "imported": False,
                    "failed": 1,
                    "skipped_missing_bitstream": 0,
                    "log_error": f"Invalid actionId: {actionId}. Must be in range 0 to {len(dspace_actions) - 1}",
                }

            # create object for request
            data = {
                'action': dspace_actions[actionId],
                'startDate': res_policy['start_date'],
                'endDate': res_policy['end_date'],
                'name': res_policy['rpname'],
                'policyType': res_policy['rptype'],
                'description': res_policy['rpdescription']
            }

            # resource policy has defined eperson or group, not both
            # get eperson if it is not none
            client = dspace
            if workers > 1:
                client = getattr(local, "dspace", None)
                if client is None:
                    local.dspace = dspace.spawn_worker_client()
                    client = local.dspace

            if res_policy['eperson_id'] is not None:
                params['eperson'] = repo.epersons.uuid(res_policy['eperson_id'])
                try:
                    client.put_resourcepolicy(params, data)
                    return {
                        "imported": True,
                        "failed": 0,
                        "skipped_missing_bitstream": 0,
                    }
                except Exception as e:
                    return {
                        "imported": False,
                        "failed": 0,
                        "skipped_missing_bitstream": 0,
                        "log_error": f'put_resourcepolicy: [{res_policy["policy_id"]}] failed [{str(e)}]'
                    }

            # get group if it is not none
            eg_id = res_policy['epersongroup_id']
            if eg_id is not None:
                # groups created with coll and comm are already in the group
                group_list = repo.groups.uuid(eg_id)
                if not group_list:
                    return {
                        "imported": False,
                        "failed": 0,
                        "skipped_missing_bitstream": 0,
                    }
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
                        client.put_resourcepolicy(params, data)
                        imported_groups += 1
                    except Exception as e:
                        return {
                            "imported": False,
                            "failed": 0,
                            "skipped_missing_bitstream": 0,
                            "log_error": f'put_resourcepolicy: [{res_policy["policy_id"]}] failed [{str(e)}]'
                        }

                return {
                    "imported": imported_groups > 0,
                    "failed": 0,
                    "skipped_missing_bitstream": 0,
                }

            return {
                "imported": False,
                "failed": 1,
                "skipped_deleted_target": 0,
                "skipped_missing_bitstream": 0,
                "log_error": f"Cannot import resource policy {res_policy['policy_id']} because neither eperson nor group is defined",
            }

        progress_stats = {
            "skipped_deleted_target": 0,
            "skipped_missing_bitstream": 0,
        }

        def on_result(_task, result, _err, iterator):
            if result is not None:
                progress_stats["skipped_deleted_target"] += int(
                    result.get("skipped_deleted_target", 0) or 0)
                progress_stats["skipped_missing_bitstream"] += int(
                    result.get("skipped_missing_bitstream", 0) or 0)

            if hasattr(iterator, "set_postfix"):
                postfix = {
                    "skipped_deleted": progress_stats["skipped_deleted_target"],
                }
                if progress_stats["skipped_missing_bitstream"] > 0:
                    postfix["skipped_missing_bitstream"] = progress_stats["skipped_missing_bitstream"]
                iterator.set_postfix(postfix, refresh=False)

        for i, (task, result, err) in enumerate(run_tasks(
            self._respol,
            worker,
            workers=workers,
            desc=f"Importing resourcepolicies ({workers} workers)",
            on_result=on_result,
        )):
            if err is not None:
                raise err

            if result.get("log_info"):
                _logger.info(result["log_info"])
            if result.get("log_error"):
                _logger.error(result["log_error"])
            if result.get("log_critical"):
                _logger.critical(result["log_critical"])

            if result.get("imported"):
                self._imported["respol"] += 1

            failed += int(result.get("failed", 0) or 0)
            skipped_deleted_target += int(
                result.get("skipped_deleted_target", 0) or 0)
            skipped_missing_bitstream += int(
                result.get("skipped_missing_bitstream", 0) or 0)

        extra = f"{log_key}, failed:[{failed}]"
        if skipped_deleted_target > 0:
            extra = f"{extra}, skipped_deleted_target:[{skipped_deleted_target}]"
            _logger.info(
                "Skipped resource policies targeting deleted Item/Bundle records "
                f"(count:[{skipped_deleted_target}])."
            )
        if skipped_missing_bitstream > 0:
            extra = f"{extra}, skipped_missing_bitstream:[{skipped_missing_bitstream}]"
        log_after_import(extra, expected, self.imported)

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
