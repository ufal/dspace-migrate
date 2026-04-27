import sys
import time
import os
import argparse
import logging
import gc
import tracemalloc

import settings
import project_settings
from utils import init_logging, update_settings, exists_key, set_key

_logger = logging.getLogger()
_mem_logger = logging.getLogger("memory")

# env settings, update with project_settings
env = update_settings(settings.env, project_settings.settings)
init_logging(_logger, env["log_file"], env.get("memory_log_file"))

import dspace  # noqa
import pump  # noqa


def verify_disabled_mailserver():
    """
        Is the email server really off?
    """
    email_s_off = input("Please make sure your email server is turned off. "
                        "Otherwise, an unbearable amount of emails will be sent. "
                        "Is your EMAIL SERVER really OFF? (Y/N)")
    if email_s_off.lower() not in ("y", "yes"):
        _logger.critical("The email server is not off.")
        sys.exit()


def confirm_important_configuration(env: dict):
    backend = env.get("backend", {})
    ignore = env.get("ignore", {})
    licenses = env.get("licenses", {})

    epersons = ignore.get("epersons", []) or []
    rows = [
        (
            "backend.import_workers",
            backend.get("import_workers", 1),
            "parallel workers for item/bundle/resourcepolicy import",
        ),
        (
            "backend.ignore_deleted_bitstreams",
            backend.get("ignore_deleted_bitstreams", False),
            "True = skip deleted bitstreams",
        ),
        (
            "backend.testing",
            backend.get("testing", False),
            "True = testing-only fallback behavior",
        ),
    ]

    if epersons:
        rows.append(
            (
                "ignore.epersons",
                epersons,
                "eperson IDs skipped during import",
            )
        )

    rows.extend([
        (
            "licenses.to_replace_def_url",
            licenses.get("to_replace_def_url", ""),
            "source URL prefix to be replaced",
        ),
        (
            "licenses.replace_with_def_url",
            licenses.get("replace_with_def_url", ""),
            "target URL prefix used for replacement",
        ),
    ])

    key_w = max(len(key) for key, _, _ in rows)
    val_w = max(len(str(value)) for _, value, _ in rows)

    _logger.info("=" * 108)
    _logger.info("IMPORTANT CONFIGURATION (please review before import)")
    _logger.info("-" * 108)
    for key, value, comment in rows:
        _logger.info(
            f"{key:<{key_w}} : {str(value):<{val_w}}    # {comment}"
        )
    _logger.info("=" * 108)

    try:
        _logger.info(
            "Press ENTER to continue if this configuration is OK (Ctrl+C to abort)...")
        input()
    except EOFError:
        _logger.warning(
            "No interactive input available; continuing without confirmation keypress.")


def deserialize(resume: bool, obj, cache_file: str) -> bool:
    """
        If cache file exists, deserialize it and return True.
    """
    if not resume:
        return False

    if not os.path.exists(cache_file):
        return False
    obj.deserialize(cache_file)
    return True


