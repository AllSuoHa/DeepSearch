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
from deepsearch.infrastructure.customer_service import (
    CustomerServicePublisher,
    DeliveryArtifact,
    DeliveryError,
)
from deepsearch.infrastructure.storage import ConversationStore
from deepsearch.presentation.web.delivery import (
    DATA_CLASSIFICATION_OPTIONS,
    build_library_delivery_artifact,
    conversation_delivery_external_id,
    data_classification_label,
    data_classification_value,
)


class CustomerServicePublisherTests(unittest.TestCase):
    def test_data_classification_uses_chinese_labels_without_changing_contract_values(self):
        self.assertEqual(DATA_CLASSIFICATION_OPTIONS, ("公开", "内部", "机密"))
        self.assertEqual(data_classification_value("公开"), "public")
        self.assertEqual(data_classification_value("内部"), "internal")
        self.assertEqual(data_classification_value("机密"), "confidential")
        self.assertEqual(data_classification_label("confidential"), "机密")

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
            captured = {"requests": []}

            def transport(request, timeout):
                captured["requests"].append((request.get_method(), request.full_url))
                captured["headers"] = dict(request.header_items())
                captured["timeout"] = timeout
                if request.get_method() == "POST":
                    captured["payload"] = json.loads(request.data.decode("utf-8"))
                    return 202, b'{"job_id":"ingest_41","status":"queued"}'
                return 200, b'{"external_id":"scheduled","versions":[{"job_id":"ingest_41","status":"succeeded","document_id":"doc_41"}]}'

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
            self.assertEqual(outcome.job_id, "ingest_41")
            self.assertEqual(outcome.document_id, "doc_41")
            self.assertTrue(captured["requests"][0][1].endswith("/api/v2/integrations/deepsearch/documents"))
            self.assertIn("/api/v2/integrations/deepsearch/documents/scheduled-", captured["requests"][1][1])
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

            def succeeding_transport(request, timeout):
                if request.get_method() == "POST":
                    return 202, b'{"job_id":"ingest_9","status":"queued"}'
                return 200, b'{"versions":[{"job_id":"ingest_9","status":"succeeded","document_id":"doc_9"}]}'

            succeeding = CustomerServicePublisher(
                settings, transport=succeeding_transport, sleeper=lambda _: None
            )
            retried = succeeding.flush_pending(force=True)
            self.assertEqual(
                [(item.status, item.document_id) for item in retried],
                [("indexed", "doc_9")],
            )
            self.assertEqual(list(settings.outbox_dir.glob("*.json")), [])

    def test_same_task_keeps_stable_external_id_across_report_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            external_ids = []

            def transport(request, timeout):
                if request.get_method() == "POST":
                    external_ids.append(json.loads(request.data)["external_id"])
                    return 202, b'{"job_id":"ingest_1","status":"queued"}'
                return 200, b'{"versions":[{"job_id":"ingest_1","status":"succeeded","document_id":"doc_1"}]}'

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
            self.assertIn("拒绝了联动密钥", record_text)

    def test_search_artifact_uses_the_same_publish_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "search.md"
            snapshot.write_text("# Search\n\n[Official](https://example.org)", encoding="utf-8")
            captured = {}

            def transport(request, timeout):
                if request.get_method() == "POST":
                    captured.update(json.loads(request.data.decode("utf-8")))
                    return 202, b'{"job_id":"ingest_7","status":"queued"}'
                return 200, b'{"versions":[{"job_id":"ingest_7","status":"succeeded","document_id":"doc_7"}]}'

            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                outbox_dir=Path(directory) / "outbox",
            )
            outcome = CustomerServicePublisher(settings, transport=transport).publish_artifact(
                DeliveryArtifact("conversation-search-1", "Search", snapshot, kind="search")
            )

            self.assertEqual(outcome.status, "indexed")
            self.assertEqual(captured["metadata"]["kind"], "search")

    def test_v2_background_failure_is_not_reported_as_indexed(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "search.md"
            snapshot.write_text("# Search", encoding="utf-8")

            def transport(request, timeout):
                if request.get_method() == "POST":
                    return 202, b'{"job_id":"ingest_failed","status":"queued"}'
                return 200, b'{"versions":[{"job_id":"ingest_failed","status":"failed","document_id":null}]}'

            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                retry_attempts=1,
                outbox_dir=Path(directory) / "outbox",
            )
            outcome = CustomerServicePublisher(settings, transport=transport).publish_artifact(
                DeliveryArtifact("conversation-search-1", "Search", snapshot, kind="search")
            )

            self.assertEqual(outcome.status, "queued")
            self.assertIn("后台入库失败", outcome.error)
            self.assertEqual(len(list(settings.outbox_dir.glob("*.json"))), 1)

    def test_v2_response_without_job_id_is_a_visible_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "search.md"
            snapshot.write_text("# Search", encoding="utf-8")
            settings = CustomerServiceSettings(
                enabled=True,
                integration_key="integration-secret",
                retry_attempts=1,
                outbox_dir=Path(directory) / "outbox",
            )

            outcome = CustomerServicePublisher(
                settings,
                transport=lambda request, timeout: (202, b'{"status":"queued"}'),
            ).publish_artifact(DeliveryArtifact("conversation-search-1", "Search", snapshot))

            self.assertEqual(outcome.status, "queued")
            self.assertIn("缺少 job_id", outcome.error)

    def test_disabled_integration_does_not_create_an_outbox_record(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "search.md"
            snapshot.write_text("result", encoding="utf-8")
            settings = CustomerServiceSettings(enabled=False, outbox_dir=Path(directory) / "outbox")

            with self.assertRaises(DeliveryError):
                CustomerServicePublisher(settings).publish_artifact(
                    DeliveryArtifact("conversation-search-1", "Search", snapshot, kind="search")
                )
            self.assertFalse(settings.outbox_dir.exists())

    def test_library_delivery_reuses_linked_conversation_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "reports" / "answer.md"
            report_path.parent.mkdir()
            report_path.write_text("# Answer\n\nBody", encoding="utf-8")
            store = ConversationStore(root / "data" / "conversations")
            conversation = store.create()
            store.append(conversation, {
                "role": "assistant",
                "content": "Body",
                "mode": "research",
                "kind": "research",
                "question": "Original question",
                "artifact_path": str(report_path),
            })

            artifact = build_library_delivery_artifact(
                {
                    "path": str(report_path),
                    "title": "Answer",
                    "kind": "研究报告",
                    "format": "md",
                },
                store.directory,
                "confidential",
            )

            self.assertEqual(
                artifact.external_id,
                conversation_delivery_external_id(
                    conversation["id"], "research", "Original question"
                ),
            )
            self.assertEqual(artifact.question, "Original question")
            self.assertEqual(artifact.data_classification, "confidential")
            self.assertEqual(artifact.metadata["source"], "library")


if __name__ == "__main__":
    unittest.main()
