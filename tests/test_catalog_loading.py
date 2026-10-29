"""Automatic discovery keeps transport fallback separate from authentication."""
from urllib.error import URLError
import unittest
from unittest.mock import patch

from protocol.fetch import load_index
from protocol.manifest import ProtocolError


class CatalogLoadingTests(unittest.TestCase):
    def load(self):
        return load_index("https://registry/index", "https://registry/sig", "cache", public_key="public.pem")

    @patch("protocol.fetch.cached_index")
    @patch("protocol.fetch.fetch_index")
    def test_online_does_not_read_stale_cache(self, fetch, cached):
        fetch.return_value = {"index": {"sequence": 3}, "sha256": "a" * 64}
        result = self.load()
        self.assertFalse(result["offline"])
        self.assertEqual(result["index"]["sequence"], 3)
        self.assertEqual(fetch.call_args.kwargs["timeout"], 12)
        cached.assert_not_called()

    @patch("protocol.fetch.cached_index")
    @patch("protocol.fetch.fetch_index", side_effect=URLError("offline"))
    def test_transport_failure_reverifies_cache(self, fetch, cached):
        cached.return_value = {"sequence": 2}
        self.assertTrue(self.load()["offline"])
        cached.assert_called_once_with("cache", public_key="public.pem", openssl="openssl")

    @patch("protocol.fetch.cached_index")
    @patch("protocol.fetch.fetch_index", side_effect=TimeoutError("timeout"))
    def test_timeout_reverifies_cache(self, fetch, cached):
        cached.return_value = {"sequence": 2}
        self.assertTrue(self.load()["offline"])

    @patch("protocol.fetch.cached_index")
    @patch("protocol.fetch.fetch_index")
    def test_authentication_and_rollback_errors_never_fall_back(self, fetch, cached):
        for error in ("Index signature rejected", "Signed index rollback", "Invalid manifest"):
            fetch.side_effect = ProtocolError(error)
            with self.assertRaisesRegex(ProtocolError, error):
                self.load()
        cached.assert_not_called()

    @patch("protocol.fetch.cached_index", side_effect=ProtocolError("Index signature rejected"))
    @patch("protocol.fetch.fetch_index", side_effect=URLError("offline"))
    def test_modified_cached_signature_still_fails(self, fetch, cached):
        with self.assertRaisesRegex(ProtocolError, "signature rejected"):
            self.load()

    @patch("protocol.fetch.cached_index", side_effect=FileNotFoundError())
    @patch("protocol.fetch.fetch_index", side_effect=URLError("offline"))
    def test_first_offline_run_gives_recovery_options(self, fetch, cached):
        with self.assertRaisesRegex(ProtocolError, "Retry or import a local device package"):
            self.load()


if __name__ == "__main__":
    unittest.main()
