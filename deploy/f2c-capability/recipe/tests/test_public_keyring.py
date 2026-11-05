"""Data-only regression tests. Run with Python unittest, also under -O.

The downloaded fixture must be adjacent, or in sibling inputs/ when this module
is placed in a candidate's recipe/ directory. No downloads or subprocesses.
"""
import hashlib
from pathlib import Path
import unittest

import importlib.util
R=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('packet_inspection_under_test',R/'inspect_public_keyring.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)


def fixture_path():
    return R.parent / 'inputs' / 'ubuntu-archive-keyring.gpg'


def packet(tag, body):
    return bytes([0xc0 | tag, 255]) + len(body).to_bytes(4, "big") + body


class PublicKeyringInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture_path().read_bytes()
        cls.packets = subject.packet_stream(cls.data)

    def reject(self, data, exact=False):
        with self.assertRaises(subject.InspectionError):
            subject.inspect_vendor_keyring(data, require_exact_digest=exact)

    def edited_body(self, index, body):
        p = self.packets[index]
        return self.data[:p["offset"]] + packet(p["tag"], body) + self.data[p["offset"] + p["total_size"]:]

    def test_frozen_vendor_passes(self):
        report = subject.inspect_vendor_keyring(self.data)
        self.assertEqual(report["sha256"], subject.VENDOR_SHA256)
        self.assertEqual(report["size_bytes"], 3607)
        self.assertEqual(report["packet_count"], 18)
        self.assertEqual(report["tag_counts"], {"2": 3, "6": 3, "12": 9, "13": 3})
        self.assertEqual(report["accepted_signer_count"], 1)
        self.assertFalse(report["secret_key_material_present"])

    def test_all_primary_fingerprints_match(self):
        expected = [x[0] for x in subject.EXPECTED_KEYS]
        actual = [subject.public_key(p["body"])["fingerprint_v4_sha1"] for p in self.packets if p["tag"] == 6]
        self.assertEqual(actual, expected)

    def test_selected_fingerprint_independent_framing(self):
        body = self.packets[12]["body"]
        framed = bytes((0x99, len(body) >> 8, len(body) & 255)) + body
        self.assertEqual(hashlib.sha1(framed).hexdigest().upper(), subject.SELECTED_SIGNER)

    def test_all_short_prefixes_rejected(self):
        for length in range(len(self.data)):
            with self.subTest(length=length):
                self.reject(self.data[:length])

    def test_every_single_byte_xor_rejected_with_exact_pin(self):
        for offset in range(len(self.data)):
            with self.subTest(offset=offset):
                mutated = self.data[:offset] + bytes([self.data[offset] ^ 1]) + self.data[offset + 1:]
                self.reject(mutated, exact=True)

    def test_secret_primary_and_subkey_rejected(self):
        for tag in (5, 7):
            with self.subTest(tag=tag):
                self.reject(packet(tag, b"\x04\x01") + self.data)

    def test_all_unexpected_packet_tags_rejected(self):
        for tag in range(64):
            if tag not in subject.TAG_NAMES:
                with self.subTest(tag=tag):
                    self.reject(packet(tag, b"a") + self.data)

    def test_empty_and_oversized_input(self):
        self.reject(b"")
        self.reject(b"a" * (subject.MAX_BYTES + 1))

    def test_nonbytes_input(self):
        for value in ("not bytes", bytearray(self.data), None, 1):
            with self.subTest(kind=type(value).__name__):
                self.reject(value)

    def test_invalid_header(self):
        self.reject(b"\x06\x01a")

    def test_partial_lengths_rejected(self):
        for length in range(224, 255):
            self.reject(bytes([0xc6, length]) + b"a" * 8)

    def test_old_indeterminate_length_rejected(self):
        self.reject(bytes([0x80 | (6 << 2) | 3]) + b"a")

    def test_truncated_length_fields(self):
        for value in (b"\xc6", b"\xc6\xc0", b"\xc6\xff", b"\xc6\xff\x00", b"\xc6\xff\x00\x00\x00", b"\x99", b"\x99\x00"):
            self.reject(value)

    def test_oversized_and_zero_packet(self):
        self.reject(b"\xc6\xff" + (subject.MAX_PACKET_BODY + 1).to_bytes(4, "big"))
        self.reject(b"\xc6\x00")

    def test_extra_duplicate_or_reordered_packets(self):
        self.reject(self.data + packet(12, b"a"))
        self.reject(self.data * 2)
        segments = [self.data[p["offset"]:p["offset"] + p["total_size"]] for p in self.packets]
        segments[1], segments[2] = segments[2], segments[1]
        self.reject(b"".join(segments))

    def test_too_many_packets(self):
        self.reject(packet(12, b"a") * (subject.MAX_PACKETS + 1))

    def test_trailing_junk(self):
        self.reject(self.data + b"junk")

    def test_key_mutation_and_unknown_version_algorithm(self):
        body = self.packets[0]["body"]
        for offset, value in ((0, 5), (5, 17), (9, body[9] ^ 1)):
            self.reject(self.edited_body(0, body[:offset] + bytes([value]) + body[offset + 1:]))
        self.reject(self.edited_body(0, body + b"\x00"))

    def test_noncanonical_mpi(self):
        for raw in (b"\x00\x00", b"\x00\x08\x00", b"\x00\x01\x02", b"\x20\x01"):
            with self.assertRaises(subject.InspectionError):
                subject.mpi(raw, 0)

    def test_wrong_rsa_exponent(self):
        body = self.packets[0]["body"]
        self.reject(self.edited_body(0, body[:-1] + b"\x03"))

    def test_changed_or_non_utf8_user_id(self):
        self.reject(self.edited_body(2, b"Other"))
        self.reject(self.edited_body(2, b"\xff"))

    def test_changed_vendor_trust_bytes(self):
        for i in (1, 3, 5):
            body = self.packets[i]["body"]
            self.reject(self.edited_body(i, body[:-1] + b"\x01"))

    def test_signature_version_type_algorithm_hash_rejected(self):
        body = self.packets[4]["body"]
        for index, value in ((0, 5), (1, 0), (2, 17), (3, 255)):
            self.reject(self.edited_body(4, body[:index] + bytes([value]) + body[index + 1:]))

    def test_signature_trailing_data_rejected(self):
        self.reject(self.edited_body(4, self.packets[4]["body"] + b"a"))

    def test_signature_truncated_hashed_subpacket_area(self):
        body = self.packets[4]["body"]
        self.reject(self.edited_body(4, body[:4] + b"\xff\xff" + body[6:]))

    def test_signature_unexpected_subpacket_rejected(self):
        body = self.packets[4]["body"]
        self.reject(self.edited_body(4, body[:7] + b"\x63" + body[8:]))
        self.reject(self.edited_body(4, body[:7] + b"\x82" + body[8:]))

    def test_signature_wrong_issuer_metadata(self):
        body = self.packets[4]["body"]
        hashed_length = int.from_bytes(body[4:6], "big")
        issuer_pos = 6 + hashed_length + 2 + 2
        self.reject(self.edited_body(4, body[:issuer_pos] + bytes([body[issuer_pos] ^ 1]) + body[issuer_pos + 1:]))

    def test_no_claim_of_crypto_verification(self):
        result = subject.inspect_vendor_keyring(self.data)
        for item in result["packets"]:
            if item["tag"] == 2:
                self.assertIs(item["signature"]["cryptographic_signature_verified"], False)
            if item["tag"] == 12:
                self.assertIs(item["trust_interpreted_or_accepted"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
