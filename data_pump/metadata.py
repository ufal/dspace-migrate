import logging


from utils import read_json, convert_response_to_json, \
    do_api_get_one, do_api_get_all, do_api_post


class Metadata:
    def __init__(self, statistics_dict):
        """
        Read metadatavalue as json and
        convert it to dictionary with tuple key: resource_type_id and resource_id.
        """
        self.metadatavalue_dict = dict()
        self.metadataschema_id_dict = dict()
        self.metadatafield_id_dict = dict()
        # import all metadata
        self.read_metadata()
        self.import_metadataschemaregistry(statistics_dict)
        self.import_metadatafieldregistry(statistics_dict)

    def read_metadata(self):
        metadatavalue_json_name = 'metadatavalue.json'
        metadatavalue_json = read_json(metadatavalue_json_name)
        if not metadatavalue_json:
            logging.info('Metadatavalue JSON is empty.')
            return
        for i in metadatavalue_json:
            key = (i['resource_type_id'], i['resource_id'])
            # replace separator @@ by ;
            i['text_value'] = i['text_value'].replace("@@", ";")
            if key in self.metadatavalue_dict.keys():
                self.metadatavalue_dict[key].append(i)
            else:
                self.metadatavalue_dict[key] = [i]

    def import_metadataschemaregistry(self, statistics_dict):
        """
        Import data into database.
        Mapped tables: metadataschemaregistry
        """
        metadataschema_json_name = 'metadataschemaregistry.json'
        metadataschema_url = 'core/metadataschemas'
        imported = 0
        # get all existing data from database table
        try:
            response = do_api_get_all(metadataschema_url)
            existing_data_dict = convert_response_to_json(response)['_embedded'][
                'metadataschemas']
        except Exception:
            logging.error('GET request ' + response.url + ' failed.')

        metadataschema_json_a = read_json(metadataschema_json_name)
        if not metadataschema_json_a:
            logging.info("Metadataschemaregistry JSON is empty.")
            return
        for i in metadataschema_json_a:
            metadataschema_json_p = {'namespace': i['namespace'],
                                     'prefix': i['short_id']}
            # prefix has to be unique
            try:
                response = do_api_post(metadataschema_url, None, metadataschema_json_p)
                self.metadataschema_id_dict[i['metadata_schema_id']] = \
                    convert_response_to_json(response)['id']
                imported += 1
            except Exception:
                found = False
                if not existing_data_dict:
                    logging.error('POST request ' + response.url + ' for id: ' + str(
                        i['metadata_schema_id']) + ' failed. Status: ' + str(
                        response.status_code))
                    continue
                for j in existing_data_dict:
                    if j['prefix'] != i['short_id']:
                        continue
                    self.metadataschema_id_dict[i['metadata_schema_id']] = j['id']
                    logging.info('Metadataschemaregistry '
                                 ' prefix: ' + i['short_id']
                                 + 'already exists in database!')
                    found = True
                    imported += 1
                    break
                if not found:
                    logging.error('POST request ' + response.url + ' for id: ' + str(
                        i['metadata_schema_id']) + ' failed. Status: ' + str(
                        response.status_code))

        statistics_val = (len(metadataschema_json_a), imported)
        statistics_dict['metadataschemaregistry'] = statistics_val
        logging.info("MetadataSchemaRegistry was successfully imported!")

    def import_metadatafieldregistry(self, statistics_dict):
        """
        Import data into database.
        Mapped tables: metadatafieldregistry
        """
        metadatafield_json_name = 'metadatafieldregistry.json'
        metadatafield_url = 'core/metadatafields'
        imported = 0
        try:
            response = do_api_get_all(metadatafield_url)
            existing_data_dict = convert_response_to_json(response)['_embedded'][
                'metadatafields']
        except Exception:
            logging.error('GET request ' + response.url +
                          ' failed. Status: ' + str(response.status_code))

        metadatafield_json_a = read_json(metadatafield_json_name)
        if not metadatafield_json_a:
            logging.info("Metadatafieldregistry JSON is empty.")
            return
        for i in metadatafield_json_a:
            metadatafield_json_p = {'element': i['element'],
                                    'qualifier': i['qualifier'],
                                    'scopeNote': i['scope_note']}
            params = {'schemaId': self.metadataschema_id_dict[i['metadata_schema_id']]}
            # element and qualifier have to be unique
            try:
                response = do_api_post(metadatafield_url, params, metadatafield_json_p)
                self.metadatafield_id_dict[i['metadata_field_id']] = \
                    convert_response_to_json(response)['id']
                imported += 1
            except Exception:
                found = False
                if not existing_data_dict:
                    logging.error('POST request ' + response.url + ' for id: ' + str(
                        i['metadata_field_id']) + ' failed. Status: ' + str(
                        response.status_code))
                    continue
                for j in existing_data_dict:
                    if j['element'] != i['element'] or j['qualifier'] != i['qualifier']:
                        continue
                    self.metadatafield_id_dict[i['metadata_field_id']] = j['id']
                    logging.info('Metadatafieldregistry with element: ' +
                                 i['element'] + ' already exists in database!')
                    found = True
                    imported += 1
                    break
                if not found:
                    logging.error('POST request ' + response.url + ' for id: ' + str(
                        i['metadata_field_id']) + ' failed. Status: ' + str(
                        response.status_code))

        statistics_val = (len(metadatafield_json_a), imported)
        statistics_dict['metadatafieldregistry'] = statistics_val
        logging.info("MetadataFieldRegistry was successfully imported!")

    def get_metadata_value(self, old_resource_type_id, old_resource_id):
        """
        Get metadata value for dspace object.
        """
        url_metadatafield = 'core/metadatafields'
        url_metadataschema = 'core/metadataschemas'
        result = dict()
        # get all metadatavalue for object
        if (old_resource_type_id, old_resource_id) not in self.metadatavalue_dict:
            logging.info('Metadatavalue for resource_type_id: ' +
                         str(old_resource_type_id) + ' and resource_id: ' +
                         str(old_resource_id) + 'does not exist.')
            return None
        metadatavalue_obj = self.metadatavalue_dict[(
            old_resource_type_id, old_resource_id)]
        # create list of object metadata
        for i in metadatavalue_obj:
            if i['metadata_field_id'] not in self.metadatafield_id_dict:
                continue
            try:
                response = do_api_get_one(
                    url_metadatafield,
                    self.metadatafield_id_dict[i['metadata_field_id']])
                metadatafield_json = convert_response_to_json(response)
            except Exception:
                logging.error('GET request' + response.url +
                              ' failed. Status: ' + str(response.status_code))
                continue
            # get metadataschema
            try:
                response = do_api_get_one(
                    url_metadataschema, metadatafield_json['_embedded']['schema']['id'])
                metadataschema_json = convert_response_to_json(response)
            except Exception:
                logging.error('GET request ' + response.url +
                              ' failed. Status: ' + str(response.status_code))
                continue
            # define and insert key and value of dict
            key = metadataschema_json['prefix'] + '.' + metadatafield_json['element']
            value = {'value': i['text_value'], 'language': i['text_lang'],
                     'authority': i['authority'], 'confidence': i['confidence'],
                     'place': i['place']}
            if metadatafield_json['qualifier']:
                key += '.' + metadatafield_json['qualifier']
            if key in result.keys():
                result[key].append(value)
            else:
                result[key] = [value]
        return result
