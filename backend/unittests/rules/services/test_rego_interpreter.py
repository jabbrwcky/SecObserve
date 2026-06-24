import unittest
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from application.rules.services import rego_interpreter as rego_module
from application.rules.services.rego_interpreter import (
    OpaServerRegoInterpreter,
    RegoError,
    RegoException,
    RegoInterpreter,
    RegopyRegoInterpreter,
    _opa_error,
    _use_opa_server,
)


def _ok_put() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    return response


class TestRegoException(unittest.TestCase):
    def test_message(self):
        exception = RegoException("test error")
        self.assertEqual(str(exception), "[ErrorDetail(string='test error', code='invalid')]")

    def test_is_exception(self):
        exception = RegoException("test error")
        self.assertIsInstance(exception, Exception)


class TestBackendSelection(SimpleTestCase):
    """RegoInterpreter facade picks the backend based on config / availability."""

    @override_settings(OPA_USE_SERVER=False)
    @patch.object(rego_module, "REGOPY_AVAILABLE", True)
    @patch.object(rego_module, "OpaServerRegoInterpreter")
    @patch.object(rego_module, "RegopyRegoInterpreter")
    def test_uses_regopy_by_default(self, mock_regopy, mock_opa):
        interpreter = RegoInterpreter("package rule")

        mock_regopy.assert_called_once_with("package rule")
        mock_opa.assert_not_called()
        self.assertEqual(interpreter._backend, mock_regopy.return_value)

    @override_settings(OPA_USE_SERVER=True)
    @patch.object(rego_module, "REGOPY_AVAILABLE", True)
    @patch.object(rego_module, "OpaServerRegoInterpreter")
    @patch.object(rego_module, "RegopyRegoInterpreter")
    def test_uses_opa_when_configured(self, mock_regopy, mock_opa):
        interpreter = RegoInterpreter("package rule")

        mock_opa.assert_called_once_with("package rule")
        mock_regopy.assert_not_called()
        self.assertEqual(interpreter._backend, mock_opa.return_value)

    @override_settings(OPA_USE_SERVER=False)
    @patch.object(rego_module, "REGOPY_AVAILABLE", False)
    @patch.object(rego_module, "OpaServerRegoInterpreter")
    @patch.object(rego_module, "RegopyRegoInterpreter")
    def test_falls_back_to_opa_when_regopy_unavailable(self, mock_regopy, mock_opa):
        interpreter = RegoInterpreter("package rule")

        mock_opa.assert_called_once_with("package rule")
        mock_regopy.assert_not_called()
        self.assertEqual(interpreter._backend, mock_opa.return_value)

    @patch.object(rego_module, "OpaServerRegoInterpreter")
    @patch.object(rego_module, "RegopyRegoInterpreter")
    def test_query_delegates_to_backend(self, mock_regopy, mock_opa):
        with override_settings(OPA_USE_SERVER=True):
            interpreter = RegoInterpreter("package rule")

        data = {"title": "test"}
        result = interpreter.query(data)

        mock_opa.return_value.query.assert_called_once_with(data)
        self.assertEqual(result, mock_opa.return_value.query.return_value)

    @override_settings(OPA_USE_SERVER=False)
    def test_use_opa_server_helper_regopy_available(self):
        with patch.object(rego_module, "REGOPY_AVAILABLE", True):
            self.assertFalse(_use_opa_server())

    @override_settings(OPA_USE_SERVER=False)
    def test_use_opa_server_helper_regopy_unavailable(self):
        with patch.object(rego_module, "REGOPY_AVAILABLE", False):
            self.assertTrue(_use_opa_server())

    @override_settings(OPA_USE_SERVER=True)
    def test_use_opa_server_helper_configured(self):
        with patch.object(rego_module, "REGOPY_AVAILABLE", True):
            self.assertTrue(_use_opa_server())