def str2bool(value):
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("1", "true", "t", "yes", "y", "on"):
        return True
    if v in ("0", "false", "f", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _rss_mb() -> float:
    try:
        import psutil
        rss = psutil.Process(os.getpid()).memory_info().rss
        if rss <= 0:
            return -1.0
        return rss / (1024 * 1024)
    except Exception:
        return -1.0


def _format_elapsed_human(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, rem = divmod(total_seconds, 24 * 60 * 60)
    hours, rem = divmod(rem, 60 * 60)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def log_checkpoint(label: str, since_ts: float, total_since_ts: float = None) -> float:
    now = time.perf_counter()
    elapsed = now - since_ts
    total_elapsed = (now - total_since_ts) if total_since_ts is not None else None

    if tracemalloc.is_tracing():
        py_current, py_peak = tracemalloc.get_traced_memory()
        py_current_mb = py_current / (1024 * 1024)
        py_peak_mb = py_peak / (1024 * 1024)
    else:
        py_current_mb = 0.0
        py_peak_mb = 0.0

    gc0, gc1, gc2 = gc.get_count()
    rss_mb = _rss_mb()

    total_text = (
        f" | total_elapsed={total_elapsed:.2f}s ({_format_elapsed_human(total_elapsed)})"
        if total_elapsed is not None
        else ""
    )
    _logger.info(
        f"[PROFILE] {label} | elapsed={elapsed:.2f}s{total_text}"
    )
    rss_text = f"rss={rss_mb:.1f}MB" if rss_mb >= 0 else "rss=n/a"
    _mem_logger.info(
        f"[PROFILE_MEM] {label} | elapsed={elapsed:.2f}s{total_text} | "
        f"{rss_text} | py_current={py_current_mb:.1f}MB | py_peak={py_peak_mb:.1f}MB | gc=({gc0},{gc1},{gc2})"
    )
    return now


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Import data from previous version to current DSpace')
    parser.add_argument('--resume',
                        help='Resume by loading values into dictionary',
                        required=False, type=str2bool, default=False)
    parser.add_argument('--config',
                        help='Update configs',
                        required=False, type=str, action='append')
    parser.add_argument('--assetstore',
                        help='Location of assetstore folder',
                        required=False, type=str, default="")
    parser.add_argument('--tempdb',
                        help='Tempdb export exists',
                        required=False, action="store_true", default=False)
    parser.add_argument('--test',
                        help='Empty table test',
                        required=False, nargs='*', default=[])
    parser.add_argument('--memory-profile',
                        help='Enable Python allocation tracing (slower)',
                        required=False, action='store_true', default=False)

    args = parser.parse_args()
    run_start_ts = time.perf_counter()
    if args.memory_profile and not tracemalloc.is_tracing():
        tracemalloc.start(25)
    checkpoint_ts = run_start_ts
    checkpoint_ts = log_checkpoint("startup", checkpoint_ts)

    for k, v in [x.split("=") for x in (args.config or [])]:
        _logger.info(f"Updating [{k}]->[{v}]")
        _1, prev_val = exists_key(k, env, True)
        if isinstance(prev_val, bool):
            new_val = str(v).lower() in ("true", "t", "1")
        elif prev_val is None:
            new_val = str(v)
        else:
            new_val = type(prev_val)(v)
        set_key(k, new_val, env)

    # add assetstore folder location to env
    env["assetstore"] = args.assetstore

    # just in case
    # verify_disabled_mailserver()
    confirm_important_configuration(env)

    # update based on env
    os.makedirs(env["resume_dir"], exist_ok=True)
    for k, v in env["cache"].items():
        env["cache"][k] = os.path.join(env["resume_dir"], v)

    dspace_be = dspace.rest(
        env["backend"]["endpoint"],
        env["backend"]["user"],
        env["backend"]["password"],
        env["backend"]["authentication"],
        env["backend"].get("reauth_minutes", 20),
    )
    checkpoint_ts = log_checkpoint("backend_connection_ready", checkpoint_ts)

    env["tempdb"] = args.tempdb
    env["test"] = args.test
    _logger.info("Loading repo objects")
    repo = pump.repo(env, dspace_be)
    checkpoint_ts = log_checkpoint("repo_objects_loaded", checkpoint_ts)

    ####
    _logger.info("New instance database status:")
    repo.raw_db_7.status()
    _logger.info("Reference database dspace status:")
    repo.raw_db_dspace_5.status()
    _logger.info("Reference database dspace-utilities status:")
    repo.raw_db_utilities_5.status()
    checkpoint_ts = log_checkpoint("database_status_checked", checkpoint_ts)

    import_sep = f"\n{40 * '*'}\n"
    _logger.info("Starting import")

    _logger.info("Verifying backend authentication before first validation")
    dspace_be.verify_authentication(force=True)
    checkpoint_ts = log_checkpoint("backend_auth_verified", checkpoint_ts)

    # import handles
    cache_file = env["cache"]["handle"]
    if deserialize(args.resume, repo.handles, cache_file):
        _logger.info(f"Resuming handle [{repo.handles.imported}]")
    else:
        repo.handles.import_to(dspace_be)
        repo.handles.serialize(cache_file)
    repo.diff(repo.handles)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("handles_done", checkpoint_ts)

    # import metadata
    cache_file = env["cache"]["metadataschema"]
    if deserialize(args.resume, repo.metadatas, cache_file):
        _logger.info(
            f"Resuming metadata [schemas:{repo.metadatas.imported_schemas}][fields:{repo.metadatas.imported_fields}]")
    else:
        repo.metadatas.import_to(dspace_be)
        repo.metadatas.serialize(cache_file)
    repo.diff(repo.metadatas)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("metadata_done", checkpoint_ts)

    # import bitstreamformatregistry
    cache_file = env["cache"]["bitstreamformat"]
    if deserialize(args.resume, repo.bitstreamformatregistry, cache_file):
        _logger.info(
            f"Resuming bitstreamformatregistry [{repo.bitstreamformatregistry.imported}]")
    else:
        repo.bitstreamformatregistry.import_to(dspace_be)
        repo.bitstreamformatregistry.serialize(cache_file)
    repo.diff(repo.bitstreamformatregistry)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("bitstreamformat_done", checkpoint_ts)

    # import community
    cache_file = env["cache"]["community"]
    if deserialize(args.resume, repo.communities, cache_file):
        _logger.info(
            f"Resuming community [coms:{repo.communities.imported_coms}][com2coms:{repo.communities.imported_com2coms}]")
    else:
        repo.communities.import_to(dspace_be, repo.handles, repo.metadatas)
        if len(repo.communities) == repo.communities.imported_coms:
            repo.communities.serialize(cache_file)
    repo.diff(repo.communities)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("community_done", checkpoint_ts)

    # import collection
    cache_file = env["cache"]["collection"]
    if deserialize(args.resume, repo.collections, cache_file):
        _logger.info(
            f"Resuming collection [cols:{repo.collections.imported_cols}] [groups:{repo.collections.imported_groups}]")
    else:
        repo.collections.import_to(dspace_be, repo.handles,
                                   repo.metadatas, repo.communities)
        repo.collections.serialize(cache_file)
    repo.diff(repo.collections)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("collection_done", checkpoint_ts)

    # import registration data
    cache_file = env["cache"]["registrationdata"]
    if deserialize(args.resume, repo.registrationdatas, cache_file):
        _logger.info(f"Resuming registrationdata [{repo.registrationdatas.imported}]")
    else:
        repo.registrationdatas.import_to(dspace_be)
        repo.registrationdatas.serialize(cache_file)
    repo.diff(repo.registrationdatas)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("registrationdata_done", checkpoint_ts)

    # import eperson groups
    cache_file = env["cache"]["epersongroup"]
    if deserialize(args.resume, repo.groups, cache_file):
        _logger.info(
            f"Resuming epersongroup [eperson:{repo.groups.imported_eperson}] [g2g:{repo.groups.imported_g2g}]")
    else:
        repo.groups.import_to(dspace_be, repo.metadatas, repo.collections.groups_id2uuid,
                              repo.communities.imported_groups)
        repo.groups.serialize(cache_file)
    repo.diff(repo.groups)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("epersongroup_done", checkpoint_ts)

    # import eperson
    cache_file = env["cache"]["eperson"]
    if deserialize(args.resume, repo.epersons, cache_file):
        _logger.info(f"Resuming epersons [{repo.epersons.imported}]")
    else:
        repo.epersons.import_to(env, dspace_be, repo.metadatas)
        repo.epersons.serialize(cache_file)
    repo.diff(repo.epersons)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("eperson_done", checkpoint_ts)

    # import userregistrations
    cache_file = env["cache"]["userregistration"]
    if deserialize(args.resume, repo.userregistrations, cache_file):
        _logger.info(f"Resuming userregistrations [{repo.userregistrations.imported}]")
    else:
        repo.userregistrations.import_to(dspace_be, repo.epersons)
        repo.userregistrations.serialize(cache_file)
    repo.diff(repo.userregistrations)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("userregistration_done", checkpoint_ts)

    # import group2eperson
    cache_file = env["cache"]["group2eperson"]
    if deserialize(args.resume, repo.egroups, cache_file):
        _logger.info(f"Resuming egroups [{repo.egroups.imported}]")
    else:
        repo.egroups.import_to(dspace_be, repo.groups, repo.epersons)
        repo.egroups.serialize(cache_file)
    repo.diff(repo.egroups)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("group2eperson_done", checkpoint_ts)

    # import licenses
    cache_file = env["cache"]["license"]
    if deserialize(args.resume, repo.licenses, cache_file):
        _logger.info(
            f"Resuming licenses [labels:{repo.licenses.imported_labels}] [licenses:{repo.licenses.imported_licenses}]")
    else:
        repo.licenses.import_to(env, dspace_be, repo.epersons)
        repo.licenses.serialize(cache_file)
    repo.diff(repo.licenses)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("license_done", checkpoint_ts)

    # import item
    import_workers = env["backend"].get("import_workers", 1)

    cache_file = env["cache"]["item"]
    if deserialize(args.resume, repo.items, cache_file):
        _logger.info(f"Resuming items [{repo.items.imported}]")
        repo.items.import_to(cache_file, dspace_be, repo.handles,
                             repo.metadatas, repo.epersons, repo.collections,
                             import_workers)
    else:
        repo.items.import_to(cache_file, dspace_be, repo.handles,
                             repo.metadatas, repo.epersons, repo.collections,
                             import_workers)
        repo.items.serialize(cache_file)
        repo.items.raw_after_import(
            env, repo.raw_db_7, repo.raw_db_dspace_5, repo.metadatas)
    repo.diff(repo.items)
    repo.test(repo.items)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("item_done", checkpoint_ts)

    # import bundle
    cache_file = env["cache"]["bundle"]
    if deserialize(args.resume, repo.bundles, cache_file):
        _logger.info(f"Resuming bundles [{repo.bundles.imported}]")
    else:
        repo.bundles.import_to(
            dspace_be,
            repo.metadatas,
            repo.items,
            import_workers,
        )
        repo.bundles.serialize(cache_file)
    repo.diff(repo.bundles)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("bundle_done", checkpoint_ts)

    # import bitstreams
    cache_file = env["cache"]["bitstream"]
    if deserialize(args.resume, repo.bitstreams, cache_file):
        _logger.info(f"Resuming bitstreams [{repo.bitstreams.imported}]")
        repo.bitstreams.import_to(
            env, cache_file, dspace_be, repo.metadatas, repo.bitstreamformatregistry, repo.bundles, repo.communities, repo.collections)
    else:
        repo.bitstreams.import_to(
            env, cache_file, dspace_be, repo.metadatas, repo.bitstreamformatregistry, repo.bundles, repo.communities, repo.collections)
        repo.bitstreams.serialize(cache_file)
    repo.diff(repo.bitstreams)
    repo.test(repo.bitstreams)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("bitstream_done", checkpoint_ts)

    # import usermetadata
    cache_file = env["cache"]["usermetadata"]
    if deserialize(args.resume, repo.usermetadatas, cache_file):
        _logger.info(f"Resuming usermetadatas [{repo.usermetadatas.imported}]")
    else:
        repo.usermetadatas.import_to(dspace_be, repo.bitstreams, repo.userregistrations)
        repo.usermetadatas.serialize(cache_file)
    repo.diff(repo.usermetadatas)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("usermetadata_done", checkpoint_ts)

    # import resourcepolicy
    cache_file = env["cache"]["resourcepolicy"]
    if deserialize(args.resume, repo.resourcepolicies, cache_file):
        _logger.info(f"Resuming resourcepolicies [{repo.resourcepolicies.imported}]")
    else:
        # before importing of resource policies we have to delete all
        # created data
        repo.raw_db_7.delete_resource_policy()
        repo.resourcepolicies.import_to(env, dspace_be, repo)
        repo.resourcepolicies.serialize(cache_file)
    repo.diff(repo.resourcepolicies)
    repo.test(repo.resourcepolicies)
    _logger.info(import_sep)
    checkpoint_ts = log_checkpoint("resourcepolicy_done", checkpoint_ts)

    # migrate sequences
    repo.sequences.migrate(env, repo.raw_db_7, repo.raw_db_dspace_5,
                           repo.raw_db_utilities_5)
    checkpoint_ts = log_checkpoint("sequences_migrated", checkpoint_ts)

    took = time.perf_counter() - run_start_ts
    _logger.info(f"Took [{round(took, 2)}] seconds to import all data")
    _logger.info(
        f"Made [{dspace_be.get_cnt}] GET requests, [{dspace_be.post_cnt}] POST requests.")

    _logger.info("New instance database status:")
    repo.raw_db_7.status()
    _logger.info("Reference database dspace status:")
    repo.raw_db_dspace_5.status()
    _logger.info("Reference database dspace-utilities status:")
    repo.raw_db_utilities_5.status()

    _logger.info("Database difference")
    repo.diff()

    _logger.info("Database test")
    repo.test()
    checkpoint_ts = log_checkpoint(
        "import_finished", checkpoint_ts, total_since_ts=run_start_ts)
