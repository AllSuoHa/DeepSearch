import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from deepsearch.infrastructure.config import (
    CustomerServiceSettings,
    Settings,
    load_settings,
    save_settings,
)
from deepsearch.infrastructure.customer_service import CustomerServicePublisher


class CustomerServicePublisherTests(unittest.TestCase):
    def _objects(self, directory: str, content: str = "# Report\n\nVerified answer [1]."):
        report_path = Path(directory) / "report.md"
        report_path.write_text(content, encoding="utf-8")
        task = SimpleNamespace(
            name="AI daily",
            question="What changed?",
            profile="deep",
            data_classification="internal",
        )
        result = SimpleNamespace(
            report_path=report_path,
            question=task.question,
            rounds=3,
            stop_reason="sufficient",
            scorecard=SimpleNamespace(overall=92, grade="A"),
        )
        return task, result

    def test_successful_delivery_uses_both_auth_headers_and_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = {}

            def transport(request, timeout):
                captured["url"] = request.full_url
                captured["headers"] = dict(request.header_items())
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                captured["timeout"] = timeout
                return 200, json.dumps({"document_id": 41, "idempotent_replay": False}).encode()

            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                api_access_key="global-secret",
                outbox_dir=Path(directory) / "outbox",
                retry_backoff_seconds=0,
            )
            publisher = CustomerServicePublisher(settings, transport=transport, sleeper=lambda _: None)
            task, result = self._objects(directory)

            outcome = publisher.publish(task, result)

            self.assertEqual(outcome.status, "indexed")
            self.assertEqual(outcome.document_id, 41)
            self.assertTrue(captured["url"].endswith("/api/v1/integrations/deepsearch/documents"))
            lowered_headers = {key.lower(): value for key, value in captured["headers"].items()}
            self.assertEqual(lowered_headers["x-integration-key"], "integration-secret")
            self.assertEqual(lowered_headers["authorization"], "Bearer global-secret")
            self.assertEqual(captured["payload"]["content_markdown"], "# Report\n\nVerified answer [1].")
            self.assertEqual(captured["payload"]["chunk_strategy"], "markdown")
            self.assertEqual(captured["payload"]["data_classification"], "internal")

    def test_failure_queues_only_path_and_hash_then_flushes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                retry_attempts=1,
                retry_backoff_seconds=0,
                outbox_dir=Path(directory) / "outbox",
            )
            task, result = self._objects(directory, "# Sensitive-looking research body")
            failing = CustomerServicePublisher(
                settings,
                transport=lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
                sleeper=lambda _: None,
            )

            outcome = failing.publish(task, result)

            self.assertEqual(outcome.status, "queued")
            records = list(settings.outbox_dir.glob("*.json"))
            self.assertEqual(len(records), 1)
            record_text = records[0].read_text(encoding="utf-8")
            self.assertNotIn("Sensitive-looking research body", record_text)
            record = json.loads(record_text)
            self.assertEqual(record["report_path"], str(result.report_path.resolve()))
            self.assertIn("content_hash", record)

            succeeding = CustomerServicePublisher(
                settings,
                transport=lambda request, timeout: (200, b'{"document_id": 9, "idempotent_replay": true}'),
                sleeper=lambda _: None,
            )
            retried = succeeding.flush_pending(force=True)
            self.assertEqual([(item.status, item.document_id) for item in retried], [("indexed", 9)])
            self.assertEqual(list(settings.outbox_dir.glob("*.json")), [])

    def test_same_task_keeps_stable_external_id_across_report_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            external_ids = []

            def transport(request, timeout):
                external_ids.append(json.loads(request.data)["external_id"])
                return 200, b'{"document_id": 1}'

            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                outbox_dir=Path(directory) / "outbox",
            )
            publisher = CustomerServicePublisher(settings, transport=transport, sleeper=lambda _: None)
            task, first = self._objects(directory, "version one")
            publisher.publish(task, first)
            task, second = self._objects(directory, "version two")
            publisher.publish(task, second)

            self.assertEqual(len(external_ids), 2)
            self.assertEqual(external_ids[0], external_ids[1])

    def test_credentials_are_loaded_from_environment_but_never_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({"customer_service": {"enabled": True, "base_url": "http://localhost:8000"}}),
                encoding="utf-8",
            )
            environment = {
                "DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY": "integration-secret",
                "DEEPSEARCH_CUSTOMER_SERVICE_API_KEY": "global-secret",
            }
            with patch.dict("os.environ", environment, clear=False):
                settings = load_settings(config_path)
            self.assertEqual(settings.customer_service.integration_key, "integration-secret")
            self.assertEqual(settings.customer_service.api_access_key, "global-secret")

            save_settings(settings)
            saved = config_path.read_text(encoding="utf-8")
            self.assertNotIn("integration-secret", saved)
            self.assertNotIn("global-secret", saved)
            self.assertEqual(json.loads(saved)["customer_service"]["integration_key"], "")

    def test_server_error_cannot_echo_credentials_into_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                api_access_key="global-secret",
                retry_attempts=1,
                outbox_dir=Path(directory) / "outbox",
            )
            publisher = CustomerServicePublisher(
                settings,
                transport=lambda request, timeout: (
                    401,
                    b'{"detail":"integration-secret global-secret"}',
                ),
                sleeper=lambda _: None,
            )
            task, result = self._objects(directory)

            outcome = publisher.publish(task, result)
            record_text = next(settings.outbox_dir.glob("*.json")).read_text(encoding="utf-8")

            self.assertEqual(outcome.status, "queued")
            self.assertNotIn("integration-secret", record_text)
            self.assertNotIn("global-secret", record_text)
            self.assertIn("[REDACTED]", record_text)


if __name__ == "__main__":
    unittest.main()