class TestRegopyRegoInterpreterInit(unittest.TestCase):

    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_init_success(self, mock_interpreter_cls):
        mock_interpreter = MagicMock()
        mock_interpreter_cls.return_value = mock_interpreter
        mock_bundle = MagicMock()
        mock_interpreter.build.return_value = mock_bundle

        rego_module_text = "package rule\ndefault allow = false"
        interpreter = RegopyRegoInterpreter(rego_module_text)

        self.assertEqual(interpreter.policy, rego_module_text)
        self.assertEqual(interpreter.rego_bundle, mock_bundle)
        self.assertEqual(mock_interpreter.log_level, 1)
        mock_interpreter.add_module.assert_called_once_with("rule", rego_module_text)
        mock_interpreter.build.assert_called_once_with("data")

    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_init_rego_error(self, mock_interpreter_cls):
        mock_interpreter = MagicMock()
        mock_interpreter_cls.return_value = mock_interpreter
        mock_interpreter.add_module.side_effect = RegoError("syntax error")

        with self.assertRaises(RegoException) as context:
            RegopyRegoInterpreter("invalid rego")

        self.assertIn("Error while building rego bundle", str(context.exception))
        self.assertIn("syntax error", str(context.exception))


class TestRegopyRegoInterpreterQuery(unittest.TestCase):

    @patch("application.rules.services.rego_interpreter.Interpreter")
    def setUp(self, mock_interpreter_cls):
        mock_interpreter = MagicMock()
        mock_interpreter_cls.return_value = mock_interpreter
        mock_interpreter.build.return_value = MagicMock()
        self.interpreter = RegopyRegoInterpreter("package rule")
        self.mock_bundle = self.interpreter.rego_bundle

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_success(self, mock_interpreter_cls, mock_input_cls):
        mock_input = MagicMock()
        mock_input_cls.return_value = mock_input

        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        expected_result = {"severity": "High", "status": "Open"}
        mock_expression = MagicMock()
        mock_expression.get.return_value = expected_result
        mock_node = MagicMock()
        mock_node.expressions = [mock_expression]
        mock_output = MagicMock()
        mock_output.results = [mock_node]
        mock_rego_run.query_bundle.return_value = mock_output

        data = {"title": "test"}
        result = self.interpreter.query(data)

        self.assertEqual(result, expected_result)
        mock_input_cls.assert_called_once_with(data)
        mock_rego_run.set_input.assert_called_once_with(mock_input)
        mock_rego_run.query_bundle.assert_called_once_with(self.mock_bundle)
        mock_expression.get.assert_called_once_with("rule")

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_with_none_data(self, mock_interpreter_cls, mock_input_cls):
        mock_input = MagicMock()
        mock_input_cls.return_value = mock_input

        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        expected_result = {"severity": "Low"}
        mock_expression = MagicMock()
        mock_expression.get.return_value = expected_result
        mock_node = MagicMock()
        mock_node.expressions = [mock_expression]
        mock_output = MagicMock()
        mock_output.results = [mock_node]
        mock_rego_run.query_bundle.return_value = mock_output

        result = self.interpreter.query()

        self.assertEqual(result, expected_result)
        mock_input_cls.assert_called_once_with(None)

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_no_results(self, mock_interpreter_cls, mock_input_cls):
        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        mock_output = MagicMock()
        mock_output.results = []
        mock_rego_run.query_bundle.return_value = mock_output

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(str(context.exception), "[ErrorDetail(string='Rego output has no results', code='invalid')]")

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_no_results_none(self, mock_interpreter_cls, mock_input_cls):
        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        mock_output = MagicMock()
        mock_output.results = None
        mock_rego_run.query_bundle.return_value = mock_output

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(str(context.exception), "[ErrorDetail(string='Rego output has no results', code='invalid')]")

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_no_expressions(self, mock_interpreter_cls, mock_input_cls):
        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        mock_node = MagicMock()
        mock_node.expressions = []
        mock_output = MagicMock()
        mock_output.results = [mock_node]
        mock_rego_run.query_bundle.return_value = mock_output

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(
            str(context.exception), "[ErrorDetail(string='Rego results have no expressions', code='invalid')]"
        )

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_no_rule_element(self, mock_interpreter_cls, mock_input_cls):
        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run

        mock_expression = MagicMock()
        mock_expression.get.return_value = None
        mock_node = MagicMock()
        mock_node.expressions = [mock_expression]
        mock_output = MagicMock()
        mock_output.results = [mock_node]
        mock_rego_run.query_bundle.return_value = mock_output

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(
            str(context.exception), "[ErrorDetail(string=\"Rego expressions have no 'rule' element\", code='invalid')]"
        )

    @patch("application.rules.services.rego_interpreter.Input")
    @patch("application.rules.services.rego_interpreter.Interpreter")
    def test_query_rego_error(self, mock_interpreter_cls, mock_input_cls):
        mock_rego_run = MagicMock()
        mock_interpreter_cls.return_value = mock_rego_run
        mock_rego_run.query_bundle.side_effect = RegoError("query failed")

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertIn("Error while querying rego module", str(context.exception))
        self.assertIn("query failed", str(context.exception))


