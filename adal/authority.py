﻿#------------------------------------------------------------------------------
#
# Copyright (c) Microsoft Corporation. 
# All rights reserved.
# 
# This code is licensed under the MIT License.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
#------------------------------------------------------------------------------

try:
    from urllib.parse import quote, urlparse
except ImportError:
    from urllib import quote # pylint: disable=no-name-in-module
    from urlparse import urlparse # pylint: disable=import-error,ungrouped-imports

import requests

from .constants import AADConstants
from .adal_error import AdalError
from . import log
from . import util

class Authority(object):

    def __init__(self, authority_url, validate_authority=True):

        self._log = None
        self._call_context = None
        self._url = urlparse(authority_url)

        self._validate_authority_url()
        self._validated = not validate_authority

        self._host = None
        self._tenant = None
        self._parse_authority()

        self._authorization_endpoint = None
        self.token_endpoint = None
        self.device_code_endpoint = None

    @property
    def url(self):
        return self._url.geturl()

    def _validate_authority_url(self):

        if self._url.scheme != 'https':
            raise ValueError("The authority url must be an https endpoint.")

        if self._url.query:
            raise ValueError("The authority url must not have a query string.")

    def _parse_authority(self):
        self._host = self._url.hostname

        path_parts = self._url.path.split('/')
        try:
            self._tenant = path_parts[1]
        except IndexError:
            raise ValueError("Could not determine tenant.")

    def _perform_static_instance_discovery(self):

        self._log.debug("Performing static instance discovery")

        try:
            AADConstants.WELL_KNOWN_AUTHORITY_HOSTS.index(self._url.hostname)
        except ValueError:
            return False

        self._log.debug("Authority validated via static instance discovery")
        return True

    def _create_authority_url(self):
        return "https://{}/{}{}".format(self._url.hostname, 
                                        self._tenant, 
                                        AADConstants.AUTHORIZE_ENDPOINT_PATH)

    def _create_instance_discovery_endpoint_from_template(self, authority_host):

        discovery_endpoint = AADConstants.INSTANCE_DISCOVERY_ENDPOINT_TEMPLATE
        discovery_endpoint = discovery_endpoint.replace('{authorize_host}', authority_host)
        discovery_endpoint = discovery_endpoint.replace('{authorize_endpoint}', 
                                                        quote(self._create_authority_url(), 
                                                              safe='~()*!.\''))
        return urlparse(discovery_endpoint)

    def _perform_dynamic_instance_discovery(self):
        discovery_endpoint = self._create_instance_discovery_endpoint_from_template(
            AADConstants.WORLD_WIDE_AUTHORITY)
        get_options = util.create_request_options(self)
        operation = "Instance Discovery"
        self._log.debug("Attempting instance discover at: %s", discovery_endpoint.geturl())

        try:
            resp = requests.get(discovery_endpoint.geturl(), headers=get_options['headers'])
            util.log_return_correlation_id(self._log, operation, resp)
        except Exception:
            self._log.info("%s request failed", operation)
            raise

        if not util.is_http_success(resp.status_code):
            return_error_string = "{} request returned http error: {}".format(operation, 
                                                                              resp.status_code)
            error_response = ""
            if resp.text:
                return_error_string += " and server response: {}".format(resp.text)
                try:
                    error_response = resp.json()
                except ValueError:
                    pass

            raise AdalError(return_error_string, error_response)

        else:
            discovery_resp = resp.json()
            if discovery_resp.get('tenant_discovery_endpoint'):
                return discovery_resp['tenant_discovery_endpoint']
            else:
                raise AdalError('Failed to parse instance discovery response')

    def _validate_via_instance_discovery(self):
        valid = self._perform_static_instance_discovery()
        if not valid:
            self._perform_dynamic_instance_discovery()

    def _get_oauth_endpoints(self):

        if (not self.token_endpoint) or (not self.device_code_endpoint):
            self.token_endpoint = self._url.geturl() + AADConstants.TOKEN_ENDPOINT_PATH
            self.device_code_endpoint = self._url.geturl() + AADConstants.DEVICE_ENDPOINT_PATH

    def validate(self, call_context):

        self._log = log.Logger('Authority', call_context['log_context'])
        self._call_context = call_context

        if not self._validated:
            self._log.debug("Performing instance discovery: %s", self._url.geturl())
            self._validate_via_instance_discovery()
            self._validated = True
        else:
            self._log.debug(
                "Instance discovery/validation has either already been completed or is turned off: %s",
                self._url.geturl())

        self._get_oauth_endpoints()
