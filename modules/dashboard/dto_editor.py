# Copyright 2014 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Classes supporting editing of DAO/DTO-managed models."""

__author__ = [
    'John Orr (jorr@google.com)',
    'Mike Gainer (mgainer@googe.com)'
]

import cgi
import copy
import urllib

from common import utils as common_utils
from common.crypto import XsrfTokenManager
from controllers import utils
from models import roles
from models import transforms
from modules.oeditor import oeditor


class BaseDatastoreAssetEditor(utils.ApplicationHandler):

    def get_form(
            self, rest_handler, key, exit_url, deletable=True,
            auto_return=False, app_context=None):
        """Build the Jinja template for the editor form."""
        rest_url = self.canonicalize_url(rest_handler.URI)
        if exit_url:
            exit_url = self.canonicalize_url(exit_url)
        if key and deletable:
            delete_url = '%s?%s' % (
                self.canonicalize_url(rest_handler.URI),
                urllib.urlencode({
                    'key': key,
                    'xsrf_token': cgi.escape(
                        self.create_xsrf_token(rest_handler.XSRF_TOKEN))
                    }))
        else:
            delete_url = None

        if app_context:
            schema = rest_handler.get_schema(app_context)
        else:
            schema = rest_handler.get_schema()
        return oeditor.ObjectEditor.get_html_for(
            self,
            schema.get_json_schema(),
            schema.get_schema_dict(),
            key, rest_url, exit_url,
            auto_return=auto_return,
            delete_url=delete_url, delete_method='delete',
            required_modules=rest_handler.REQUIRED_MODULES,
            extra_js_files=rest_handler.EXTRA_JS_FILES,
            extra_css_files=getattr(rest_handler, 'EXTRA_CSS_FILES', None),
            additional_dirs=getattr(rest_handler, 'ADDITIONAL_DIRS', None),)


