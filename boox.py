#  SPDX-License-Identifier: MIT

import configparser
import json
import locale
import logging
import os
import oss2
import requests
import uuid
from datetime import datetime


# ================= fix for multipart upload retries =================
from oss2.exceptions import ServerError

def fetch_with_retry(self):
    for i in range(self.max_retries):
        try:
            self.is_truncated = False
            self.next_marker = str(int(self.next_marker) + 1)
            # self.is_truncated, self.next_marker = self._fetch()
        except ServerError as e:
            if e.status // 100 != 5:
                raise

            if i == self.max_retries - 1:
                raise
        else:
            return

oss2.iterators._BaseIterator.fetch_with_retry = fetch_with_retry
# =====================================================================



def read_config(filename="config.ini"):
    config = configparser.ConfigParser()
    config.read(filename)

    return config


class Boox:

    def __init__(self, config, code=None, skip_init=False,
                 show_log=False, device_mac=None):

        if show_log:
            logging.basicConfig(level=logging.NOTSET)

        if config['default']['cloud']:
            self.cloud = config['default']['cloud']
        else:
            self.cloud = 'eur.boox.com'

        if skip_init:
            self.token = False
        else:
            if config['default']['token']:
                self.token = config['default']['token']
            elif config['default']['email'] and code:
                self.token = False
                self.login_with_email(config['default']['email'], code)

            self.userid = self.api_call('users/me')['data']['uid']

            self.api_call('users/getDevice')
            self.api_call('im/getSig', params={"user": self.userid})

            onyx_cloud = self.api_call('config/buckets')['data']['onyx-cloud']

            self.bucket_name = onyx_cloud['bucket']
            self.endpoint = onyx_cloud['aliEndpoint']
            
            self.get_sync_token()
            if not device_mac:
                self.device_mac = config['default']['device_mac']


    def login_with_email(self, email, code):

        self.token = self.api_call('users/signupByPhoneOrEmail',
                                   data={'mobi': email,
                                         'code': code})['data']['token']


    def get_sync_token(self):
        response = self.api_call('users/syncToken')
        self.sync_token = response["data"]["session_id"]
        
        

    def api_call(self, api_url, method='GET', headers={}, data={}, params={},
                 api='api/1'):

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if data:
            headers['Content-Type'] = 'application/json;charset=utf-8'
            method = 'POST'

        r = requests.request(method,
                             f'https://{self.cloud}/{api}/{api_url}',
                             headers=headers,
                             params=params,
                             data=json.dumps(data))

        logging.info(json.dumps(r.json(), indent=4))
        logging.info('')

        return r.json()


    def list_files(self, limit=24, offset=0):
        # I would expect LC_ALL to be set but it may not be
        locale.setlocale(locale.LC_ALL, locale.getlocale()[0])
        files = self.api_call('push/message',
                              params={"where": '{' f'"limit": {limit}, '
                                      f'"offset": {offset}, '
                                      '"parent": 0}'})['list']

        print("        ID               |    Size    | Name")
        print("-------------------------|------------|"
              "-------------------------------------------------------")

        for entry in files:
            data = entry['data']['args']
            format = data['formats'][0]
            print(f"{data['_id']} | "
                  f"{int(data['storage'][format]['oss']['size']):>10n} | "
                  f"{data['name']}")
            
        return files


    def send_file(self, filepath):
        
        list_old = self.list_files()
        stss_data = self.api_call('config/stss')['data']

        self.access_key_id = stss_data['AccessKeyId']
        self.access_key_secret = stss_data['AccessKeySecret']
        self.security_token = stss_data['SecurityToken']

        auth = oss2.Auth(self.access_key_id, self.access_key_secret)

        bucket = oss2.Bucket(auth, self.endpoint, self.bucket_name)

        tmp, extension = os.path.splitext(filepath)
        file_uuid = uuid.uuid4()
        remotename = f'{self.userid}/push/{file_uuid}{extension}'

        token_headers = {'x-oss-security-token': self.security_token}

        oss2.resumable_upload(bucket, remotename, filepath,
                              headers=token_headers)

        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)

        self.api_call('push/saveAndPush',
                      headers={
                          'Content-Type': 'application/json;charset=utf-8',
                      },
                      data={
                          "data": {
                              "bucket": self.bucket_name,
                              'name': filename,
                              'parent': None,
                              'resourceDisplayName': filename,
                              "resourceKey": remotename,
                              "resourceType": extension.split('.')[1],
                              "title": filename}
                      })

        list_new = self.list_files()
        new_file = self.get_list_diff_elem(list_old, list_new, filename)
        new_file_id = new_file['data']['args']['cbMsg']['id']
        old_rev = new_file['data']['args']['cbMsg']['rev']
        new_file_rev = '2-' + str(uuid.uuid4()).replace('-', '')[:32]
        self.revs_diff(new_file_id, new_file_rev)
        self.post_bulk_doc_data(filename, filesize, self.userid, file_uuid, new_file_id, new_file_rev, old_rev)
        

    def request_verification_code(self, email):
        self.api_call('users/sendMobileCode', data={"mobi": email})


    def delete_files(self, ids):
        self.api_call('push/message/batchDelete', data={"ids": ids})


    def revs_diff(self, file_id, rev_key, headers={}, cookies={}):
        
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Accept"] = "application/json"
            headers["Content-Type"] = "application/json"
                
        if self.sync_token:
            cookies = {'SyncGatewaySession': self.sync_token}
            headers['Cookie'] = 'SyncGatewaySession=' + self.sync_token
            
        r= requests.post(f'https://{self.cloud}/neocloud/_revs_diff',
                    headers=headers,
                    data=json.dumps({file_id: [rev_key]}))
        
        r.json()
        
        
    def get_list_diff_elem(self, list_old, list_new, filename):
        new_entries = []
        for entry in list_new:
            if entry not in list_old:
                new_entries.append(entry)
                
        for entry in new_entries:
            if filename in entry['data']['args']['name']:
                return entry
            
        raise Exception("File not found in new list")
    
    def post_bulk_doc_data(self, file_name, file_size, user_id, uuid, idkey, idrev, old_rev, headers={}, cookies={}):
        
        updated_time = round(datetime.now().timestamp()*1000)
        
        bulkdata = {
            "docs": [
                {
                "contentType": "digital_content",
                "content": json.dumps({
                    "category":[],
                    "tags": [],
                    "formats": [
                        "epub"
                    ],
                    "viewCount": 0,
                    "downloadCount": 0,
                    "commentsCount": 0,
                    "size": file_size,
                    "sourceType": 0,
                    "childCount": 0,
                    "pushNum": 1,
                    "isFolder": "no",
                    "parent": None,
                    "_id": idkey,
                    "storage":{
                        "epub":{
                            "oss":{
                                "displayName":file_name,
                                "expires":0,
                                "key":f"{user_id}/push/{uuid}.epub",
                                "provider":"oss",
                                "size":file_size,
                                "bucket":  "onyx-cloud-us",
                                "url":f"https://onyx-cloud-us.oss-us-west-1.aliyuncs.com/{user_id}/push/{uuid}.epub?OSSAccessKeyId=STS.NUcdtoXy6gPCzFyZcWB7D2fLn&Expires=1736348307&Signature=WeasSUGCjl2IFxGICQPp6w23%2BkA%3D&response-content-disposition=attachment&security-token=CAIS7Ah1q6Ft5B2yfSjIr5bWL87btaYX0JKoeGDIvmMCTrho3aPnjDz2IH1NdXhvAugat%2FU3lWFV6vwZlqp6U4cdmKVXq2cqvPpt6gqET9frW6PXhOV2JfDHdEGXDxnkphK7AYHQR8%2FcffGAck3NkjQJr5LxaTSlWS5RWP%2FsjoV8PPsaQi6ybzdNGLUzIRB5%2BvcHKVzbN%2FumLnyShXHLXmZlvgdghER166m03q%2Fk7QHF3nL31sgfpYn6PrGva6sUO4xkAe%2BowMt8dKfKzAdb73o687xt3oVO%2Fi3bm8yZH1hJ6g%2BaDvLQ9dRjTnZ%2BfbNoIaNfsP%2FmmNpyuOHYi%2BaGzA1Wb81YVynDSaW9xNfFAOekEdw7eL3nIA4mvrLpDJTutB4%2Ban82LR5Df8FbSkV9EhsxUDrXWCHMmhrDaRzxTLOeguNkk8hyyUT4uN6NKB2ERLGd3C0EO5g6Kl4zVVIf1nezdbQdIU4eMQFgAaeFCIF0Y1VVrqfssAbOVypmi2xM%2BL%2BcF%2FrdofIYcp6tHMAEg40bdYQDtWw2QhHqUNDcg0wPJmt%2BWuQUguu%2FPpmu%2BPqDx%2FmeJPPdTbJlxVxRamLWtDHfCyESfyr3981mYEKAvsnMrt3F%2BIgyFxA1tJINT1OCd8d96lV%2B%2B%2B6%2F8lPRkp3qWWv4%2BXEjqJuKvdtI71p0O%2F66ivObpHvDpx7kaKIiysDMQz8tE1blfHpo0LWbmHsbrgpEjnrzeXRs4l%2BKh2GrZ59ZgL7ZwXVVEuJYnuLERjG8u3h7a6eA6K1ZXvh5LqIcEKS72xB5g%2FbgqQG9vZGErAwiJYyEfrA9MYlTGECxsua%2FatcHiaprDnrzZOkeyPRXzALF2C8wjYdeNYY8AVtIQYcgZPKZvOzGvpwUz6lBi5rTbt3rQrLr7pz%2BRnavJnkQy9B%2B5gt1J2fV67WQSUOKYKg7u0CGvH9pH0q6is%2B11T4CAq%2BdAZ0G7bd6MX7Q0XpZZKEzDvA8%2BJHlX4poPB9YL4ZtGx37fLt80vlDZjF2%2FLo6ZKnMWGmb%2BA6fFFNabJ0ByKvDP9pS9DKowOptoNj7OEDa52v85qP%2Fp4Zl2lYqFg7pwuCWch39MEhrVD5y3N3fGe4qpcInpyACB7HxM%2F3e3qWEgaEuKAHp4RKUXt61rWP1NlnGUiPeMkwTrmlDtqEtgOQeP3ZxYGFmFta0JdJ3vxiQ51DKPjygRiivIFzdAn5Xj6E1zWeF43PCmqRyWXQ2KFduFomAiKXBjY%2BCjYPXUWemGZHq8KJqJvfRSIlDjKmEKI9JldRaxoMXExBebj61ir9a7%2FGkeaxVXlDOHA%2FXsMqAlujxlkaSY68s8XMhva%2FoDbDeuFTcCIlEBO60xDUX3Waabd7xt4cz4AENCE%2B0tuNP2KJqTAkJtWe2j6eC%2Fm661GW7LSvAbFhQLLHm4dyRiYaAcTZOT7C5h55g2CqjnlnhDnliHQpPpMhRSKx8n%2BG%2FQNzk%2F4jjd3jrMNb%2B9EEiNj4IVvaLId7mmOJRYBt%2Bva%2FlGoABQuesUR8%2BFW13TUE3feWXTgY8Vpw4FQTSzh3xYjPLhzv%2B9bIawRooUOT7RPTw5%2BC7vrgrxmn9kiHoY0KHYXnSz48EgkzdP45F6qIg8R%2BWPi8HMJ%2FtVylBa0v8zBNB0xFtp7ovl5ryOWEN0nkJvMH83WxIIAo%2FDFA18kD7Px5HjQ0gAA%3D%3D"
                                }
                            }
                        },
                    "userId": user_id,
                    "name":file_name,
                    "ownerId":user_id,
                    "title": file_name,
                    "distributeChannel":"onyx",
                    "guid":idkey,
                    "mac": self.device_mac,
                    "deviceModel": "NoteAir4C",
                    "updatedAt": updated_time,
                     # "createdAt": updated_time,  
                    }),
                "msgType": 2,
                "dbId": f"{user_id}-MESSAGE",
                "user": user_id,
                "name": f"{file_name}",
                "size": file_size,
                "uniqueId": idkey,
                "createdAt": updated_time,
                "updatedAt": updated_time,
                "_id": idkey,
                "_rev": idrev,
                "_revisions": {
                    "start": 2,
                    "ids": [
                    idrev.split('-')[1],
                    old_rev.split('-')[1]
                    ]
                }
                }
            ],
            "new_edits": False
            }
        
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Accept"] = "application/json"
            headers["Content-Type"] = "application/json"
            
        if self.sync_token:
            cookies = {'SyncGatewaySession': self.sync_token}
            
        headers['Cookie'] = 'SyncGatewaySession=' + self.sync_token
            
        r = requests.post(f'https://{self.cloud}/neocloud/_bulk_docs',
                        headers=headers,
                        json=bulkdata
                        )
        result = r.json()