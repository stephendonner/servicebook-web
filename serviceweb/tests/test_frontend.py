import os
import re
import json

import yaml
import requests_mock

from serviceweb.tests.support import BaseTest


class FrontEndTest(BaseTest):

    def test_browsing_project(self):
        r = self.app.get('/info')
        first_proj_link = r.html.findAll('a')[3]['href']

        bz_matcher = re.compile('.*bugzilla.*')
        bz_resp = {'bugs': []}
        sw_matcher = re.compile('search.stage.mozaws.net.*')
        yamlf = os.path.join(os.path.dirname(__file__), '__api__.yaml')

        with open(yamlf) as f:
            sw_resp = yaml.load(f.read())

        with requests_mock.Mocker(real_http=True) as m:
            m.get(bz_matcher, text=json.dumps(bz_resp))
            m.get(sw_matcher, text=json.dumps(sw_resp))
            project_33 = self.app.get(first_proj_link)
            self.assertTrue('Rebecca' in project_33)

    def test_browsing_user(self):
        r = self.app.get('/info')

        for index, link in enumerate(r.html.findAll('a')):
            if 'Karl' in link.text:
                break

        karl = r.click(index=index)
        self.assertTrue('ABSearch' in karl)

    def test_browsing_group(self):
        custom = self.app.get('/groups/Customization')
        self.assertTrue('Telemetry' in custom)
