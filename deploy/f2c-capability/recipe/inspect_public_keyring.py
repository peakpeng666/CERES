#!/usr/bin/env python3
"""Bounded DATA inspection of one frozen official Ubuntu public-keyring profile.

No subprocess, native verifier, trust database, import, network or file mutation.
This is not signature verification, revocation checking, or a general OpenPGP API.
The parser deliberately rejects unneeded formats instead of accepting extensions.
"""
import datetime
import hashlib
import json
from pathlib import Path
import sys

MAX_BYTES = 1048576
MAX_PACKETS = 64
MAX_PACKET_BODY = 65535
VENDOR_SIZE = 3607
VENDOR_SHA256 = "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31"
SELECTED_SIGNER = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
EXPECTED_KEYS = (
    ("790BC7277767219C42C86F933B4FE6ACC0B21F32", "Ubuntu Archive Automatic Signing Key (2012) <ftpmaster@ubuntu.com>"),
    ("843938DF228D22F7B3742BC0D94AA3F0EFE21092", "Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>"),
    (SELECTED_SIGNER, "Ubuntu Archive Automatic Signing Key (2018) <ftpmaster@ubuntu.com>"),
)
TAG_NAMES = {2: "Signature", 6: "Public-Key", 12: "Trust", 13: "User-ID"}
EXPECTED_TAGS = [6, 12, 13, 12, 2, 12] * 3
TRUST_PROFILE = (
    "000067706701000000000000", "000067706702000000000000", "000367706700"
)


class InspectionError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InspectionError(message)


def take(data, pos, size):
    require(isinstance(size, int) and size >= 0, "invalid field size")
    require(0 <= pos <= len(data) and size <= len(data) - pos, "truncated field")
    return data[pos:pos + size], pos + size


def definite_length(data, pos, *, subpacket=False):
    b, pos = take(data, pos, 1)
    first = b[0]
    if first < 192:
        return first, pos
    if first < 224:
        b, pos = take(data, pos, 1)
        return ((first - 192) << 8) + b[0] + 192, pos
    if first == 255:
        b, pos = take(data, pos, 4)
        return int.from_bytes(b, "big"), pos
    raise InspectionError("unsupported partial/non-profile length")


def packet_stream(data):
    require(type(data) is bytes, "input must be bytes")
    require(0 < len(data) <= MAX_BYTES, "empty or oversized input")
    out, pos = [], 0
    while pos < len(data):
        require(len(out) < MAX_PACKETS, "too many packets")
        start = pos
        h, pos = take(data, pos, 1)
        header = h[0]
        require(header & 0x80, "invalid packet header")
        if header & 0x40:
            tag = header & 0x3f
            size, pos = definite_length(data, pos)
            form = "new"
        else:
            tag, kind = (header >> 2) & 0x0f, header & 3
            require(kind != 3, "indeterminate old packet length")
            b, pos = take(data, pos, (1, 2, 4)[kind])
            size, form = int.from_bytes(b, "big"), "old"
        require(tag not in (5, 7), "secret-key material forbidden")
        require(tag in TAG_NAMES, "unexpected packet tag")
        require(0 < size <= MAX_PACKET_BODY, "empty or oversized packet")
        body_start = pos
        body, pos = take(data, pos, size)
        out.append({"offset": start, "body_offset": body_start,
                    "body_size": size, "total_size": pos - start,
                    "header_format": form, "tag": tag,
                    "name": TAG_NAMES[tag], "body": body})
    require(pos == len(data), "unconsumed trailing content")
    return out