class BaseDatastoreRestHandler(utils.BaseRESTHandler):
    """Basic REST operations for DTO objects.

    Provides REST functionality for derived classes based on Entity/DAO/DTO
    pattern (see models/models.py).  Subclasses are expected to provide
    the following:

    DAO: Subclasses should have a class-level variable named "DAO".
         This should name the DAO type corresponding to the entity
         being handled.  DAO must have a member "DTO", which names
         the DTO type.
    XSRF_TOKEN: A short string of the form 'foobar-edit', where foobar
         is a short, lowercased version of the name of the entity type.
    SCHEMA_VERSIONS: A list of supported version numbers of schemas
         of items.  The 0th element of the list must be the preferred
         version number for newly-created items.

    Hook method overrides.  Other than the basic 'put', 'delete', and
    'get' methods, there are a number of hook functions you may need
    to override.  The only mandatory function is 'get_default_version()'.
    """

    # Enable other modules to add transformations to the schema. Each member
    # must be a function of the form:
    #     callback(question_field_registry)
    # where the argument is the root FieldRegistry for the schema
    SCHEMA_LOAD_HOOKS = ()

    # Enable other modules to add transformations to the load. Each member must
    # be a function of the form:
    #     callback(question, question_dict)
    # and the callback should update fields of the question_dict, which will be
    # returned to the caller of a GET request.

    PRE_LOAD_HOOKS = ()

    # Enable other modules to add transformations to the save. Each member must
    # be a function of the form:
    #     callback(question, question_dict)
    # and the callback should update fields of the question with values read
    # from the dict which was the payload of a PUT request.
    PRE_SAVE_HOOKS = ()


    def sanitize_input_dict(self, json_dict):
        """Give subclasses a hook to clean up incoming data before storage.

        Args:
          json_dict: This is the raw dict contining a parse of the JSON
              object as returned by the form editor.  In particular, it
              has not been converted into a DTO yet.  Modify the dict
              in place to clean up values.  (E.g., remove leading/trailing
              whitespace, fix up string/int conversions, etc.)
        """
        pass

    def validate(self, item_dict, key, schema_version, errors):
        """Allow subclasses to do validations that the form cannot.

        Args:
          item_dict: A Python dict that will be used to populate
              the saved version of the item.  Modify this in place as
              necessary.
          key: The key for the item, if available.  New items will not
              yet have a key when this function is called.
          schema_version: This version has already been checked against
              the SCHEMA_VERSIONS declared in your class; it is provided
              to facilitate dispatch to a version-specific validation
              function.
          errors: A list of strings.  These will be displayed
              on the editor page when there is a problem.  The save
              operation will be prevented if there are any entries in
              the errors list.
        """
        pass

    def pre_save_hook(self, dto):
        """Give subclasses a hook to modify the DTO before saving."""
        pass

    def after_save_hook(self):
        """Give subclasses a hook to perform an action after saving."""
        pass

    def is_deletion_allowed(self, dto):
        """Allow subclasses to check referential integrity before delete.

        If deletion is not allowed, the subclass should:
        - Return False.
        - Return an appropriate message to the REST client; the base
          class will just return without taking any further action.

        Args:
          dto: A DTO of the type specified by the subclass' DAO.DTO variable.
        Returns:
          True: The base class may proceed with deletion.
          False: Deletion is prohibited; derived class has emitted a response.
        """
        return True

    def transform_for_editor_hook(self, item_dict):
        """Allow subclasses to modify dict before it goes to the edit form."""
        return item_dict

    def transform_after_editor_hook(self, item_dict):
        """Allow subclasses to modify dict returned from editor form."""
        return item_dict

    def get_default_content(self):
        """Subclass provides default values to initialize editor form."""
        raise NotImplementedError('Subclasses must override this function.')

    def put(self):
        """Store a DTO in the datastore in response to a PUT."""
        request = transforms.loads(self.request.get('request'))
        key = request.get('key')

        if not self.assert_xsrf_token_or_fail(
                request, self.XSRF_TOKEN, {'key': key}):
            return

        if not roles.Roles.is_course_admin(self.app_context):
            transforms.send_json_response(
                self, 401, 'Access denied.', {'key': key})
            return

        payload = request.get('payload')
        json_dict = transforms.loads(payload)
        self.sanitize_input_dict(json_dict)

        errors = []
        try:
            python_dict = transforms.json_to_dict(
                json_dict, self.get_schema().get_json_schema_dict())

            version = python_dict.get('version')
            if version not in self.SCHEMA_VERSIONS:
                errors.append('Version %s not supported.' % version)
            else:
                python_dict = self.transform_after_editor_hook(python_dict)
                self.validate(python_dict, key, version, errors)
        except (TypeError, ValueError) as err:
            errors.append(str(err))
        if errors:
            self.validation_error('\n'.join(errors), key=key)
            return

        if key:
            item = self.DAO.DTO(key, python_dict)
        else:
            item = self.DAO.DTO(None, python_dict)

        self.pre_save_hook(item)
        common_utils.run_hooks(self.PRE_SAVE_HOOKS, item, python_dict)
        key_after_save = self.DAO.save(item)
        self.after_save_hook()

        transforms.send_json_response(
            self, 200, 'Saved.', payload_dict={'key': key_after_save})

    def delete(self):
        """Delete the Entity in response to REST request."""
        key = self.request.get('key')

        if not self.assert_xsrf_token_or_fail(
                self.request, self.XSRF_TOKEN, {'key': key}):
            return

        if not roles.Roles.is_course_admin(self.app_context):
            transforms.send_json_response(
                self, 401, 'Access denied.', {'key': key})
            return

        item = self.DAO.load(key)
        if not item:
            transforms.send_json_response(
                self, 404, 'Not found.', {'key': key})
            return

        if self.is_deletion_allowed(item):
            self.DAO.delete(item)
            transforms.send_json_response(self, 200, 'Deleted.')

    def get(self):
        """Respond to the REST GET verb with the contents of the item."""
        key = self.request.get('key')
        if not roles.Roles.is_course_admin(self.app_context):
            transforms.send_json_response(
                self, 401, 'Access denied.', {'key': key})
            return

        if key:
            item = self.DAO.load(key)
            version = item.dict.get('version')
            if version not in self.SCHEMA_VERSIONS:
                transforms.send_json_response(
                    self, 403, 'Version %s not supported.' % version,
                    {'key': key})
                return
            display_dict = copy.copy(item.dict)
            display_dict['id'] = item.id
            common_utils.run_hooks(self.PRE_LOAD_HOOKS, item, display_dict)
            payload_dict = self.transform_for_editor_hook(display_dict)
        else:
            payload_dict = self.get_default_content()

        transforms.send_json_response(
            self, 200, 'Success',
            payload_dict=payload_dict,
            xsrf_token=XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN))