class TestOpaError(unittest.TestCase):
    def test_message_and_errors(self):
        response = MagicMock()
        response.json.return_value = {
            "message": "compile error",
            "errors": [{"message": "rego_parse_error"}, {"message": "var x is unsafe"}],
        }
        self.assertEqual(_opa_error(response), "compile error: rego_parse_error; var x is unsafe")

    def test_message_only(self):
        response = MagicMock()
        response.json.return_value = {"message": "bad request"}
        self.assertEqual(_opa_error(response), "bad request")

    def test_errors_only(self):
        response = MagicMock()
        response.json.return_value = {"errors": [{"message": "boom"}]}
        self.assertEqual(_opa_error(response), "boom")

    def test_non_json_falls_back_to_text(self):
        response = MagicMock()
        response.json.side_effect = ValueError("no json")
        response.text = "Internal Server Error"
        self.assertEqual(_opa_error(response), "Internal Server Error")

    def test_non_json_no_text_falls_back_to_status(self):
        response = MagicMock()
        response.json.side_effect = ValueError("no json")
        response.text = ""
        response.status_code = 500
        self.assertEqual(_opa_error(response), "HTTP 500")


@override_settings(OPA_SERVER_URL="http://localhost:8181", OPA_SERVER_TIMEOUT=60)
class TestOpaServerRegoInterpreterInit(SimpleTestCase):

    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_success(self, mock_requests):
        mock_requests.put.return_value = _ok_put()

        rego_module_text = "package rule\ndefault allow = false"
        interpreter = OpaServerRegoInterpreter(rego_module_text)

        self.assertEqual(interpreter.policy, rego_module_text)
        self.assertTrue(interpreter.package_name.startswith("secobserve_rule_"))
        self.assertEqual(interpreter.policy_id, interpreter.package_name)

        mock_requests.put.assert_called_once()
        args, kwargs = mock_requests.put.call_args
        self.assertEqual(args[0], f"http://localhost:8181/v1/policies/{interpreter.policy_id}")
        # the original 'package rule' is rewritten to the unique package name
        uploaded = kwargs["data"].decode("utf-8")
        self.assertIn(f"package {interpreter.package_name}", uploaded)
        self.assertNotIn("package rule\n", uploaded)
        self.assertIn("default allow = false", uploaded)
        self.assertEqual(kwargs["headers"], {"Content-Type": "text/plain"})
        self.assertEqual(kwargs["timeout"], 60)

    @override_settings(OPA_SERVER_URL="http://opa.internal:8181/")
    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_strips_trailing_slash(self, mock_requests):
        mock_requests.put.return_value = _ok_put()

        interpreter = OpaServerRegoInterpreter("package rule")

        args, _ = mock_requests.put.call_args
        self.assertEqual(args[0], f"http://opa.internal:8181/v1/policies/{interpreter.policy_id}")

    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_package_name_is_stable_per_module(self, mock_requests):
        mock_requests.put.return_value = _ok_put()

        module = "package rule\ndefault allow = false"
        first = OpaServerRegoInterpreter(module).package_name
        second = OpaServerRegoInterpreter(module).package_name
        third = OpaServerRegoInterpreter("package rule\ndefault allow = true").package_name

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_no_package_declaration(self, mock_requests):
        with self.assertRaises(RegoException) as context:
            OpaServerRegoInterpreter("default allow = false")

        self.assertIn("rego module has no 'package' declaration", str(context.exception))
        mock_requests.put.assert_not_called()

    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_build_error(self, mock_requests):
        mock_requests.RequestException = requests.RequestException
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {
            "message": "error(s) occurred while compiling module(s)",
            "errors": [{"message": "rego_parse_error: unexpected eof"}],
        }
        mock_requests.put.return_value = response

        with self.assertRaises(RegoException) as context:
            OpaServerRegoInterpreter("package rule\nbroken")

        self.assertIn("Error while building rego bundle", str(context.exception))
        self.assertIn("rego_parse_error", str(context.exception))

    @patch("application.rules.services.rego_interpreter.requests")
    def test_init_connection_error(self, mock_requests):
        mock_requests.RequestException = requests.RequestException
        mock_requests.put.side_effect = requests.ConnectionError("connection refused")

        with self.assertRaises(RegoException) as context:
            OpaServerRegoInterpreter("package rule")

        self.assertIn("Error while connecting to OPA server", str(context.exception))
        self.assertIn("connection refused", str(context.exception))