def mpi(data, pos):
    b, pos = take(data, pos, 2)
    bits = int.from_bytes(b, "big")
    require(0 < bits <= 8192, "unsupported MPI size")
    raw, pos = take(data, pos, (bits + 7) // 8)
    n = int.from_bytes(raw, "big")
    require(n.bit_length() == bits, "noncanonical MPI")
    return n, bits, pos


def public_key(body):
    fixed, pos = take(body, 0, 6)
    require(fixed[0] == 4, "only v4 keys supported")
    require(fixed[5] == 1, "only profile RSA public keys supported")
    modulus, bits, pos = mpi(body, pos)
    exponent, exponent_bits, pos = mpi(body, pos)
    require(pos == len(body), "trailing public-key data")
    require(bits == 4096 and modulus & 1, "unexpected RSA modulus")
    require(exponent == 65537 and exponent_bits == 17, "unexpected RSA exponent")
    fingerprint = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
    created = int.from_bytes(fixed[1:5], "big")
    return {"version": 4, "algorithm": "RSA", "algorithm_id": 1,
            "modulus_bits": bits, "exponent": exponent,
            "creation_unix": created,
            "created_utc": datetime.datetime.fromtimestamp(created, datetime.timezone.utc).isoformat(),
            "fingerprint_v4_sha1": fingerprint, "key_id": fingerprint[-16:],
            "public_key_body_sha256": hashlib.sha256(body).hexdigest()}


def signature_subpackets(data):
    out, pos = [], 0
    while pos < len(data):
        require(len(out) < 32, "too many signature subpackets")
        length, pos = definite_length(data, pos, subpacket=True)
        require(1 <= length <= 1024, "invalid signature subpacket size")
        body, pos = take(data, pos, length)
        require(body[0] & 128 == 0, "unsupported critical signature subpacket")
        tag, value = body[0], body[1:]
        require(tag in (2, 11, 16, 21, 22, 23, 27, 30), "unexpected signature subpacket")
        if tag == 2:
            require(len(value) == 4, "invalid creation-time subpacket")
        elif tag == 16:
            require(len(value) == 8, "invalid issuer subpacket")
        elif tag in (23, 27, 30):
            require(len(value) == 1, "invalid one-byte subpacket")
        else:
            require(1 <= len(value) <= 16, "invalid preference subpacket")
        out.append({"type": tag, "critical": False, "value_hex": value.hex()})
    return out


def signature(body, key):
    fixed, pos = take(body, 0, 4)
    require(fixed[:3] == b"\x04\x13\x01", "unexpected signature version/type/algorithm")
    require(fixed[3] in (2, 10), "unexpected signature hash algorithm")
    b, pos = take(body, pos, 2)
    hashed, pos = take(body, pos, int.from_bytes(b, "big"))
    b, pos = take(body, pos, 2)
    unhashed, pos = take(body, pos, int.from_bytes(b, "big"))
    hash_prefix, pos = take(body, pos, 2)
    _, sig_bits, pos = mpi(body, pos)
    require(pos == len(body), "trailing signature data")
    require(sig_bits <= 4096, "oversized RSA signature MPI")
    h, u = signature_subpackets(hashed), signature_subpackets(unhashed)
    require([x["type"] for x in h] == [2, 27, 11, 21, 22, 30, 23], "unexpected hashed signature profile")
    require([x["type"] for x in u] == [16], "unexpected unhashed signature profile")
    require(h[0]["value_hex"] == key["creation_unix"].to_bytes(4, "big").hex(), "signature/key creation mismatch")
    require(u[0]["value_hex"].upper() == key["key_id"], "issuer metadata mismatch")
    return {"version": 4, "signature_type": 19, "public_key_algorithm": 1,
            "hash_algorithm": fixed[3], "hash_prefix_hex": hash_prefix.hex(),
            "signature_mpi_bits": sig_bits,
            "hashed_subpackets": h, "unhashed_subpackets": u,
            "cryptographic_signature_verified": False}


def inspect_vendor_keyring(data, *, require_exact_digest=True):
    packets = packet_stream(data)
    require([p["tag"] for p in packets] == EXPECTED_TAGS, "unexpected vendor packet layout")
    keys, inventory = [], []
    for i, p in enumerate(packets):
        entry = {k: v for k, v in p.items() if k != "body"}
        body, within = p["body"], i % 6
        entry["body_sha256"] = hashlib.sha256(body).hexdigest()
        if p["tag"] == 6:
            key = public_key(body)
            require(key["fingerprint_v4_sha1"] == EXPECTED_KEYS[i // 6][0], "unexpected public-key fingerprint")
            key["packet_offset"] = p["offset"]
            key["accepted_signer"] = key["fingerprint_v4_sha1"] == SELECTED_SIGNER
            keys.append(key)
            entry["public_key"] = dict(key)
        elif p["tag"] == 13:
            try:
                uid = body.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise InspectionError("invalid UTF-8 user ID") from exc
            require(uid == EXPECTED_KEYS[i // 6][1], "unexpected user ID")
            entry["user_id"] = uid
            keys[-1]["user_id"] = uid
        elif p["tag"] == 12:
            expected = TRUST_PROFILE[{1: 0, 3: 1, 5: 2}[within]]
            require(body.hex() == expected, "unexpected vendor trust-packet bytes")
            entry["body_hex"] = body.hex()
            entry["trust_interpreted_or_accepted"] = False
        elif p["tag"] == 2:
            entry["signature"] = signature(body, keys[-1])
        inventory.append(entry)
    digest = hashlib.sha256(data).hexdigest()
    if require_exact_digest:
        require(len(data) == VENDOR_SIZE and digest == VENDOR_SHA256, "frozen vendor bytes mismatch")
    return {"inspection": "PASS", "kind": "bounded public-data structure and fingerprint inspection only",
            "size_bytes": len(data), "sha256": digest,
            "exact_frozen_vendor_bytes_required": require_exact_digest,
            "packet_count": len(packets),
            "tag_counts": {str(tag): sum(p["tag"] == tag for p in packets) for tag in sorted(TAG_NAMES)},
            "secret_key_material_present": False,
            "selected_exact_signing_fingerprint": SELECTED_SIGNER,
            "accepted_signer_count": sum(k["accepted_signer"] for k in keys),
            "keys": keys, "packets": inventory,
            "limits": ["No signature, self-signature, revocation, freshness, or native-verifier check was performed.",
                       "V4 fingerprints use OpenPGP SHA-1 framing; the full vendor file is pinned with SHA-256.",
                       "Vendor tag-12 trust packets are inventoried and byte-matched, not treated as authority.",
                       "Only the selected exact signer is in policy; other keys are outside policy."]}


def main(argv):
    require(len(argv) == 2, "usage: inspect_public_keyring.py FILE")
    with Path(argv[1]).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    result = inspect_vendor_keyring(data)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv)
