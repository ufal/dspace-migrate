settings = {

    "handle_prefix": ["http://hdl.handle.net/", "https://hdl.handle.net/"],

    "actions": ["READ", "WRITE", "OBSOLETE (DELETE)",
                "ADD", "REMOVE", "WORKFLOW_STEP_1",
                "WORKFLOW_STEP_2", "WORKFLOW_STEP_3",
                "WORKFLOW_ABORT", "DEFAULT_BITSTREAM_READ",
                "DEFAULT_ITEM_READ", "ADMIN",
                "WITHDRAWN_READ"]
}

# # there should be no need to modify this part, unless adding new tests.
# # mainly concatenates and parses settings above
# OAI_url = BE_url + "oai/"
# OAI_req = OAI_url + "request?verb=ListRecords&metadataPrefix=oai_dc&set="
# OAI_openaire_dc = OAI_url + "openaire_data?verb=ListRecords&" \
#                             "metadataPrefix=oai_dc&set="
# OAI_openaire_datacite = OAI_url + "openaire_data?verb=ListRecords&" \
#                                   "metadataPrefix=oai_datacite&set="
# OAI_olac = OAI_url + "request?verb=ListRecords&metadataPrefix=olac&set="
# OAI_cmdi = OAI_url + "request?verb=ListRecords&metadataPrefix=cmdi&set="
# IMPORT_DATA_PATH = "data/license_import/"
# COM = "BB-TEST-COM"
# com_UUID = None
# COL = "BB-TEST-COL"
# col_UUID = None
# ITM_prefix = "BB-TEST-ITM-"
# EMBEDDED = "_embedded"
#
# import enum
#
#
# class ItemType(enum.Enum):
#     ITEM = 1
#     COMMUNITY = 2
#     COLLECTION = 3
#
#
# # constants for resource type ID, taken from DSpace (BE) codebase
# SITE = 5