@override_settings(OPA_SERVER_URL="http://localhost:8181", OPA_SERVER_TIMEOUT=60)
class TestOpaServerRegoInterpreterQuery(SimpleTestCase):

    @patch("application.rules.services.rego_interpreter.requests")
    def setUp(self, mock_requests):
        mock_requests.put.return_value = _ok_put()
        self.interpreter = OpaServerRegoInterpreter("package rule")
        self.package_name = self.interpreter.package_name

    def _response(self, status_code=200, json_body=None, json_error=None):
        response = MagicMock()
        response.status_code = status_code
        if json_error is not None:
            response.json.side_effect = json_error
        else:
            response.json.return_value = json_body
        return response

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_success(self, mock_requests):
        expected_result = {"severity": "High", "status": "Open"}
        mock_requests.post.return_value = self._response(json_body={"result": expected_result})

        data = {"title": "test"}
        result = self.interpreter.query(data)

        self.assertEqual(result, expected_result)
        mock_requests.post.assert_called_once()
        args, kwargs = mock_requests.post.call_args
        self.assertEqual(args[0], f"http://localhost:8181/v1/data/{self.package_name}")
        self.assertEqual(kwargs["json"], {"input": data})
        self.assertEqual(kwargs["timeout"], 60)

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_with_none_data(self, mock_requests):
        expected_result = {"severity": "Low"}
        mock_requests.post.return_value = self._response(json_body={"result": expected_result})

        result = self.interpreter.query()

        self.assertEqual(result, expected_result)
        _, kwargs = mock_requests.post.call_args
        self.assertEqual(kwargs["json"], {"input": None})

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_no_result_key(self, mock_requests):
        # OPA returns {} when the queried path is undefined
        mock_requests.post.return_value = self._response(json_body={})

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(str(context.exception), "[ErrorDetail(string='Rego output has no results', code='invalid')]")

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_result_none(self, mock_requests):
        mock_requests.post.return_value = self._response(json_body={"result": None})

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertEqual(str(context.exception), "[ErrorDetail(string='Rego output has no results', code='invalid')]")

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_empty_result_object(self, mock_requests):
        # A package that matched no rules yields an empty object, not an error
        mock_requests.post.return_value = self._response(json_body={"result": {}})

        result = self.interpreter.query({"title": "test"})

        self.assertEqual(result, {})

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_http_error(self, mock_requests):
        mock_requests.RequestException = requests.RequestException
        mock_requests.post.return_value = self._response(status_code=500, json_body={"message": "evaluation error"})

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertIn("Error while querying rego module", str(context.exception))
        self.assertIn("evaluation error", str(context.exception))

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_invalid_json(self, mock_requests):
        mock_requests.RequestException = requests.RequestException
        mock_requests.post.return_value = self._response(json_error=ValueError("no json"))

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertIn("invalid OPA response", str(context.exception))

    @patch("application.rules.services.rego_interpreter.requests")
    def test_query_request_error(self, mock_requests):
        mock_requests.RequestException = requests.RequestException
        mock_requests.post.side_effect = requests.Timeout("timed out")

        with self.assertRaises(RegoException) as context:
            self.interpreter.query({"title": "test"})

        self.assertIn("Error while querying rego module", str(context.exception))
        self.assertIn("timed out", str(context.exception))
