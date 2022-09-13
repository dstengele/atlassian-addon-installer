import json
import logging
import os
import re
import tempfile
import time

import requests as requests
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO)


class AddonDeployer:
    def __init__(self, config_file, credentials_file):
        self.target_config = json.load(config_file)

        self.tempdir = tempfile.TemporaryDirectory()

        self.url = self.target_config.get("url")

        credentials = json.load(credentials_file)
        self.username = credentials.get("username")
        self.password = credentials.get("password")

        self.upm_token = None
        self.current_addon_data = None

        self.upm_session = requests.session()

    def deploy_config(self):
        for addon_entry in self.target_config.get("config"):
            self.update_addon_data()

            addon_key = addon_entry.get("key")
            addon_target_version = addon_entry.get("version")

            logging.info(f"Checking plugin {addon_key}")

            if addon_target_version != self.current_addon_data.get(addon_key, ""):
                self.install(addon_key, addon_target_version)

        self.tempdir.cleanup()

    def update_addon_data(self):
        addon_dict = {}

        try:
            addon_response = self.upm_session.get(
                f"{self.url}/rest/plugins/1.0/", auth=(self.username, self.password)
            )
            self.upm_token = addon_response.headers.get("upm-token")

            addon_data = addon_response.json()
        except Exception:
            addon_data = {}

        for addon in addon_data.get("plugins", []):
            addon_dict[addon["key"]] = addon["version"]

        self.current_addon_data = addon_dict

    def get_all_addon_versions(self, addon_key):
        versions = []
        item_index = 0
        while True:
            new_versions_reply = self.upm_session.get(
                f"https://marketplace.atlassian.com/rest/2/addons/{addon_key}/versions/",
                params={"offset": item_index, "limit": 50},
            )
            new_versions_reply.raise_for_status()

            new_versions = (
                new_versions_reply.json().get("_embedded", {}).get("versions", [])
            )
            if not new_versions:
                break
            versions += new_versions
            item_index += 50
        return versions

    def get_download_url(self, addon_key, addon_version):
        for mp_version in self.get_all_addon_versions(addon_key):
            if mp_version.get("name", None) == addon_version:
                return (
                    mp_version.get("_embedded", {})
                    .get("artifact", {})
                    .get("_links", {})
                    .get("binary", {})
                    .get("href", None)
                )
        else:
            return None

    def install(self, addon_key, addon_version):
        logging.info("Trying to install plugin...")
        logging.info("Looking for download URL...")
        install_url = self.get_download_url(addon_key, addon_version)

        if not install_url:
            raise Exception("Addon version not found")

        logging.info("Downloading addon from marketplace...")
        jar_download_result = requests.get(install_url)

        if "Content-Disposition" in jar_download_result.headers.keys():
            fname = re.findall("filename=\"(.+)\"", jar_download_result.headers["Content-Disposition"])[0]
        else:
            fname = install_url.split("/")[-1]
        addon_file_path = os.path.join(self.tempdir.name, fname)

        logging.info(f"Savin plugin jar to file {addon_file_path}")

        addon_file = open(addon_file_path, "w+b")

        addon_file.write(jar_download_result.content)
        addon_file.seek(0)
        logging.info("Download from Marketplace complete, uploading to UPM...")

        result = self.upm_session.post(
            f"{self.url}/rest/plugins/1.0/?token={self.upm_token}",
            auth=(self.username, self.password),
            headers={
                "Accept": "application/json",
            },
            files={"plugin": addon_file},
        )

        addon_file.close()

        result.raise_for_status()

        check_url = urljoin(self.url, result.json().get("links", {}).get("alternate"))

        while True:
            status_result = self.upm_session.get(check_url, auth=(self.username, self.password))
            status_result.raise_for_status()
            status_result_json = status_result.json()
            if status_result_json.get("done", False):
                break
            status_progress = status_result_json.get("progress", 0) * 100
            logging.info(f"Plugin Install not done yet, progress: {status_progress}%")
            time.sleep(5)

        logging.info(f"Plugin {addon_key} was successfully installed ")


if __name__ == "__main__":
    with open("config.json", "r") as config_file, open("credentials.json", "r") as credentials_file:
        deployer = AddonDeployer(config_file, credentials_file)
        deployer.deploy_config()
