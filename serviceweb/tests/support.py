import warnings
import shutil
import os
from unittest import TestCase
import multiprocessing
from contextlib import contextmanager
import json
import re
import time
from http.client import HTTPConnection
import signal
import sys
from io import StringIO
import requests

import yaml
import requests_mock
from flask_webtest import TestApp
from serviceweb.server import create_app
import flask


_ONE_TIME = None
_INI = os.path.join(os.path.dirname(__file__), 'serviceweb.ini')
_BOOK = os.path.join(os.path.dirname(__file__), 'servicebook.ini')
_DB = os.path.join(os.path.dirname(__file__), 'projects.db')


def run_server(port=8888):
    """Running in a subprocess to avoid any interference
    """
    def _run():
        import os
        from servicebook.server import create_app as app
        import socketserver
        import sys

        socketserver.TCPServer.allow_reuse_address = True
        test_dir = os.path.dirname(_BOOK)
        os.chdir(test_dir)
        sys.stderr = open("coserver.stderr", "w")
        sys.stdout = open("coserver.stdout", "w")
        app = app(_BOOK)
        try:
            app.run(port=port, debug=False)
        except KeyboardInterrupt:
            pass

    p = multiprocessing.Process(target=_run)
    p.start()
    start = time.time()
    connected = False

    while time.time() - start < 10 and not connected:
        try:
            conn = HTTPConnection('localhost', 8888)
            conn.request("GET", "/api/")
            conn.getresponse()
            connected = True
        except Exception:
            time.sleep(.1)

    if not connected:
        if not p.is_alive():
            print("Looks like the process is born-dead")
            test_dir = os.path.dirname(_BOOK)
            with open(os.path.join(test_dir, "coserver.stdout")) as f:
                print(f.read())

            with open(os.path.join(test_dir, "coserver.stderr")) as f:
                print(f.read())
        else:
            os.kill(p.pid, signal.SIGTERM)
            p.join(timeout=1.)
        raise OSError('Could not connect to coserver')
    else:
        if not p.is_alive():
            raise OSError("There's a ghost coserver")

    return p


class BaseTest(TestCase):
    def setUp(self):
        global _ONE_TIME
        super(BaseTest, self).setUp()
        self.app = self._coserver = None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shutil.copyfile(_DB, _DB + '.saved')
            shutil.copyfile(_BOOK, _BOOK + '.saved')
            try:
                old_stdout = sys.stdout
                old_stderr = sys.stderr
                sys.stderr = sys.stdout = StringIO()
                try:
                    self._migrate(_DB)
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

                with open(_BOOK) as f:
                    new_content = f.read() % {'DB': _DB}
                    with open(_BOOK, 'w') as w:
                        w.write(new_content)

                if _ONE_TIME is None:
                    _ONE_TIME = TestApp(create_app(_INI))

                self._coserver = run_server(8888)
                self.app = _ONE_TIME
            except Exception:
                self.tearDown()
                raise

    def tearDown(self):
        if self._coserver is not None:
            os.kill(self._coserver.pid, signal.SIGTERM)
            self._coserver.join(timeout=1.)

        shutil.copyfile(_DB + '.saved', _DB)
        os.remove(_DB + '.saved')
        shutil.copyfile(_BOOK + '.saved', _BOOK)
        os.remove(_BOOK + '.saved')
        super(BaseTest, self).tearDown()

    def _migrate(self, dbfile):
        sqlite = 'sqlite:///' + dbfile
        from servicebook.db import migrate_db
        migrate_db(['--sqluri', sqlite])

    @contextmanager
    def logged_in(self, extra_mocks=None):
        if extra_mocks is None:
            extra_mocks = []

        class _Session(object):
            def __init__(self, session):
                self.session = session
                self._local = {}

            def pop(self, key):
                if key in self._local:
                    self._local.pop(key)
                return self.session.pop(key)

            def __getitem__(self, key):
                return self.session[key]

            def __setitem__(self, key, item):
                self.session[key] = item
                self._local[key] = item

        flask.session = _Session(flask.session)

        # redirects to 0auth, let's fake the callback
        code = 'yeah'
        oidc_resp = {'access_token': 'yup', 'token_type': 'xxx'}
        oidc_user = {'email': 'tarek@mozilla.com',
                     'login': 'tarekziade', 'name': 'Tarek Ziade'}
        oidc_auth = {}

        oidc_matcher = re.compile('auth0.com/oauth/token')
        oidc_usermatcher = re.compile('auth0.com/userinfo')
        oidc_authmatcher = re.compile('auth0.com/authorize')

        bz_matcher = re.compile('.*bugzilla.*')
        bz_resp = {'bugs': []}
        sw_matcher = re.compile('search.stage.mozaws.net.*')

        yamlf = os.path.join(os.path.dirname(__file__), '__api__.yaml')
        with open(yamlf) as f:
            sw_resp = yaml.load(f.read())

        headers = {'Content-Type': 'application/json'}
        with requests_mock.Mocker(real_http=True) as m:
            m.post(oidc_matcher, json=oidc_resp, headers=headers)
            m.post(oidc_usermatcher, json=oidc_user, headers=headers)
            m.get(oidc_authmatcher, json=oidc_auth, headers=headers)
            m.get(bz_matcher, text=json.dumps(bz_resp))
            m.get(sw_matcher, text=json.dumps(sw_resp))

            resp = self.app.get('/login')
            auth0_url = resp.location
            resp = requests.get(auth0_url)    # simulated auth0 call

            # now back to our redirect
            state = flask.session._local['state']
            resp = self.app.get('/auth0?code=%s&state=%s' % (code, state))
            resp.follow()     # that calls /login again

            # mocking more things
            for verb, url, text in extra_mocks:
                m.register_uri(verb, re.compile(url), text=text,
                               headers=headers)

            # at this point we are logged in
            try:
                yield
            finally:
                # logging out
                self.app.get('/logout')
