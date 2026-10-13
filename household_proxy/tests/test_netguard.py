"""Where the proxy may connect (app/netguard.py, spec/SPEC.md §4.5): target parsing, address kinds, the check at
connect time, and the trusted-certificate TLS context."""
import _env  # noqa: F401  (must be first)

import asyncio
import ipaddress
import ssl
import unittest
from unittest import mock

import httpcore

from app import netguard


class Targets(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(netguard.parse_target("http://192.168.1.10:9120"), ("http", "192.168.1.10", 9120))
        self.assertEqual(netguard.parse_target("https://NAS.lan/"), ("https", "nas.lan", 443))
        self.assertEqual(netguard.parse_target("http://[fd00::1]:8080"), ("http", "fd00::1", 8080))
        for bad in ("ftp://x", "192.168.1.10", "http://", "http://u:p@x", "http://x/path", "http://x?q=1",
                    "http://x:99999", "javascript:alert(1)", "http://x#f"):
            with self.assertRaises(netguard.TargetError, msg=bad):
                netguard.parse_target(bad)

    def test_origin(self):
        self.assertEqual(netguard.origin("http", "x", 80), "http://x")
        self.assertEqual(netguard.origin("https", "x", 8443), "https://x:8443")
        self.assertEqual(netguard.origin("http", "fd00::1", 81), "http://[fd00::1]:81")

    def test_address_kinds(self):
        k = lambda a: netguard.address_kind(ipaddress.ip_address(a))     # noqa: E731
        for a in ("192.168.1.10", "10.0.0.5", "172.30.32.2", "169.254.1.2", "fd00::1", "fe80::1", "100.64.1.1"):
            self.assertEqual(k(a), "local", a)
        for a in ("8.8.8.8", "2606:4700::1111"):
            self.assertEqual(k(a), "public", a)
        with mock.patch.object(netguard, "ALLOW_LOOPBACK", False):
            for a in ("127.0.0.1", "::1", "0.0.0.0", "::", "224.0.0.1", "240.0.0.1", "::ffff:127.0.0.1"):
                self.assertEqual(k(a), "never", a)

    def test_saved_check(self):
        with mock.patch.object(netguard, "ALLOW_LOOPBACK", False):
            for host in ("127.0.0.1", "localhost", "app.localhost", "0.0.0.0"):
                with self.assertRaises(netguard.TargetError, msg=host):
                    netguard.check_saved(host, True)
        with self.assertRaises(netguard.TargetError):
            netguard.check_saved("8.8.8.8", False)
        netguard.check_saved("8.8.8.8", True)
        netguard.check_saved("nas.lan", False)          # judged when connecting


class Resolve(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def fake_dns(self, *addrs):
        async def getaddrinfo(host, port, **kw):
            return [(2, 1, 6, "", (a, port)) for a in addrs]
        return getaddrinfo

    def resolve(self, host, addrs, allow_public=False):
        async def go():
            with mock.patch.object(netguard, "_lookup", self.fake_dns(*addrs)):
                return await netguard.resolve(host, 80, allow_public)
        return self.run_(go())

    def test_name_to_local_address(self):
        self.assertEqual(self.resolve("nas.lan", ["192.168.1.20"]), ["192.168.1.20"])

    def test_name_to_public_address_needs_permission(self):
        with self.assertRaises(netguard.TargetError):
            self.resolve("nas.lan", ["93.184.216.34"])
        self.assertEqual(self.resolve("nas.lan", ["93.184.216.34"], True), ["93.184.216.34"])

    def test_rebinding_to_loopback_refused(self):
        with mock.patch.object(netguard, "ALLOW_LOOPBACK", False):
            with self.assertRaises(netguard.TargetError):
                self.resolve("evil.lan", ["127.0.0.1"], True)
            self.assertEqual(self.resolve("mixed.lan", ["127.0.0.1", "192.168.1.5"]), ["192.168.1.5"])

    def test_backend_connects_to_the_checked_address(self):
        backend = netguard.GuardedBackend(False)
        seen = []

        async def fake_connect(addr, port, **kw):
            seen.append(addr)
            return "stream"

        async def go():
            with mock.patch.object(netguard, "_lookup", self.fake_dns("192.168.1.30")), \
                    mock.patch.object(backend._inner, "connect_tcp", fake_connect):
                return await backend.connect_tcp(b"nas.lan", 443)
        self.assertEqual(self.run_(go()), "stream")
        self.assertEqual(seen, ["192.168.1.30"])

    def test_backend_refuses_public(self):
        backend = netguard.GuardedBackend(False)

        async def go():
            with mock.patch.object(netguard, "_lookup", self.fake_dns("8.8.8.8")):
                await backend.connect_tcp("dns.example", 80)
        with self.assertRaises(httpcore.ConnectError):
            self.run_(go())
        with self.assertRaises(httpcore.ConnectError):
            self.run_(backend.connect_unix_socket("/var/run/docker.sock"))


class Tls(unittest.TestCase):
    def test_default_verifies(self):
        ctx = netguard.ssl_context(None)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_trusted_certificate_context(self):
        pem = _self_signed()
        ctx = netguard.ssl_context(pem)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.cert_store_stats()["x509"], 1)     # that one certificate, no public authorities


class Faster(unittest.TestCase):
    def test_context_made_once_per_certificate(self):
        self.assertIs(netguard.ssl_context(None), netguard.ssl_context(None))
        pem = _self_signed()
        self.assertIs(netguard.ssl_context(pem), netguard.ssl_context(pem))
        self.assertIsNot(netguard.ssl_context(pem), netguard.ssl_context(None))

    def test_a_name_not_found_is_remembered_for_a_few_seconds(self):
        import socket
        calls = []

        async def missing(host, port):
            calls.append(host)
            raise socket.gaierror("not known")
        netguard._not_found.clear()
        try:
            with mock.patch.object(netguard, "_lookup", missing):
                for _ in range(3):
                    with self.assertRaises(netguard.TargetError):
                        asyncio.run(netguard.resolve("gone.lan", 80, False))
            self.assertEqual(calls, ["gone.lan"])
            with mock.patch.object(netguard.time, "monotonic", lambda: 1e12), \
                    mock.patch.object(netguard, "_lookup", missing):
                with self.assertRaises(netguard.TargetError):
                    asyncio.run(netguard.resolve("gone.lan", 80, False))
            self.assertEqual(len(calls), 2)                      # asked again once the few seconds are over
        finally:
            netguard._not_found.clear()


class Warnings(unittest.TestCase):
    def test_home_assistants_network_and_link_local(self):
        for host in ("172.30.32.1", "172.30.33.9", "supervisor", "homeassistant"):
            self.assertIn("Home Assistant's own internal network", netguard.target_warning(host), host)
        for host in ("169.254.1.2", "fe80::1"):
            self.assertIn("link-local", netguard.target_warning(host), host)
        for host in ("192.168.1.10", "nas.lan", "172.30.34.1", ""):
            self.assertIsNone(netguard.target_warning(host), host)


def _self_signed() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    import datetime
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nas.lan")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


if __name__ == "__main__":
    unittest.main()
